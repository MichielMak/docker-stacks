#!/usr/bin/env python3
"""Move Komodo stack secrets into SOPS files and export Komodo resources as TOML.

Phase 3 of docs/plans/komodo-iac-sops.md. This script never prints secret
values, only key names. Plaintext secrets only ever go to `sops` on stdin.

Modes (pick one):
  --discover       Default. Read-only. Prints each stack's source, paths and env
                   key names with their classification, the Komodo Variable
                   names, and resource counts. Safe to share.
  --write-secrets  Writes <stack>/secrets.sops.env for every stack that has
                   secrets. Needs `sops` (3.9+) on PATH and an admin API key,
                   because Komodo hides secret Variable values from non-admins.
  --write-toml     Writes komodo/resources/*.toml with all secret values removed.

Environment:
  KOMODO_URL, KOMODO_API_KEY, KOMODO_API_SECRET
  Any that aren't set are asked for on the terminal (key and secret hidden).

Classification overrides live in scripts/secret-classification.yaml (names only).

Needs Python 3.11+ (tomllib). Standard library only.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit("komodo-migrate needs Python 3.11 or newer (for tomllib)")

import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SECRETS_FILE = "secrets.sops.env"
WRAPPER = f"sops exec-env {SECRETS_FILE} '[[COMPOSE_COMMAND]]'"
# Never add "config": Komodo 2.3.x logs the resolved compose config unredacted
# when the wrapper applies to it (moghtech/komodo#1636).
WRAPPER_INCLUDE = ["up", "pull", "build", "run"]
# komodo: deploys periphery itself, so its secrets stay out of SOPS for now (plan Phase 6).
# stash: files_on_host outside this repo, so there's nowhere to put its secrets file.
DEFAULT_EXCLUDE = ["komodo", "stash"]

EXPORT_PARAMS = {
    "include_resources": True,
    "tags": [],
    "include_variables": False,
    "include_user_groups": False,
}

# Top-level keys of an exported Resource Sync TOML, and the file each goes to.
RESOURCE_FILES = {
    "server": "servers.toml",
    "swarm": "swarms.toml",
    "stack": "stacks.toml",
    "deployment": "deployments.toml",
    "build": "builds.toml",
    "repo": "repos.toml",
    "procedure": "procedures.toml",
    "action": "actions.toml",
    "alerter": "alerters.toml",
    "builder": "builders.toml",
    "resource_sync": "syncs.toml",
    "variable": "variables.toml",
}

SECRET_NAME_RE = re.compile(
    r"pass|pw|secret|token|key|api|arl|auth|cookie|jwt|claim|oauth|credential"
    r"|account_?id|tunnel_?id|endpoint|private|salt|dsn|webhook",
    re.IGNORECASE,
)
REF_RE = re.compile(r"\[\[([A-Za-z0-9_.\-]+)\]\]")
PURE_REF_RE = re.compile(r"""^(["']?)\[\[[A-Za-z0-9_.\-]+\]\]\1$""")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# ${VAR}, ${VAR:-default}, $VAR, but not $$VAR (escaped)
COMPOSE_VAR_RE = re.compile(
    r"(?<!\$)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
)
QUOTES = ('"', "'")
# Secret values shorter than this aren't used by the TOML leak check,
# they'd match too much ordinary text.
MIN_LEAK_CHECK_LEN = 8


class KomodoError(Exception):
    pass


class SopsError(Exception):
    pass


# --- Komodo API ---------------------------------------------------------------


class Komodo:
    """Minimal read-only client: POST {url}/read {"type": ..., "params": ...}."""

    def __init__(self, url: str, key: str, secret: str, insecure: bool = False):
        self.url = url.rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
            "X-Api-Key": key,
            "X-Api-Secret": secret,
        }
        self.context = ssl._create_unverified_context() if insecure else None

    @classmethod
    def from_env(cls, insecure: bool = False) -> Komodo:
        """Settings from the environment, or asked for on the terminal
        (key and secret without echo, so they stay out of shell history)."""
        return cls(
            _setting("KOMODO_URL", hidden=False),
            _setting("KOMODO_API_KEY", hidden=True),
            _setting("KOMODO_API_SECRET", hidden=True),
            insecure,
        )

    def read(self, request: str, params: dict | None = None):
        body = json.dumps({"type": request, "params": params or {}}).encode()
        req = urllib.request.Request(
            self.url + "/read", data=body, headers=self.headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise KomodoError(f"{request}: HTTP {e.code}: {_api_error(e)}") from None
        except urllib.error.URLError as e:
            raise KomodoError(f"{request}: cannot reach {self.url}: {e.reason}") from None


def _setting(name: str, hidden: bool) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if not sys.stdin.isatty():
        raise KomodoError(f"set {name} in the environment")
    if hidden:
        value = getpass.getpass(f"{name} (hidden): ")
    else:
        # Prompt on stderr so it still shows when stdout is piped (| tee).
        print(f"{name}: ", end="", file=sys.stderr, flush=True)
        value = input()
    if not value.strip():
        raise KomodoError(f"{name} is empty")
    return value.strip()


def _api_error(e: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(e.read()).get("error", ""))[:300]
    except (ValueError, AttributeError):
        return e.reason


def list_full_stacks(client) -> list[dict]:
    """ListFullStacks is paginated (default 30 per page), so page until nothing new."""
    stacks: dict[str, dict] = {}
    for page in range(1000):
        batch = client.read("ListFullStacks", {"page": page, "limit": 100})
        new = [s for s in batch if s["name"] not in stacks]
        if not new:
            break
        stacks.update((s["name"], s) for s in new)
    return sorted(stacks.values(), key=lambda s: s["name"])


def export_resources(client) -> dict:
    return tomllib.loads(client.read("ExportAllResourcesToToml", EXPORT_PARAMS)["toml"])


# --- Parsing ------------------------------------------------------------------


def parse_env(text: str) -> list[tuple[str, str]]:
    """Parse a Komodo environment string exactly like Komodo does
    (komodo_client parsers::parse_key_value_list). Values keep their quotes."""
    pairs = []
    trimmed = text.strip()
    if not trimmed:
        return pairs
    for i, line in enumerate(trimmed.split("\n")):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        line = line.split(" #", 1)[0].strip().lstrip("-").strip()
        m = re.search(r"[=:]", line)
        if not m:
            raise ValueError(f"line {i} has no '=' or ':'")
        key, value = line[: m.start()].strip(), line[m.end() :].strip()
        if key[:1] in QUOTES and key[-1:] not in QUOTES and value[-1:] in QUOTES:
            key, value = key[1:].strip(), value[:-1].strip()
        pairs.append((key, value))
    return pairs


def dedupe(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Last value wins, like docker compose reading the .env file."""
    return list(dict(pairs).items())


def interpolate(text: str, values: dict[str, str]) -> tuple[str, set[str]]:
    """Replace [[NAME]] like Komodo does. Unknown names stay as-is."""
    missing: set[str] = set()

    def sub(m: re.Match) -> str:
        if m.group(1) in values:
            return values[m.group(1)]
        missing.add(m.group(1))
        return m.group(0)

    return REF_RE.sub(sub, text), missing


def dotenv_value(raw: str) -> tuple[str, bool]:
    """The value docker compose reads for `KEY=<raw>` in the .env Komodo writes.

    Returns (value, exact). exact=False means compose might read it differently
    (escapes, $-expansion, odd quoting) and the result needs a manual check.
    """
    if raw[:1] in QUOTES:
        quote, inner = raw[0], raw[1:-1]
        closed = len(raw) >= 2 and raw[-1] == quote
        if not closed:
            return raw, False
        simple = quote not in inner and "\\" not in inner
        if quote == '"':
            simple = simple and "$" not in inner
        return inner, simple
    return raw, "$" not in raw and "\\" not in raw


def dotenv_problem(key: str, value: str) -> str | None:
    """Why a key/value can't be stored in a sops dotenv file, or None."""
    if not ENV_KEY_RE.match(key):
        return "not a valid variable name"
    if "\\n" in value:
        return r"contains a literal \n, which sops dotenv turns into a newline"
    if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in value):
        return "contains control characters"
    return None


def build_dotenv(values: dict[str, str]) -> str:
    lines = []
    for key, value in values.items():
        problem = dotenv_problem(key, value)
        if problem:
            raise ValueError(f"{key}: {problem}")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def compose_vars(paths: list[Path]) -> dict[str, bool]:
    """${VAR} names used in compose files -> True if every use has a default."""
    found: dict[str, bool] = {}
    for path in paths:
        for line in path.read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            for m in COMPOSE_VAR_RE.finditer(line):
                name = m.group(1) or m.group(3)
                has_default = (m.group(2) or "").startswith((":-", "-", ":+", "+"))
                found[name] = found.get(name, True) and has_default
    return found


# --- Classification -----------------------------------------------------------


@dataclass
class Overrides:
    secret: set[str] = field(default_factory=set)
    plain: set[str] = field(default_factory=set)

    def lookup(self, name: str, scope: str | None = None) -> str | None:
        """Entries are NAME or <stack>/NAME. The scoped one wins."""
        for candidate in ([f"{scope}/{name}"] if scope else []) + [name]:
            if candidate in self.secret:
                return "secret"
            if candidate in self.plain:
                return "plain"
        return None


def parse_overrides(text: str) -> Overrides:
    """Tiny YAML subset: `secret:` / `plain:` with a [flow] or `- block` list."""
    lists: dict[str, list[str]] = {"secret": [], "plain": []}
    current = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            current = m.group(1)
            if current not in lists:
                raise ValueError(f"overrides line {n}: unknown section '{current}'")
            rest = m.group(2).strip()
            if rest.startswith("[") and rest.endswith("]"):
                lists[current] += [x.strip().strip("'\"") for x in rest[1:-1].split(",") if x.strip()]
            elif rest:
                raise ValueError(f"overrides line {n}: expected [A, B] or a '- NAME' list")
            continue
        m = re.match(r"^\s+-\s*(\S+)\s*$", line)
        if m and current:
            lists[current].append(m.group(1).strip("'\""))
            continue
        raise ValueError(f"overrides line {n}: cannot parse")
    both = set(lists["secret"]) & set(lists["plain"])
    if both:
        raise ValueError(f"overrides list these as both secret and plain: {', '.join(sorted(both))}")
    return Overrides(set(lists["secret"]), set(lists["plain"]))


def load_overrides(path: Path) -> Overrides:
    return parse_overrides(path.read_text()) if path.exists() else Overrides()


@dataclass
class Variable:
    name: str
    value: str
    description: str
    flagged: bool  # is_secret in Komodo
    is_secret: bool  # flagged, override or name pattern
    masked: bool  # value hidden because the API key isn't admin


def load_variables(raw: list[dict], overrides: Overrides) -> dict[str, Variable]:
    out = {}
    for v in raw:
        name, value, flagged = v["name"], v.get("value", ""), bool(v.get("is_secret"))
        override = overrides.lookup(name)
        # A plain override can't un-secret a Komodo secret Variable: its value
        # would end up in variables.toml.
        is_secret = flagged or override == "secret" or (
            override != "plain" and bool(SECRET_NAME_RE.search(name))
        )
        masked = flagged and value != "" and set(value) == {"#"}
        out[name] = Variable(name, value, v.get("description", ""), flagged, is_secret, masked)
    return out


@dataclass
class EnvKey:
    key: str
    raw: str  # value as Komodo parsed it, before [[VAR]] interpolation
    kind: str  # secret | plain | unknown
    why: str  # override | variable | name | default | ref
    refs: list[str]


def classify_key(scope: str, key: str, raw: str, variables: dict[str, Variable], overrides: Overrides) -> EnvKey:
    refs = REF_RE.findall(raw)
    override = overrides.lookup(key, scope)
    if override:
        return EnvKey(key, raw, override, "override", refs)
    if any(variables[r].is_secret for r in refs if r in variables):
        return EnvKey(key, raw, "secret", "variable", refs)
    if any(r not in variables for r in refs):
        # Probably a core/periphery config secret. Komodo resolves it at deploy.
        return EnvKey(key, raw, "unknown", "ref", refs)
    if SECRET_NAME_RE.search(key):
        return EnvKey(key, raw, "secret", "name", refs)
    return EnvKey(key, raw, "plain", "default", refs)


def classify_env(scope: str, text: str, variables: dict[str, Variable], overrides: Overrides) -> list[EnvKey]:
    return [classify_key(scope, k, v, variables, overrides) for k, v in dedupe(parse_env(text))]


def key_tag(k: EnvKey) -> str:
    tag = {
        "secret": f"secret ({k.why})",
        "plain": "plain (override)" if k.why == "override" else "plain",
        "unknown": "unknown",
    }[k.kind]
    return " ".join([tag] + [f"ref:[[{r}]]" for r in k.refs])


# --- Stacks -------------------------------------------------------------------


@dataclass
class Stack:
    name: str
    source: str
    server: str
    run_directory: str
    file_paths: list[str]
    environment: str
    env_files: list[str]  # env_file_path if not the default, plus additional_env_files
    has_wrapper: bool
    repo_dir: str | None  # directory in this repo that holds the stack's files


def find_repo_dir(repo: Path, name: str, run_directory: str) -> str | None:
    rd = run_directory.strip().rstrip("/")
    candidates = []
    if rd and not os.path.isabs(rd) and "[[" not in rd:
        candidates.append(os.path.normpath(rd))
    if rd:
        candidates.append(os.path.basename(rd))
    candidates.append(name)
    for c in candidates:
        if c and c not in (".", "..") and (repo / c).is_dir():
            return c
    return None


def load_stacks(api_stacks: list[dict], export: dict, repo: Path) -> list[Stack]:
    exported = {s["name"]: s.get("config", {}) for s in export.get("stack", [])}
    stacks = []
    for item in api_stacks:
        cfg, exp = item.get("config", {}), exported.get(item["name"], {})
        if cfg.get("files_on_host"):
            source = "files_on_host"
        elif cfg.get("linked_repo"):
            source = f"linked_repo ({exp.get('linked_repo') or cfg['linked_repo']})"
        elif cfg.get("repo"):
            source = f"repo ({cfg['repo']}@{cfg.get('branch') or '?'})"
        elif cfg.get("file_contents"):
            source = "ui (compose defined in Komodo)"
        else:
            source = "none"
        server = exp.get("server") or exp.get("swarm") or cfg.get("server_id") or cfg.get("swarm_id") or "-"
        run_directory = cfg.get("run_directory", "")
        env_files = [] if cfg.get("env_file_path", ".env") == ".env" else [cfg["env_file_path"]]
        env_files += [f if isinstance(f, str) else f.get("path", "") for f in cfg.get("additional_env_files") or []]
        stacks.append(
            Stack(
                name=item["name"],
                source=source,
                server=server,
                run_directory=run_directory,
                file_paths=list(cfg.get("file_paths") or []),
                environment=cfg.get("environment", ""),
                env_files=env_files,
                has_wrapper=bool(cfg.get("compose_cmd_wrapper")),
                repo_dir=find_repo_dir(repo, item["name"], run_directory),
            )
        )
    return stacks


def select(stacks: list[Stack], only: list[str], exclude: list[str]) -> list[Stack]:
    unknown = set(only) - {s.name for s in stacks}
    if unknown:
        raise ValueError(f"no such stack: {', '.join(sorted(unknown))}")
    return [s for s in stacks if (not only or s.name in only) and s.name not in exclude]


# --- Discover -----------------------------------------------------------------


def discover(client, repo: Path, overrides: Overrides, out=sys.stdout) -> int:
    def p(line: str = "") -> None:
        print(line, file=out)

    variables = load_variables(client.read("ListVariables"), overrides)
    export = export_resources(client)
    stacks = load_stacks(list_full_stacks(client), export, repo)

    counts = {k: len(v) for k, v in export.items() if isinstance(v, list)}
    p("== Resources (ExportAllResourcesToToml)")
    p("  " + (", ".join(f"{k}: {n}" for k, n in counts.items()) or "none"))

    ref_count: dict[str, int] = {}
    unknown_refs: set[str] = set()
    by_source: dict[str, int] = {}
    totals = {"secret": 0, "plain": 0, "unknown": 0}
    secret_why: dict[str, int] = {}
    stacks_with_secrets = 0
    literal_keys: dict[str, int] = {}
    no_dir, unparsable = [], []

    p(f"\n== Stacks ({len(stacks)})")
    for st in stacks:
        by_source[st.source.split(" ")[0]] = by_source.get(st.source.split(" ")[0], 0) + 1
        p(f"\n{st.name}")
        p(f"  source: {st.source}   server: {st.server}")
        p(f"  run_directory: {st.run_directory or '(root)'}   file_paths: {', '.join(st.file_paths) or '(default compose.yaml)'}")
        if st.repo_dir:
            has_file = (repo / st.repo_dir / SECRETS_FILE).exists()
            p(f"  repo dir: {st.repo_dir}/   {SECRETS_FILE}: {'present' if has_file else 'absent'}")
        else:
            no_dir.append(st.name)
            p("  repo dir: NOT FOUND")
        if any("/" in f for f in st.file_paths):
            p(f"  note: compose file is not directly in run_directory; the wrapper path '{SECRETS_FILE}' may need adjusting")
        if st.env_files:
            p(f"  extra env files: {', '.join(st.env_files)}")
        if st.has_wrapper:
            p("  compose_cmd_wrapper: already set")
        try:
            keys = classify_env(st.name, st.environment, variables, overrides)
        except ValueError as e:
            unparsable.append(st.name)
            p(f"  env: cannot parse ({e})")
            continue
        if keys:
            p(f"  env ({len(keys)}):")
            width = max(len(k.key) for k in keys)
            for k in keys:
                p(f"    {k.key:<{width}}  {key_tag(k)}")
        else:
            p("  env: empty")
        if any(k.kind == "secret" for k in keys):
            stacks_with_secrets += 1
        for k in keys:
            totals[k.kind] += 1
            if k.kind == "secret":
                secret_why[k.why] = secret_why.get(k.why, 0) + 1
            for r in k.refs:
                ref_count[r] = ref_count.get(r, 0) + 1
                if r not in variables:
                    unknown_refs.add(r)
            if not k.refs:
                literal_keys[k.key] = literal_keys.get(k.key, 0) + 1

        if st.repo_dir:
            files = [repo / st.repo_dir / f for f in (st.file_paths or ["compose.yaml"])]
            missing_files = [f.name for f in files if not f.is_file()]
            if missing_files:
                p(f"  compose file not in repo: {', '.join(missing_files)}")
            used = compose_vars([f for f in files if f.is_file()])
            env_names = {k.key for k in keys}
            not_in_env = [f"{v} (default)" if used[v] else v for v in sorted(set(used) - env_names)]
            unused = sorted(env_names - set(used))
            if not_in_env:
                p(f"  compose uses, not in env: {', '.join(not_in_env)}")
            if unused:
                p(f"  in env, not used by compose: {', '.join(unused)}")

    p(f"\n== Variables ({len(variables)})")
    if variables:
        width = max(len(n) for n in variables)
        for v in sorted(variables.values(), key=lambda v: v.name):
            if v.flagged:
                tag = "secret"
            elif v.is_secret:
                tag = "secret by name/override, but is_secret=false in Komodo"
            else:
                tag = "plain"
            p(f"  {v.name:<{width}}  {tag}  (used in {ref_count.get(v.name, 0)} env keys)")
    if any(v.masked for v in variables.values()):
        p("  note: secret values are hidden from this API key. --write-secrets needs an admin key.")

    p("\n== Summary")
    p("  stacks by source: " + ", ".join(f"{k} {n}" for k, n in sorted(by_source.items())))
    p(f"  stacks with secrets: {stacks_with_secrets}")
    p(
        f"  env keys: {totals['secret']} secret ("
        + ", ".join(f"{n} by {w}" for w, n in sorted(secret_why.items()))
        + f"), {totals['plain']} plain, {totals['unknown']} unknown"
    )
    shared = sorted(((n, k) for k, n in literal_keys.items() if n >= 3), reverse=True)
    if shared:
        p("  keys set literally in 3+ stacks (candidates for Komodo Variables): " + ", ".join(f"{k} ({n})" for n, k in shared))
    if unknown_refs:
        p("  [[refs]] that are not Komodo Variables (core/periphery config secrets?): " + ", ".join(sorted(unknown_refs)))
    if no_dir:
        p("  stacks with no matching directory in the repo: " + ", ".join(no_dir))
    if unparsable:
        p("  stacks whose env could not be parsed: " + ", ".join(unparsable))
    p("  'secret (name)' is a guess from the key name. Fix wrong guesses in scripts/secret-classification.yaml.")
    return 0


# --- Write secrets ------------------------------------------------------------


@dataclass
class SecretPlan:
    values: dict[str, str] = field(default_factory=dict)
    review: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)


