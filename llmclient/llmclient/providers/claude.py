from __future__ import annotations

from .. import config
from ..errors import ProviderUnavailable

_JSON_NUDGE = "\n\nReturn ONLY valid JSON with no prose and no code fences."


def complete(prompt: str, *, model: str, json_mode: bool = False, system: str | None = None) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise ProviderUnavailable("anthropic SDK not installed (pip install anthropic)") from e

    key = config.api_key("claude")
    if not key:
        raise ProviderUnavailable("No ANTHROPIC_API_KEY found")
    client = anthropic.Anthropic(api_key=key)

    # Claude has no response_format flag; nudge JSON via the prompt/system instead.
    user_prompt = prompt + (_JSON_NUDGE if json_mode else "")
    kwargs = {"model": model, "max_tokens": 4096,
              "messages": [{"role": "user", "content": user_prompt}]}
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return "".join(parts)
