"""OpenAI-compatible provider — serves both `openai` and `deepseek`.

DeepSeek exposes an OpenAI-compatible API, so the only differences are the API
key and the base_url.
"""
from __future__ import annotations

from .. import config
from ..errors import ProviderUnavailable


def _client(provider: str):
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ProviderUnavailable("openai SDK not installed (pip install openai)") from e
    key = config.api_key(provider)
    if not key:
        raise ProviderUnavailable(f"No API key for {provider}")
    if provider == "deepseek":
        return OpenAI(api_key=key, base_url=config.deepseek_base_url())
    return OpenAI(api_key=key)


def make(provider: str):
    def complete(prompt: str, *, model: str, json_mode: bool = False, system: str | None = None) -> str:
        client = _client(provider)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs = {"model": model, "messages": messages}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    return complete