def plan_secrets(st: Stack, variables: dict[str, Variable], overrides: Overrides) -> SecretPlan:
    """Resolve the stack's secret keys to the exact values compose sees today.
    Everything stays in memory."""
    plan = SecretPlan()
    keys = [k for k in classify_env(st.name, st.environment, variables, overrides) if k.kind == "secret"]
    if not keys:
        return plan
    masked = sorted({r for k in keys for r in k.refs if r in variables and variables[r].masked})
    if masked:
        raise KomodoError(
            f"{st.name}: secret Variable values are hidden from this API key ({', '.join(masked)}). Use an admin API key."
        )
    # Komodo interpolates the whole environment string first, then parses it.
    text, _ = interpolate(st.environment, {n: v.value for n, v in variables.items()})
    resolved_pairs = dedupe(parse_env(text))
    if [k for k, _ in resolved_pairs] != [k for k, _ in dedupe(parse_env(st.environment))]:
        plan.manual.append(f"{st.name}: a Variable value changes how the env parses; migrate this stack by hand")
        return plan
    resolved = dict(resolved_pairs)
    for k in keys:
        unresolved = [r for r in k.refs if r not in variables]
        if unresolved:
            plan.manual.append(f"{st.name}/{k.key}: uses [[{']], [['.join(unresolved)}]], which is not a Komodo Variable")
            continue
        value, exact = dotenv_value(resolved[k.key])
        problem = dotenv_problem(k.key, value)
        if problem:
            plan.manual.append(f"{st.name}/{k.key}: {problem}")
            continue
        if not exact:
            plan.review.append(f"{st.name}/{k.key}: quotes, '$' or '\\' in the value; check the result with sops")
        plan.values[k.key] = value
    return plan


