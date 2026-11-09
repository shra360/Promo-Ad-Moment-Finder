"""Load config.yaml, then let environment variables override the model settings,
so a different provider or model needs no code change."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

PROVIDER_DEFAULTS = {
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"]},
    "anthropic": {"base_url": None, "key_env": ["ANTHROPIC_API_KEY"]},
    "openai_compat": {"base_url": None, "key_env": ["LLM_API_KEY", "OPENAI_API_KEY"]},
    "mock": {"base_url": None, "key_env": []},
    "external": {"base_url": None, "key_env": []},     # answers come from files (any model, agent or person)
}


def load_dotenv(paths=None) -> None:
    """Read KEY=VALUE lines from .env (project folder, then the current folder).
    Values already set in the environment win. Nothing is printed or logged."""
    for f in paths or [DEFAULT_PATH.parent / ".env", Path.cwd() / ".env"]:
        try:
            lines = Path(f).read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip().strip('"').strip("'")
            if k and v and not os.getenv(k):
                os.environ[k] = v


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    load_dotenv()
    cfg = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    llm = cfg.setdefault("llm", {})
    env = {
        "provider": os.getenv("LLM_PROVIDER"),
        "model": os.getenv("LLM_MODEL"),
        "judge_model": os.getenv("LLM_JUDGE_MODEL"),
        "base_url": os.getenv("LLM_BASE_URL"),
    }
    for k, v in env.items():
        if v:
            llm[k] = v
    for section, values in (overrides or {}).items():
        cfg.setdefault(section, {}).update({k: v for k, v in values.items() if v is not None})
    prov = PROVIDER_DEFAULTS.get(llm.get("provider", ""), {})
    if not llm.get("base_url"):
        llm["base_url"] = prov.get("base_url")
    llm["api_key"] = os.getenv("LLM_API_KEY") or next(
        (os.getenv(k) for k in prov.get("key_env", []) if os.getenv(k)), None)
    return cfg


def public_view(cfg: dict) -> dict:
    """Config as written to the run manifest: never includes the key."""
    out = copy.deepcopy(cfg)
    out["llm"].pop("api_key", None)
    out["llm"]["api_key_set"] = bool(cfg["llm"].get("api_key"))
    return out
