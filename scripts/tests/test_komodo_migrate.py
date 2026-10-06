"""Tests for scripts/komodo-migrate.py.

Run from the repo root:  python3 -m unittest discover -s scripts/tests -v

All fixture values are fake. Every secret value starts with FAKESECRET, so the
tests can check that none of them reach output, TOML, or encrypted files.
The sops round-trip tests need `sops` and `age-keygen` on PATH and are
skipped otherwise.
"""

import copy
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SCRIPT = HERE.parent / "komodo-migrate.py"
REPO_ROOT = HERE.parent.parent

spec = importlib.util.spec_from_file_location("komodo_migrate", SCRIPT)
km = importlib.util.module_from_spec(spec)
sys.modules["komodo_migrate"] = km
spec.loader.exec_module(km)

MARKER = "FAKESECRET"
OVERRIDES = km.parse_overrides("secret: [SOCIALACCOUNT_PROVIDERS]\nplain: [AUTHENTIK_ERROR_REPORTING__ENABLED]\n")

COMPOSE_FILES = {
    "mealie/compose.yaml": (
        "services:\n  mealie:\n    environment:\n"
        "      - BASE_URL=${BASE_URL}\n      - DB=${POSTGRES_PASSWORD}\n"
        "      - OIDC=${OIDC_CLIENT_SECRET}\n      - SMTP=${SMTP_TOKEN}\n"
        "      - TZ=${TZ:-UTC}\n      - PUID=$PUID\n"
        "    labels:\n      - traefik.http.routers.mealie.rule=Host(`mealie.${DOMAIN}`)\n"
    ),
    "wizarr/compose.yaml": "services:\n  wizarr:\n    environment:\n      - TZ=${TZ}\n      - X=${CORE_SECRET_REF}\n",
    "komodo/compose.yaml": "services:\n  core:\n    environment:\n      - P=${KOMODO_PASSKEY}\n",
    "spotweb/compose.yaml": (
        "services:\n  spotweb:\n    command: echo $${NOT_A_VAR}\n    environment:\n"
        "      - DB=${DB_PASSWORD}\n      - K=${API_KEY}\n      - S=${SOCIALACCOUNT_PROVIDERS}\n"
    ),
}

EXPECTED_SECRETS = {
    "mealie": {
        "POSTGRES_PASSWORD": "FAKESECRET-shared-db",
        "OIDC_CLIENT_SECRET": "FAKESECRET-oidc",
        "SMTP_TOKEN": "FAKESECRET-smtp-token",
    },
    "spotweb": {
        "DB_PASSWORD": "FAKESECRET pass with spaces",
        "API_KEY": "FAKESECRET$dollar",
        "SOCIALACCOUNT_PROVIDERS": '{"openid_connect": {"APPS": [{"secret": "FAKESECRET-json"}]}}',
    },
}


class FakeKomodo:
    """Serves the fixtures. Ignores `limit` and pages by 3, like a Komodo
    whose default page size is smaller than the stack count."""

    def __init__(self, variables=None):
        self.stacks = json.loads((FIXTURES / "list_full_stacks.json").read_text())
        self.variables = variables if variables is not None else json.loads((FIXTURES / "list_variables.json").read_text())
        self.toml = (FIXTURES / "export_all_resources.toml").read_text()

    def read(self, request, params=None):
        if request == "ListFullStacks":
            page = params.get("page", 0)
            return copy.deepcopy(self.stacks[page * 3 : (page + 1) * 3])
        if request == "ListVariables":
            return copy.deepcopy(self.variables)
        if request == "ExportAllResourcesToToml":
            assert params == km.EXPORT_PARAMS
            return {"toml": self.toml}
        raise AssertionError(f"unexpected request {request}")


def make_repo(root: Path) -> Path:
    for rel, text in COMPOSE_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def load_export():
    return tomllib.loads((FIXTURES / "export_all_resources.toml").read_text())


def load_vars(overrides=OVERRIDES, raw=None):
    raw = raw if raw is not None else json.loads((FIXTURES / "list_variables.json").read_text())
    return km.load_variables(raw, overrides)


class TempRepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()