def safe_stderr(stderr: bytes, secrets) -> str:
    text = stderr.decode(errors="replace").strip()
    if any(s and s in text for s in secrets):
        return "(sops output hidden because it contained plaintext)"
    return text[-500:]


def sops_encrypt(plaintext: str, target: Path, repo: Path, secrets) -> bytes:
    rel = target.relative_to(repo).as_posix()
    proc = subprocess.run(
        ["sops", "encrypt", "--input-type", "dotenv", "--output-type", "dotenv", "--filename-override", rel, "/dev/stdin"],
        input=plaintext.encode(),
        capture_output=True,
        cwd=repo,
    )
    if proc.returncode != 0:
        raise SopsError(f"sops encrypt failed for {rel} (exit {proc.returncode}): {safe_stderr(proc.stderr, secrets)}")
    return proc.stdout


def encrypted_keys(data: str) -> list[str]:
    """Key names in a sops dotenv file. Fails if a non-empty value isn't encrypted."""
    if not re.search(r"^sops_mac=", data, re.MULTILINE):
        raise SopsError("sops output has no sops_mac line")
    keys = []
    for line in data.splitlines():
        if not line or line.startswith("#") or line.startswith("sops_"):
            continue
        key, _, value = line.partition("=")
        if value and not value.startswith("ENC["):
            raise SopsError(f"{key} is not encrypted in the sops output")
        keys.append(key)
    return keys


