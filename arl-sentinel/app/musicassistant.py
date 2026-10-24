"""Push a new Deezer ARL into Music Assistant's Deezer provider.

Music Assistant stores the ARL as a SECURE_STRING inside its provider config,
encrypted at rest with a per-install Fernet key, so the config file cannot be
edited from the outside. Instead this module drives the JSON-RPC API on
`POST /api` (Music Assistant 2.10+), which needs a long-lived token:

    Settings -> (your user) -> Long-lived tokens -> create

Since 2.10 the ARL lives in the provider's encrypted `setup_data`, which is only
writable through the reconfigure setup flow:

    config/providers            -> find the deezer instance_id
    config/providers/reconfigure -> start the flow, get a FORM step
    config/flows/submit          -> submit {"arl_token": <arl>}

Accounts with Deezer Family get a second "profile" form after the ARL; it is
submitted with its preselected value so the current profile is kept.

Music Assistant reloads the provider itself, so no container restart is needed.
Older instances that keep the ARL in plain config values are handled by falling
back to `config/providers/save`.
"""

import time
import uuid

import requests

DEEZER_DOMAIN = "deezer"
CONF_ARL_TOKEN = "arl_token"
# step_id of the ARL form; it is served again (with errors) when Deezer refuses the ARL
ARL_STEP_ID = "user"
# How long to keep polling while the flow reports a PROGRESS step.
FLOW_POLL_TIMEOUT_SECONDS = 60
FLOW_POLL_INTERVAL_SECONDS = 2


class MusicAssistantError(RuntimeError):
    """Raised when Music Assistant rejects a command or the setup flow fails."""


def api_command(base_url: str, token: str, command: str, args: dict | None = None):
    resp = requests.post(
        f"{base_url}/api",
        headers={"Authorization": f"Bearer {token}"},
        json={"message_id": uuid.uuid4().hex, "command": command, "args": args or {}},
        timeout=60,
    )
    if resp.status_code != 200:
        raise MusicAssistantError(f"{command} failed: HTTP {resp.status_code} {resp.text.strip()[:300]}")
    if not resp.content:
        return None
    return resp.json()


def get_deezer_config(base_url: str, token: str) -> dict:
    configs = api_command(base_url, token, "config/providers", {"provider_domain": DEEZER_DOMAIN}) or []
    if not configs:
        raise MusicAssistantError("no Deezer provider configured in Music Assistant")
    return configs[0]


def get_musicassistant_deezer_error(base_url: str, token: str) -> str | None:
    """Return the Deezer provider's last error (e.g. a rejected ARL), or None if healthy."""
    return get_deezer_config(base_url.rstrip("/"), token).get("last_error") or None


def _flow_error(step: dict) -> str:
    errors = step.get("errors") or {}
    if errors:
        return "; ".join(f"{key}: {value}" for key, value in errors.items())
    return step.get("reason") or "unknown error"


def _current_form_values(step: dict) -> dict:
    """Submit a form unchanged: every entry keeps its current (or default) value."""
    values = {}
    for entry in step.get("entries") or []:
        value = entry.get("value")
        values[entry["key"]] = entry.get("default_value") if value is None else value
    return values


def _run_reconfigure_flow(base_url: str, token: str, step: dict, arl: str) -> None:
    deadline = time.time() + FLOW_POLL_TIMEOUT_SECONDS
    arl_submitted = False

    while True:
        step_type = step.get("type")

        if step_type == "finish":
            return
        if step_type == "abort":
            raise MusicAssistantError(f"reconfigure flow aborted: {_flow_error(step)}")
        if step_type == "external":
            raise MusicAssistantError("reconfigure flow wants an external (OAuth) step, cannot automate")

        if step_type == "form":
            if step.get("step_id") == ARL_STEP_ID:
                if arl_submitted:
                    # The ARL form came back instead of moving on: Deezer refused the ARL.
                    raise MusicAssistantError(f"Music Assistant rejected the ARL: {_flow_error(step)}")
                values = {CONF_ARL_TOKEN: arl}
                arl_submitted = True
            elif step.get("errors"):
                raise MusicAssistantError(f"reconfigure step {step.get('step_id')!r} failed: {_flow_error(step)}")
            else:
                # Follow-up form (e.g. the Deezer Family profile picker): keep what is set.
                values = _current_form_values(step)
            step = api_command(
                base_url,
                token,
                "config/flows/submit",
                {"flow_id": step["flow_id"], "values": values},
            )
            continue

        # progress (or anything unexpected): poll until the flow moves on
        if time.time() > deadline:
            raise MusicAssistantError("reconfigure flow timed out")
        time.sleep(FLOW_POLL_INTERVAL_SECONDS)
        step = api_command(base_url, token, "config/flows/get", {"flow_id": step["flow_id"]})


def set_musicassistant_arl(base_url: str, token: str, arl: str) -> None:
    """Write the ARL into Music Assistant's Deezer provider and reload it."""
    base_url = base_url.rstrip("/")
    instance_id = get_deezer_config(base_url, token)["instance_id"]

    try:
        step = api_command(
            base_url, token, "config/providers/reconfigure", {"instance_id": instance_id}
        )
    except MusicAssistantError as exc:
        # Only pre-2.10 instances lack the command; any other error (auth, scope,
        # server error) must surface instead of being masked by a no-op save.
        if "Invalid Command" not in str(exc):
            raise
        # Pre-2.10 instances keep the ARL in plain config values and have no
        # reconfigure flow - write the value directly instead.
        api_command(
            base_url,
            token,
            "config/providers/save",
            {
                "provider_domain": DEEZER_DOMAIN,
                "instance_id": instance_id,
                "values": {CONF_ARL_TOKEN: arl},
            },
        )
        return

    try:
        _run_reconfigure_flow(base_url, token, step, arl)
    except Exception:
        # Do not leave a half-finished flow behind; it would block the next attempt.
        if step.get("flow_id"):
            try:
                api_command(base_url, token, "config/flows/abort", {"flow_id": step["flow_id"]})
            except Exception:
                pass
        raise
