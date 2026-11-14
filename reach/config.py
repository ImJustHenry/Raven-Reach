"""Private config lives OUTSIDE the repo (it holds phone numbers and Twilio keys)."""
import json
import os
from pathlib import Path

PRIVATE = Path.home() / ".config" / "raven-reach" / "config.json"
EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"


def config_path() -> Path:
    env = os.environ.get("RAVEN_REACH_CONFIG")
    if env:
        return Path(env)
    return PRIVATE if PRIVATE.exists() else EXAMPLE


def load_config():
    path = config_path()
    config = json.loads(path.read_text())
    config["_path"] = str(path)
    if config.get("call_mode") == "android_app":
        config["call_mode"] = "iphone_app"  # same Wi-Fi phone-app hand-off for both
    if os.environ.get("RAVEN_REACH_MODE"):
        config["call_mode"] = os.environ["RAVEN_REACH_MODE"]
    if config.get("call_mode", "preview") not in {"preview", "twilio", "iphone_app"}:
        raise ValueError("call_mode must be 'preview', 'iphone_app' or 'twilio'")
    return config


def save_bridge_token(config, token):
    """Persist a token adopted from a phone's QR (private config only)."""
    config.setdefault("phone_bridge", {})["token"] = token
    path = Path(config.get("_path", ""))
    if path == PRIVATE and path.exists():
        data = json.loads(path.read_text())
        data.setdefault("phone_bridge", {})["token"] = token
        path.write_text(json.dumps(data, indent=2))


def ensure_bridge_token(config):
    """Return the phone-GPS pairing token, creating and saving one if needed."""
    import secrets
    bridge = config.setdefault("phone_bridge", {})
    token = str(bridge.get("token") or "")
    if len(token) >= 6:
        return token
    token = secrets.token_hex(4)
    bridge["token"] = token
    path = Path(config.get("_path", ""))
    if path == PRIVATE and path.exists():  # never write secrets into the repo's example file
        data = json.loads(path.read_text())
        data.setdefault("phone_bridge", {})["token"] = token
        path.write_text(json.dumps(data, indent=2))
    return token