def verify_roundtrip(path: Path, expected: dict[str, str]) -> list[str]:
    """Run `sops exec-env` like the Komodo wrapper does and compare the values
    in memory. Returns the keys that differ. Needs the age private key."""
    dump = "import json, os, sys; json.dump(dict(os.environ), sys.stdout)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(dump)}"
    proc = subprocess.run(["sops", "exec-env", path.name, command], cwd=path.parent, capture_output=True)
    if proc.returncode != 0:
        raise SopsError(f"sops exec-env failed for {path} (exit {proc.returncode}); --verify needs the age private key, e.g. SOPS_AGE_KEY_FILE")
    env = json.loads(proc.stdout)
    return [k for k, v in expected.items() if env.get(k) != v]


def write_secrets(client, repo: Path, overrides: Overrides, only=(), exclude=(), force=False, verify=False, out=sys.stdout) -> int:
    def p(line: str = "") -> None:
        print(line, file=out)

    if not shutil.which("sops"):
        raise SopsError("sops is not on PATH")
    if not (repo / ".sops.yaml").is_file():
        raise SopsError(f"{repo / '.sops.yaml'} not found")

    variables = load_variables(client.read("ListVariables"), overrides)
    export = export_resources(client)
    stacks = select(load_stacks(list_full_stacks(client), export, repo), list(only), list(exclude))

    review, manual, skipped = [], [], []
    written = 0
    for st in stacks:
        try:
            plan = plan_secrets(st, variables, overrides)
        except ValueError as e:
            raise ValueError(f"stack {st.name}: {e}") from None
        review += plan.review
        manual += plan.manual
        if not plan.values:
            continue
        if not st.repo_dir:
            manual.append(f"{st.name}: no matching directory in the repo, nothing written")
            continue
        target = repo / st.repo_dir / SECRETS_FILE
        if target.exists() and not force:
            skipped.append(st.name)
            continue
        data = sops_encrypt(build_dotenv(plan.values), target, repo, plan.values.values())
        keys = encrypted_keys(data.decode())
        if keys != list(plan.values):
            raise SopsError(f"{st.name}: sops output has different keys than expected, nothing written")
        target.write_bytes(data)
        written += 1
        line = f"{st.name}: {len(keys)} secrets written ({', '.join(keys)})"
        if verify:
            bad = verify_roundtrip(target, plan.values)
            line += f"  ROUND TRIP MISMATCH: {', '.join(bad)}" if bad else "  round trip ok"
            if bad:
                manual.append(f"{st.name}: round trip mismatch for {', '.join(bad)}")
        p(line)

    p(f"\n{written} secrets files written.")
    if skipped:
        p(f"Skipped, {SECRETS_FILE} already exists (use --force to overwrite): {', '.join(skipped)}")
    if exclude:
        p(f"Excluded: {', '.join(exclude)}")
    if review:
        p("\nREVIEW (written, but compose may have read these differently before):")
        for line in review:
            p(f"  {line}")
    if manual:
        p("\nMANUAL (not written, add with `sops <stack>/secrets.sops.env`):")
        for line in manual:
            p(f"  {line}")
    return 1 if manual else 0


