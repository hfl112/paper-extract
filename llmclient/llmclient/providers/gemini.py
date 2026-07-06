from __future__ import annotations

import itertools

from .. import config
from ..errors import ProviderUnavailable


_key_cycle = None


def _next_key() -> str:
    global _key_cycle
    keys = config.gemini_keys()
    if not keys:
        raise ProviderUnavailable("No GEMINI_API_KEY found in environment/.env")
    if _key_cycle is None:
        _key_cycle = itertools.cycle(range(len(keys)))
    return keys[next(_key_cycle) % len(keys)]


def complete(prompt: str, *, model: str, json_mode: bool = False, system: str | None = None) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise ProviderUnavailable("google-genai not installed (pip install google-genai)") from e

    client = genai.Client(api_key=_next_key())
    cfg_kwargs = {}
    if system:
        cfg_kwargs["system_instruction"] = system
    if json_mode:
        cfg_kwargs["response_mime_type"] = "application/json"
    resp = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None,
    )
    return resp.text or ""