class ParseTests(unittest.TestCase):
    def test_parse_env_matches_komodo_doc_example(self):
        text = (
            "# Supports comments\nKEY_1 = value_1 # end of line comments\n\n"
            '# Supports string wrapped values\nKEY_2="value_2"\n\'KEY_3 = value_3\'\n\n'
            '# Also supports yaml list formats\n- KEY_4: \'value_4\'\n- "KEY_5=value_5"\n\n'
            "# Wrapping outer quotes are removed while inner quotes are preserved\n\"KEY_6 = 'value_6'\"\n"
        )
        self.assertEqual(
            km.parse_env(text),
            [
                ("KEY_1", "value_1"),
                ("KEY_2", '"value_2"'),
                ("KEY_3", "value_3"),
                ("KEY_4", "'value_4'"),
                ("KEY_5", "value_5"),
                ("KEY_6", "'value_6'"),
            ],
        )

    def test_parse_env_splits_on_first_assignment_char(self):
        self.assertEqual(km.parse_env("URL=http://a:1/b?c=d"), [("URL", "http://a:1/b?c=d")])
        self.assertEqual(km.parse_env("// comment\n\n"), [])

    def test_parse_error_does_not_include_the_line(self):
        with self.assertRaises(ValueError) as ctx:
            km.parse_env("OK=1\nFAKESECRET-no-assignment")
        self.assertNotIn(MARKER, str(ctx.exception))

    def test_dedupe_keeps_last(self):
        self.assertEqual(km.dedupe([("A", "1"), ("B", "2"), ("A", "3")]), [("A", "3"), ("B", "2")])

    def test_interpolate(self):
        text, missing = km.interpolate("A=[[X]]-[[Y]]", {"X": "x"})
        self.assertEqual(text, "A=x-[[Y]]")
        self.assertEqual(missing, {"Y"})

    def test_dotenv_value(self):
        cases = {
            "plain": ("plain", True),
            '"double"': ("double", True),
            "'single $x \"q\"'": ('single $x "q"', True),
            '{"json": "ok"}': ('{"json": "ok"}', True),
            "with$dollar": ("with$dollar", False),
            '"dq $x"': ("dq $x", False),
            '"esc\\"aped"': ('esc\\"aped', False),
            "back\\slash": ("back\\slash", False),
            '"unclosed': ('"unclosed', False),
            "": ("", True),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(km.dotenv_value(raw), expected)

    def test_build_dotenv(self):
        self.assertEqual(km.build_dotenv({"A": "1", "B": "x y=z"}), "A=1\nB=x y=z\n")
        bad = {"BAD KEY": "FAKESECRET-a", "NL": "FAKESECRET\\nb", "CTRL": "FAKESECRET\x01"}
        for key, value in bad.items():
            with self.subTest(key=key), self.assertRaises(ValueError) as ctx:
                km.build_dotenv({key: value})
            self.assertNotIn(MARKER, str(ctx.exception))

    def test_compose_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "compose.yaml"
            f.write_text("a: ${A}\nb: ${B:-x}\nc: $C\n# d: ${D}\ne: $${E}\nf: ${B}\ng: ${G-y}\n")
            self.assertEqual(km.compose_vars([f]), {"A": False, "B": False, "C": False, "G": True})


class ClassifyTests(unittest.TestCase):
    def test_precedence(self):
        variables = load_vars()
        ov = km.parse_overrides("secret:\n  - FORCED\nplain:\n  - mealie/MY_TOKEN\n")

        def kind(key, raw, scope="mealie"):
            k = km.classify_key(scope, key, raw, variables, ov)
            return k.kind, k.why

        self.assertEqual(kind("FORCED", "x"), ("secret", "override"))
        self.assertEqual(kind("MY_TOKEN", "x"), ("plain", "override"))
        self.assertEqual(kind("MY_TOKEN", "x", scope="other"), ("secret", "name"))
        self.assertEqual(kind("DB_URL", "pg://u:[[SHARED_DB_PASSWORD]]@db"), ("secret", "variable"))
        self.assertEqual(kind("SMTP", "[[SMTP_TOKEN]]"), ("secret", "variable"))  # secret by name
        self.assertEqual(kind("X", "[[NOPE]]"), ("unknown", "ref"))
        self.assertEqual(kind("GRAFANA_ADMIN_PASSWORD", "x"), ("secret", "name"))
        self.assertEqual(kind("TZ", "[[DOMAIN]]"), ("plain", "default"))

    def test_overrides_parser(self):
        ov = km.parse_overrides("# c\nsecret: [A, 'B']  # trailing\nplain:\n  - C\n  - \"D\"\n")
        self.assertEqual((ov.secret, ov.plain), ({"A", "B"}, {"C", "D"}))
        for text in ("other: [A]\n", "secret: A\n", "secret: [A]\nplain: [A]\n", "garbage\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                km.parse_overrides(text)

    def test_repo_overrides_file_parses(self):
        ov = km.load_overrides(REPO_ROOT / "scripts" / "secret-classification.yaml")
        self.assertIn("TUNNELID", ov.secret)
        self.assertIn("AUTHENTIK_ERROR_REPORTING__ENABLED", ov.plain)

    def test_variables(self):
        raw = [
            {"name": "FLAGGED", "value": "#####", "is_secret": True},
            {"name": "SOME_TOKEN", "value": "v", "is_secret": False},
            {"name": "PLAIN", "value": "v", "is_secret": False},
        ]
        ov = km.parse_overrides("plain: [FLAGGED]\n")
        v = km.load_variables(raw, ov)
        self.assertTrue(v["FLAGGED"].is_secret)  # plain override can't un-secret it
        self.assertTrue(v["FLAGGED"].masked)
        self.assertTrue(v["SOME_TOKEN"].is_secret and not v["SOME_TOKEN"].flagged)
        self.assertFalse(v["PLAIN"].is_secret)

    def test_find_repo_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            self.assertEqual(km.find_repo_dir(repo, "mealie", "mealie"), "mealie")
            self.assertEqual(km.find_repo_dir(repo, "x", "[[DOCKER_STACKS_DIR]]/wizarr"), "wizarr")
            self.assertEqual(km.find_repo_dir(repo, "x", "/srv/stacks/spotweb/"), "spotweb")
            self.assertEqual(km.find_repo_dir(repo, "komodo", ""), "komodo")
            self.assertIsNone(km.find_repo_dir(repo, "nope", "../nope"))


class TomlEmitterTests(unittest.TestCase):
    def roundtrip(self, doc):
        text = km.emit_toml(doc, "header line\n\nsecond")
        self.assertEqual(tomllib.loads(text), doc, text)
        return text

    def test_export_fixture_roundtrips(self):
        self.roundtrip(load_export())

    def test_tricky_strings(self):
        strings = [
            'a"', '""', '"""', 'x"""y', "back\\slash\\", "tab\there", "cr\rhere", "ctl\x01\x7f",
            "multi\nline", 'multi\nending quote"', 'multi\n"""\n', "\nleading newline", "trailing\\\n",
            "unicode ✓ é", "", "'single'",
        ]
        for s in strings:
            with self.subTest(s=s):
                self.roundtrip({"t": {"s": s, "l": [s, s]}, "k": s})

    def test_structures(self):
        self.roundtrip({
            "stack": [
                {"name": "a", "config": {"x": 1, "f": 1.5, "b": False, "empty": {}, "list": [], "sub": {"deep": {"y": "z"}}}},
                {"name": "b", "config": {"stage": [{"executions": [{"execution": {"type": "T", "params": {"n": 1}}}]}]}},
            ],
            "odd key": {"a.b": 1, "with space": [1, 2, 3]},
            "long": ["x" * 30, "y" * 30, "z" * 30],
        })


class DiscoverTests(TempRepoCase):
    def run_discover(self, variables=None):
        buf = io.StringIO()
        rc = km.discover(FakeKomodo(variables), self.repo, OVERRIDES, out=buf)
        return rc, buf.getvalue()

    def test_output_has_names_only(self):
        rc, out = self.run_discover()
        self.assertEqual(rc, 0)
        self.assertNotIn(MARKER, out)
        self.assertIn("== Stacks (4)", out)  # paged past the first 3
        self.assertIn("server: 1, stack: 4, deployment: 1, repo: 1, procedure: 1, alerter: 2, builder: 1", out)

    def test_stack_details(self):
        _, out = self.run_discover()
        mealie = out.split("\nmealie\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("source: linked_repo (docker-stacks)   server: local", mealie)
        self.assertRegex(mealie, r"POSTGRES_PASSWORD\s+secret \(variable\) ref:\[\[SHARED_DB_PASSWORD\]\]")
        self.assertRegex(mealie, r"OIDC_CLIENT_SECRET\s+secret \(name\)")
        self.assertRegex(mealie, r"BASE_URL\s+plain ref:\[\[DOMAIN\]\]")
        self.assertIn("compose uses, not in env: PUID, TZ (default)", mealie)
        self.assertIn("in env, not used by compose: ALLOW_SIGNUP", mealie)

        wizarr = out.split("\nwizarr\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("source: files_on_host", wizarr)
        self.assertIn("extra env files: ../global.env", wizarr)
        self.assertIn("repo dir: wizarr/", wizarr)
        self.assertRegex(wizarr, r"AUTHENTIK_ERROR_REPORTING__ENABLED\s+plain \(override\)")
        self.assertRegex(wizarr, r"CORE_SECRET_REF\s+unknown ref:\[\[KOMODO_CORE_ONLY\]\]")

        spotweb = out.split("\nspotweb\n", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("NOT_A_VAR", spotweb)
        self.assertRegex(spotweb, r"SOCIALACCOUNT_PROVIDERS\s+secret \(override\)")

    def test_variables_and_summary(self):
        _, out = self.run_discover()
        self.assertRegex(out, r"SMTP_TOKEN\s+secret by name/override, but is_secret=false in Komodo")
        self.assertRegex(out, r"DOMAIN\s+plain  \(used in 2 env keys\)")
        self.assertIn("not Komodo Variables (core/periphery config secrets?): KOMODO_CORE_ONLY, UNDEFINED_VAR", out)

    def test_masked_variables_note(self):
        raw = json.loads((FIXTURES / "list_variables.json").read_text())
        for v in raw:
            if v["is_secret"]:
                v["value"] = "#" * len(v["value"])
        _, out = self.run_discover(raw)
        self.assertIn("--write-secrets needs an admin key", out)


class PlanSecretsTests(TempRepoCase):
    def stacks(self, variables=None):
        client = FakeKomodo(variables)
        return {s.name: s for s in km.load_stacks(km.list_full_stacks(client), load_export(), self.repo)}

    def test_values_match_what_compose_sees(self):
        stacks, variables = self.stacks(), load_vars()
        for name, expected in EXPECTED_SECRETS.items():
            plan = km.plan_secrets(stacks[name], variables, OVERRIDES)
            self.assertEqual(plan.values, expected)
            self.assertEqual(plan.manual, [])
        plan = km.plan_secrets(stacks["spotweb"], variables, OVERRIDES)
        self.assertEqual([r.split(":")[0] for r in plan.review], ["spotweb/API_KEY"])
        self.assertEqual(km.plan_secrets(stacks["wizarr"], variables, OVERRIDES).values, {})

    def test_unresolved_ref_in_secret_is_manual(self):
        stack = self.stacks()["mealie"]
        stack.environment += "EXTRA_TOKEN=[[NOT_A_VARIABLE]]\n"
        ov = km.parse_overrides("secret: [EXTRA_TOKEN]\n")
        plan = km.plan_secrets(stack, load_vars(ov), ov)
        self.assertNotIn("EXTRA_TOKEN", plan.values)
        self.assertTrue(any("EXTRA_TOKEN" in m for m in plan.manual))

    def test_masked_variable_needs_admin(self):
        raw = json.loads((FIXTURES / "list_variables.json").read_text())
        for v in raw:
            if v["is_secret"]:
                v["value"] = "#" * len(v["value"])
        with self.assertRaises(km.KomodoError) as ctx:
            km.plan_secrets(self.stacks(raw)["mealie"], load_vars(raw=raw), OVERRIDES)
        self.assertIn("admin", str(ctx.exception))

    def test_safe_stderr_hides_plaintext(self):
        err = b"Error unmarshalling file: invalid dotenv input line: FAKESECRET-x"
        self.assertNotIn(MARKER, km.safe_stderr(err, ["FAKESECRET-x"]))
        self.assertEqual(km.safe_stderr(b"no matching creation rules", ["FAKESECRET-x"]), "no matching creation rules")


class BuildResourcesTests(TempRepoCase):
    def build(self, **kw):
        return km.build_resources(load_export(), load_vars(), OVERRIDES, self.repo, exclude=["komodo"], **kw)

    def test_stacks(self):
        result = self.build()
        stacks = {s["name"]: s["config"] for s in result.files["stacks.toml"]["stack"]}
        self.assertEqual(sorted(stacks), ["mealie", "spotweb", "wizarr"])

        mealie = stacks["mealie"]
        self.assertEqual(mealie["environment"], "DOMAIN=[[DOMAIN]]\nBASE_URL=https://mealie.[[DOMAIN]]\nALLOW_SIGNUP=false\n")
        self.assertEqual(mealie["compose_cmd_wrapper"], "sops exec-env secrets.sops.env '[[COMPOSE_COMMAND]]'")
        self.assertEqual(mealie["compose_cmd_wrapper_include"], ["up", "pull", "build", "run"])
        self.assertEqual(mealie["pre_deploy"], {"command": "echo pre"})

        self.assertEqual(stacks["spotweb"]["environment"], "SPOTWEB_URL=https://spotweb.test\n")

        wizarr = stacks["wizarr"]
        self.assertNotIn("compose_cmd_wrapper", wizarr)
        self.assertNotIn("webhook_secret", wizarr)
        self.assertEqual(
            wizarr["environment"],
            "TZ=Europe/Amsterdam\nAUTHENTIK_ERROR_REPORTING__ENABLED=false\nCORE_SECRET_REF=[[KOMODO_CORE_ONLY]]\n",
        )
        self.assertTrue(any("MIXED_KEY" in m for m in result.manual))
        self.assertIn("stack wizarr: webhook_secret", result.dropped)
        self.assertTrue(any("stack mealie: has secrets but no secrets.sops.env" in n for n in result.notes))

    def test_parse_error_names_the_resource(self):
        export = load_export()
        export["stack"][1]["config"]["environment"] += "FAKESECRET-no-assignment\n"
        with self.assertRaises(ValueError) as ctx:
            km.build_resources(export, load_vars(), OVERRIDES, self.repo)
        self.assertIn("stack mealie: line", str(ctx.exception))
        self.assertNotIn(MARKER, str(ctx.exception))

    def test_inline_file_contents_gets_a_note(self):
        export = load_export()
        export["stack"][1]["config"]["file_contents"] = "services: {}\n"
        result = km.build_resources(export, load_vars(), OVERRIDES, self.repo)
        self.assertIn("stack mealie: has inline file_contents, check it for secrets by hand", result.notes)

    def test_only_filter(self):
        result = self.build(only=["mealie"])
        self.assertEqual([s["name"] for s in result.files["stacks.toml"]["stack"]], ["mealie"])

    def test_other_resources(self):
        result = self.build()
        files = result.files
        self.assertNotIn("passkey", files["servers.toml"]["server"][0]["config"])
        self.assertNotIn("passkey", files["builders.toml"]["builder"][0]["config"]["params"])
        alerters = {a["name"]: a["config"]["endpoint"]["params"]["url"] for a in files["alerters.toml"]["alerter"]}
        self.assertEqual(alerters, {"discord": "[[KOMODO_ALERTER_DISCORD_URL]]", "gotify": "[[GOTIFY_ALERTER_URL]]"})
        self.assertEqual(
            files["deployments.toml"]["deployment"][0]["config"]["environment"],
            "WHOAMI_NAME=test\nWHOAMI_TOKEN=[[KOMODO_DEPLOYMENT_WHOAMI_WHOAMI_TOKEN]]\n",
        )
        self.assertEqual(files["procedures.toml"], {"procedure": load_export()["procedure"]})
        self.assertEqual(
            [n for n, _ in result.needed_vars],
            ["KOMODO_DEPLOYMENT_WHOAMI_WHOAMI_TOKEN", "KOMODO_ALERTER_DISCORD_URL"],
        )

    def test_variables_are_plain_only(self):
        result = self.build()
        self.assertEqual(
            result.files["variables.toml"]["variable"],
            [
                {"name": "DOCKER_STACKS_DIR", "value": "/srv/docker-stacks"},
                {"name": "DOMAIN", "value": "example.test", "description": "Base domain"},
            ],
        )
        self.assertTrue(any("SMTP_TOKEN" in n and "is_secret=false" in n for n in result.notes))

    def test_no_secret_in_rendered_toml(self):
        result = self.build()
        rendered = {f: km.emit_toml(doc) for f, doc in result.files.items()}
        for fname, text in rendered.items():
            self.assertNotIn(MARKER, text, fname)
        self.assertEqual(km.find_leaks(rendered, result.leak_check), [])

    def test_leak_check_catches_a_leak(self):
        result = self.build()
        rendered = {"stacks.toml": 'x = "FAKESECRET-oidc"', "variables.toml": "y = 'FAKESECRET-shared-db'"}
        self.assertEqual(
            km.find_leaks(rendered, result.leak_check),
            [
                "stacks.toml: value of mealie/OIDC_CLIENT_SECRET",
                # mealie/POSTGRES_PASSWORD resolves to the same Variable value
                "variables.toml: value of mealie/POSTGRES_PASSWORD",
                "variables.toml: value of variable/SHARED_DB_PASSWORD",
            ],
        )


class WriteTomlTests(TempRepoCase):
    def test_writes_files_without_secrets(self):
        out_dir = self.repo / "komodo" / "resources"
        buf = io.StringIO()
        rc = km.write_toml(FakeKomodo(), self.repo, OVERRIDES, out_dir, exclude=["komodo"], out=buf)
        self.assertEqual(rc, 1)  # MIXED_KEY needs manual handling
        self.assertEqual(
            sorted(p.name for p in out_dir.iterdir()),
            ["alerters.toml", "builders.toml", "deployments.toml", "procedures.toml", "repos.toml", "servers.toml", "stacks.toml", "variables.toml"],
        )
        for path in out_dir.iterdir():
            text = path.read_text()
            self.assertNotIn(MARKER, text, path.name)
            self.assertTrue(text.startswith("# Generated by scripts/komodo-migrate.py"))
            tomllib.loads(text)
        self.assertNotIn(MARKER, buf.getvalue())
        self.assertIn("KOMODO_ALERTER_DISCORD_URL  <- alerter discord endpoint URL  [missing]", buf.getvalue())

        buf = io.StringIO()
        self.assertEqual(km.write_toml(FakeKomodo(), self.repo, OVERRIDES, out_dir, exclude=["komodo"], out=buf), 1)
        self.assertIn("Already exists", buf.getvalue())

    def test_refuses_to_write_a_leak(self):
        # A secret Variable's value hardcoded in a key whose name looks plain.
        client = FakeKomodo()
        client.toml = client.toml.replace(
            "ALLOW_SIGNUP=false\n", "ALLOW_SIGNUP=false\nDB_URL_COPY=postgres://u:FAKESECRET-shared-db@db/x\n"
        )
        out_dir = self.repo / "out"
        buf = io.StringIO()
        rc = km.write_toml(client, self.repo, OVERRIDES, out_dir, exclude=["komodo"], out=buf)
        self.assertEqual(rc, 1)
        self.assertIn("stacks.toml: value of variable/SHARED_DB_PASSWORD", buf.getvalue())
        self.assertNotIn(MARKER, buf.getvalue())
        self.assertFalse(out_dir.exists())

        # --allow-match accepts a known false positive
        buf = io.StringIO()
        allow = ["variable/SHARED_DB_PASSWORD", "mealie/POSTGRES_PASSWORD"]  # same value, both labels
        km.write_toml(client, self.repo, OVERRIDES, out_dir, exclude=["komodo"], allow_match=allow, out=buf)
        self.assertTrue((out_dir / "stacks.toml").exists())


def have_sops():
    return shutil.which("sops") and shutil.which("age-keygen")


@unittest.skipUnless(have_sops(), "needs sops and age-keygen on PATH")
class SopsRoundTripTests(TempRepoCase):
    """Encrypt with a throwaway age key, then decrypt through `sops exec-env`
    the same way the Komodo wrapper does."""

    def setUp(self):
        super().setUp()
        key = self.repo / "test.agekey"
        subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
        public = subprocess.run(["age-keygen", "-y", str(key)], check=True, capture_output=True, text=True).stdout.strip()
        # Use the repo's real creation rule, with the throwaway recipient.
        sops_yaml = (REPO_ROOT / ".sops.yaml").read_text()
        (self.repo / ".sops.yaml").write_text(re.sub(r"age1[0-9a-z]+", public, sops_yaml))
        patcher = mock.patch.dict(os.environ, {"SOPS_AGE_KEY_FILE": str(key)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, **kw):
        buf = io.StringIO()
        rc = km.write_secrets(FakeKomodo(), self.repo, OVERRIDES, exclude=["komodo"], out=buf, **kw)
        return rc, buf.getvalue()

    def test_round_trip(self):
        rc, out = self.write(verify=True)
        self.assertEqual(rc, 0, out)
        self.assertNotIn(MARKER, out)
        self.assertIn("mealie: 3 secrets written (POSTGRES_PASSWORD, OIDC_CLIENT_SECRET, SMTP_TOKEN)  round trip ok", out)
        self.assertIn("spotweb: 3 secrets written (DB_PASSWORD, API_KEY, SOCIALACCOUNT_PROVIDERS)  round trip ok", out)
        self.assertIn("spotweb/API_KEY", out.split("REVIEW", 1)[1])
        self.assertFalse((self.repo / "komodo" / km.SECRETS_FILE).exists())
        self.assertFalse((self.repo / "wizarr" / km.SECRETS_FILE).exists())

        for name, expected in EXPECTED_SECRETS.items():
            path = self.repo / name / km.SECRETS_FILE
            data = path.read_text()
            self.assertNotIn(MARKER, data)
            self.assertRegex(data, r"(?m)^sops_mac=")
            self.assertEqual(km.encrypted_keys(data), list(expected))
            self.assertEqual(km.verify_roundtrip(path, expected), [])
            wrong = dict(expected, **{next(iter(expected)): "different"})
            self.assertEqual(km.verify_roundtrip(path, wrong), [next(iter(expected))])

    def test_refuses_overwrite_without_force(self):
        self.write()
        path = self.repo / "mealie" / km.SECRETS_FILE
        before = path.read_bytes()
        rc, out = self.write()
        self.assertIn("Skipped, secrets.sops.env already exists", out)
        self.assertEqual(path.read_bytes(), before)
        rc, out = self.write(force=True)
        self.assertIn("mealie: 3 secrets written", out)
        self.assertNotEqual(path.read_bytes(), before)  # new data key and IVs

    def test_stack_filter(self):
        rc, out = self.write(only=["spotweb"])
        self.assertIn("1 secrets files written", out)
        self.assertFalse((self.repo / "mealie" / km.SECRETS_FILE).exists())


class CliTests(unittest.TestCase):
    def test_missing_env_without_terminal(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("sys.stderr", err), \
                mock.patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(km.main(["--discover", "--repo", str(REPO_ROOT)]), 1)
        self.assertIn("set KOMODO_URL in the environment", err.getvalue())

    def test_stack_lists(self):
        self.assertEqual(km._names(["a,b", " c ", "d,,"]), ["a", "b", "c", "d"])

    def test_prompts_for_missing_settings(self):
        env = {"KOMODO_URL": "https://komodo.example.test/"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("getpass.getpass", side_effect=["FAKESECRET-key", "FAKESECRET-secret"]) as ask:
            client = km.Komodo.from_env()
        self.assertEqual(client.url, "https://komodo.example.test")
        self.assertEqual(client.headers["X-Api-Key"], "FAKESECRET-key")
        self.assertEqual(client.headers["X-Api-Secret"], "FAKESECRET-secret")
        self.assertEqual([c.args[0] for c in ask.call_args_list], ["KOMODO_API_KEY (hidden): ", "KOMODO_API_SECRET (hidden): "])

    def test_prompt_rejects_empty_input(self):
        with mock.patch.dict(os.environ, {"KOMODO_URL": "u", "KOMODO_API_KEY": "k"}, clear=True), \
                mock.patch("sys.stdin.isatty", return_value=True), mock.patch("getpass.getpass", return_value="  "):
            with self.assertRaises(km.KomodoError) as ctx:
                km.Komodo.from_env()
        self.assertEqual(str(ctx.exception), "KOMODO_API_SECRET is empty")


if __name__ == "__main__":
    unittest.main()