# --- TOML ---------------------------------------------------------------------

BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_key(key: str) -> str:
    return key if BARE_KEY_RE.match(key) else toml_str(key)


def toml_str(s: str) -> str:
    escapes = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}
    out = []
    for ch in s:
        if ch in escapes:
            out.append(escapes[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def toml_multiline(s: str) -> str:
    out = []
    for i, ch in enumerate(s):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"' and (i + 1 == len(s) or s[i + 1] == '"' or (i > 0 and s[i - 1] == '"')):
            # Keep runs of quotes and a trailing quote from closing the string.
            out.append('\\"')
        elif ch == "\r":
            out.append("\\r")
        elif ch not in "\n\t" and (ord(ch) < 0x20 or ord(ch) == 0x7F):
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"""\n' + "".join(out) + '"""'


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return toml_multiline(v) if "\n" in v else toml_str(v)
    if isinstance(v, list):
        items = [toml_value(x) for x in v]
        inline = "[" + ", ".join(items) + "]"
        if len(inline) <= 80 and "\n" not in inline:
            return inline
        return "[\n" + "".join(f"  {i},\n" for i in items) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in _flatten([], v)) + " }"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _flatten(prefix: list[str], d: dict):
    """Nested dicts as dotted keys: {"a": {"b": 1}} -> ("a.b", 1)."""
    for k, v in d.items():
        if isinstance(v, dict) and v:
            yield from _flatten(prefix + [k], v)
        else:
            yield ".".join(map(toml_key, prefix + [k])), v


def _emit_table(lines: list[str], path: list[str], table: dict) -> None:
    # Layout like Komodo's own export: [[stack]] / [stack.config] headers,
    # [[procedure.config.stage]] arrays, dotted keys and inline tables below that.
    tables, arrays = [], []
    for k, v in table.items():
        if isinstance(v, dict) and v and len(path) < 2:
            tables.append((k, v))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v) and len(path) < 3:
            arrays.append((k, v))
        elif isinstance(v, dict) and v:
            lines += [f"{dotted} = {toml_value(leaf)}" for dotted, leaf in _flatten([k], v)]
        else:
            lines.append(f"{toml_key(k)} = {toml_value(v)}")
    for k, v in tables:
        sub = path + [k]
        lines += ["", f"[{'.'.join(map(toml_key, sub))}]"]
        _emit_table(lines, sub, v)
    for k, items in arrays:
        sub = path + [k]
        for item in items:
            lines += ["", f"[[{'.'.join(map(toml_key, sub))}]]"]
            _emit_table(lines, sub, item)


