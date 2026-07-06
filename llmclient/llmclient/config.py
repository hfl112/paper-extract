"""Provider / model / key resolution.

Everything is read from environment variables (optionally seeded from a .env in
the current working directory or PROJECT root). No SDK is imported here.

Env vars:
    LLM_PROVIDER            default provider (gemini|openai|deepseek|claude)
    GEMINI_API_KEY[_2..]    Gemini keys (multi-key pool, round-robined)
    OPENAI_API_KEY
    DEEPSEEK_API_KEY
    ANTHROPIC_API_KEY       (Claude)
    <PROVIDER>_MODEL_<LEVEL> optional model override, e.g. OPENAI_MODEL_STANDARD
"""
from __future__ import annotations

import os

PROVIDERS = ("gemini", "openai", "deepseek", "claude")
LEVELS = ("lite", "standard", "pro")

# Default model routing per provider/level. First entry is preferred; providers
# lazily fall back if the SDK reports a model is unavailable.
MODEL_ROUTING: dict[str, dict[str, list[str]]] = {
    "gemini": {
        "lite": ["gemini-2.5-flash-lite", "gemini-flash-lite-latest"],
        "standard": ["gemini-2.5-flash", "gemini-flash-latest"],
        "pro": ["gemini-2.5-pro", "gemini-pro-latest"],
    },
    "openai": {
        "lite": ["gpt-4o-mini"],
        "standard": ["gpt-4o"],
        "pro": ["gpt-4o"],
    },
    "deepseek": {
        "lite": ["deepseek-chat"],
        "standard": ["deepseek-chat"],
        "pro": ["deepseek-reasoner"],
    },
    "claude": {
        "lite": ["claude-haiku-4-5-20251001"],
        "standard": ["claude-sonnet-5"],
        "pro": ["claude-opus-4-8"],
    },
}

_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _maybe_load_dotenv() -> None:
    """Best-effort: load a .env from CWD without a hard dependency on python-dotenv."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


def gemini_keys() -> list[str]:
    _maybe_load_dotenv()
    keys: list[str] = []
    for i in range(1, 20):
        name = "GEMINI_API_KEY" if i == 1 else f"GEMINI_API_KEY_{i}"
        val = os.environ.get(name)
        if val and val not in keys:
            keys.append(val)
    return keys


def api_key(provider: str) -> str:
    _maybe_load_dotenv()
    if provider == "gemini":
        keys = gemini_keys()
        return keys[0] if keys else ""
    return os.environ.get(_KEY_ENV.get(provider, ""), "")


def has_key(provider: str) -> bool:
    return bool(api_key(provider))


def resolve_provider(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit.lower()
    env = os.environ.get("LLM_PROVIDER")
    if env:
        return env.lower()
    for p in PROVIDERS:
        if has_key(p):
            return p
    return None


def resolve_models(provider: str, level: str, explicit: str | None = None) -> list[str]:
    if explicit:
        return [explicit]
    override = os.environ.get(f"{provider.upper()}_MODEL_{level.upper()}")
    if override:
        return [override]
    table = MODEL_ROUTING.get(provider, {})
    return table.get(level) or table.get("standard") or []


def deepseek_base_url() -> str:
    return os.environ.get("DEEPSEEK_BASE_URL", _DEEPSEEK_BASE_URL)