def emit_toml(doc: dict, header: str = "") -> str:
    lines = [f"# {line}" if line else "#" for line in header.splitlines()]
    body: list[str] = []
    _emit_table(body, [], doc)
    while body and body[0] == "":
        body.pop(0)
    if lines and body:
        lines.append("")
    return "\n".join(lines + body) + "\n"


def variable_name(*parts: str) -> str:
    return "KOMODO_" + re.sub(r"[^A-Z0-9]+", "_", "_".join(parts).upper()).strip("_")


@dataclass
class TomlResult:
    files: dict[str, dict] = field(default_factory=dict)
    needed_vars: list[tuple[str, str]] = field(default_factory=list)  # (variable, where the value is now)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    # stacks that get the wrapper but have no secrets.sops.env in the repo
    missing_secrets: list[str] = field(default_factory=list)
    # (label, secret value) pairs for the leak check. Labels are names only.
    leak_check: list[tuple[str, str]] = field(default_factory=list)


def build_resources(
    export: dict,
    variables: dict[str, Variable],
    overrides: Overrides,
    repo: Path,
    only=(),
    exclude=(),
) -> TomlResult:
    """Turn an exported Resource Sync TOML into files with no secret values."""
    result = TomlResult()
    unexpected = set(export) - set(RESOURCE_FILES)
    if unexpected:
        raise ValueError(f"unexpected sections in the export: {', '.join(sorted(unexpected))}")
    values = {n: v.value for n, v in variables.items() if not v.masked}

    for v in variables.values():
        if v.is_secret and not v.masked:
            result.leak_check.append((f"variable/{v.name}", v.value))

    def leak(label: str, value: str) -> None:
        result.leak_check.append((label, value))

    def split_env(kind: str, name: str, text: str) -> tuple[list[str], list[EnvKey]]:
        """Plain lines to keep, and the secret keys that were taken out."""
        keep, secrets = [], []
        resolved = dict(dedupe(parse_env(interpolate(text, values)[0])))
        for k in classify_env(name, text, variables, overrides):
            if k.kind == "plain" or (k.kind == "unknown" and PURE_REF_RE.match(k.raw)):
                keep.append(f"{k.key}={k.raw}")
            elif k.kind == "unknown":
                result.manual.append(f"{kind} {name}/{k.key}: mixes a [[ref]] that is not a Komodo Variable with other text")
            else:
                secrets.append(k)
                if not k.refs:
                    leak(f"{name}/{k.key}", k.raw)
                if k.key in resolved and all(r in values for r in k.refs):
                    leak(f"{name}/{k.key}", dotenv_value(resolved[k.key])[0])
        return keep, secrets

    def set_env(cfg: dict, field_name: str, lines: list[str]) -> None:
        if lines:
            cfg[field_name] = "\n".join(lines) + "\n"
        else:
            cfg.pop(field_name, None)

    def drop(kind: str, name: str, cfg: dict, field_name: str) -> None:
        if cfg.get(field_name):
            leak(f"{kind}/{name}/{field_name}", cfg.pop(field_name))
            result.dropped.append(f"{kind} {name}: {field_name}")

    def env_to_refs(kind: str, name: str, cfg: dict, field_name: str, all_secret: bool = False) -> None:
        """Secret keys become [[KOMODO_...]] refs to secret Variables (non-stack resources)."""
        text = cfg.get(field_name, "")
        if not text:
            return
        if all_secret:
            keep, secrets = [], classify_env(name, text, variables, overrides)
            for k in secrets:
                if not k.refs:
                    leak(f"{name}/{k.key}", k.raw)
        else:
            keep, secrets = split_env(kind, name, text)
        for k in secrets:
            if PURE_REF_RE.match(k.raw):
                keep.append(f"{k.key}={k.raw}")
                continue
            var = variable_name(kind, name, k.key)
            keep.append(f"{k.key}=[[{var}]]")
            result.needed_vars.append((var, f"{kind} {name} {field_name} {k.key}"))
        set_env(cfg, field_name, keep)

    def convert(kind: str, res: dict) -> bool:
        """Strip secrets from one exported resource in place. False = leave it out."""
        name = res["name"]
        cfg = res.setdefault("config", {})
        if kind == "stack":
            if name in exclude:
                result.notes.append(f"stack {name}: excluded, left out of stacks.toml")
                return False
            if only and name not in only:
                return False
        for field_name in ("webhook_secret", "passkey"):
            drop(kind, name, cfg, field_name)

        if kind == "stack":
            keep, secrets = split_env(kind, name, cfg.get("environment", ""))
            set_env(cfg, "environment", keep)
            if secrets:
                if cfg.get("compose_cmd_wrapper") and cfg["compose_cmd_wrapper"] != WRAPPER:
                    result.notes.append(f"stack {name}: existing compose_cmd_wrapper replaced")
                cfg["compose_cmd_wrapper"] = WRAPPER
                cfg["compose_cmd_wrapper_include"] = list(WRAPPER_INCLUDE)
                repo_dir = find_repo_dir(repo, name, cfg.get("run_directory", ""))
                if not repo_dir or not (repo / repo_dir / SECRETS_FILE).exists():
                    result.missing_secrets.append(name)
        elif kind == "alerter":
            params = cfg.get("endpoint", {}).get("params", {})
            url = params.get("url")
            if url and not PURE_REF_RE.match(url):
                var = variable_name("alerter", name, "url")
                leak(f"alerter/{name}/url", url)
                params["url"] = f"[[{var}]]"
                result.needed_vars.append((var, f"alerter {name} endpoint URL"))
        elif kind == "builder":
            drop(kind, name, cfg.get("params", {}), "passkey")
        elif kind in ("deployment", "repo"):
            env_to_refs(kind, name, cfg, "environment")
        elif kind == "build":
            env_to_refs(kind, name, cfg, "build_args")
            env_to_refs(kind, name, cfg, "secret_args", all_secret=True)
        if cfg.get("file_contents"):
            # Only the leak check covers free-form contents.
            result.notes.append(f"{kind} {name}: has inline file_contents, check it for secrets by hand")
        return True

    for kind, resources in export.items():
        if kind == "variable":
            continue  # rebuilt from ListVariables below
        if kind == "resource_sync":
            # syncs.toml is written by hand: the export drops default values like delete = false.
            result.notes.append(f"resource_sync: {len(resources)} left out, syncs.toml is maintained by hand")
            continue
        kept = []
        for res in resources:
            try:
                if convert(kind, res):
                    kept.append(res)
            except ValueError as e:
                raise ValueError(f"{kind} {res['name']}: {e}") from None
        if kept:
            result.files[RESOURCE_FILES[kind]] = {kind: kept}

    plain_vars = []
    for v in sorted(variables.values(), key=lambda v: v.name):
        if v.is_secret:
            if not v.flagged:
                result.notes.append(f"variable {v.name}: looks secret but is_secret=false in Komodo; left out, mark it secret")
            continue
        entry = {"name": v.name, "value": v.value}
        if v.description:
            entry["description"] = v.description
        plain_vars.append(entry)
    if plain_vars:
        result.files[RESOURCE_FILES["variable"]] = {"variable": plain_vars}
    return result


def find_leaks(rendered: dict[str, str], leak_check: list[tuple[str, str]]) -> list[str]:
    """Labels (never values) of secret values that appear in the rendered files."""
    hits = []
    for fname, text in rendered.items():
        for label, value in leak_check:
            if len(value) >= MIN_LEAK_CHECK_LEN and value in text:
                hits.append(f"{fname}: value of {label}")
    return sorted(set(hits))


TOML_HEADER = (
    "Generated by scripts/komodo-migrate.py --write-toml.\n"
    "No secret values: stack secrets are in <stack>/secrets.sops.env,\n"
    "other secrets are [[KOMODO_...]] refs to secret Komodo Variables."
)


def write_toml(client, repo: Path, overrides: Overrides, out_dir: Path, only=(), exclude=(), force=False, allow_match=(), out=sys.stdout) -> int:
    def p(line: str = "") -> None:
        print(line, file=out)

    variables = load_variables(client.read("ListVariables"), overrides)
    export = export_resources(client)
    known_stacks = {s["name"] for s in export.get("stack", [])}
    if set(only) - known_stacks:
        raise ValueError(f"no such stack: {', '.join(sorted(set(only) - known_stacks))}")
    result = build_resources(export, variables, overrides, repo, only, exclude)
    if result.missing_secrets:
        # Synced like this, these stacks would lose their secrets and fail to deploy.
        p(f"These stacks have secrets but no {SECRETS_FILE} in the repo. Run --write-secrets for them first.")
        p(f"Nothing written: {', '.join(result.missing_secrets)}")
        return 1

    rendered = {}
    for fname, doc in result.files.items():
        text = emit_toml(doc, TOML_HEADER)
        if tomllib.loads(text) != doc:
            raise ValueError(f"{fname}: TOML round trip failed (bug in the emitter), nothing written")
        rendered[fname] = text

    leaks = [h for h in find_leaks(rendered, result.leak_check) if h.split("value of ", 1)[1] not in allow_match]
    if leaks:
        p("A secret value was found in the generated TOML. Nothing written:")
        for hit in leaks:
            p(f"  {hit}")
        p("Fix the classification, or pass --allow-match <label> if it's a false positive.")
        return 1

    existing = [f for f in rendered if (out_dir / f).exists()]
    if existing and not force:
        p(f"Already exists (use --force to overwrite): {', '.join(existing)}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname, text in rendered.items():
        (out_dir / fname).write_text(text)
        kind = next(iter(result.files[fname]))
        p(f"{out_dir / fname}: {len(result.files[fname][kind])} {kind}")

    var_names = set(variables)
    if result.needed_vars:
        p("\nCreate these as SECRET Komodo Variables (copy the value from where it is now):")
        for var, where in result.needed_vars:
            status = "exists" if var in var_names and variables[var].flagged else "missing"
            p(f"  {var}  <- {where}  [{status}]")
    if result.dropped:
        p("\nDropped (Komodo doesn't interpolate these; the sync would clear them):")
        for line in result.dropped:
            p(f"  {line}")
    for title, lines in (("Notes", result.notes), ("MANUAL (left out, handle by hand)", result.manual)):
        if lines:
            p(f"\n{title}:")
            for line in lines:
                p(f"  {line}")
    return 1 if result.manual else 0


# --- CLI ----------------------------------------------------------------------


def _names(values: list[str]) -> list[str]:
    """--stack a,b --stack c -> [a, b, c]"""
    return [name.strip() for value in values for name in value.split(",") if name.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Output contains key names only, never values.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--discover", dest="mode", action="store_const", const="discover", help="print structure and key names (default)")
    mode.add_argument("--write-secrets", dest="mode", action="store_const", const="secrets", help="write <stack>/secrets.sops.env files")
    mode.add_argument("--write-toml", dest="mode", action="store_const", const="toml", help="write komodo/resources/*.toml")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent, help="repo root (default: this repo)")
    parser.add_argument("--stack", action="append", default=[], metavar="NAME", help="write modes: only these stacks (comma-separated or repeatable)")
    parser.add_argument("--exclude", action="append", metavar="NAME", help=f"write modes: skip these stacks (comma-separated or repeatable, default: {','.join(DEFAULT_EXCLUDE)})")
    parser.add_argument("--force", action="store_true", help="overwrite existing output files")
    parser.add_argument("--verify", action="store_true", help="--write-secrets: decrypt with sops exec-env and compare in memory (needs the age private key)")
    parser.add_argument("--out", type=Path, help="--write-toml: output directory (default: <repo>/komodo/resources)")
    parser.add_argument("--allow-match", action="append", default=[], metavar="LABEL", help="--write-toml: accept a leak-check hit, e.g. mealie/DB_USER")
    parser.add_argument("--insecure", action="store_true", help="don't verify Komodo's TLS certificate")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    only = _names(args.stack)
    exclude = DEFAULT_EXCLUDE if args.exclude is None else _names(args.exclude)
    try:
        overrides = load_overrides(repo / "scripts" / "secret-classification.yaml")
        client = Komodo.from_env(args.insecure)
        if args.mode == "secrets":
            return write_secrets(client, repo, overrides, only, exclude, args.force, args.verify)
        if args.mode == "toml":
            out_dir = args.out or repo / "komodo" / "resources"
            return write_toml(client, repo, overrides, out_dir, only, exclude, args.force, args.allow_match)
        return discover(client, repo, overrides)
    except (KomodoError, SopsError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
