"""llmclient — a tiny provider-agnostic LLM client.

    from llmclient import call_llm
    text = call_llm("Say hi", provider="gemini")            # explicit
    data = call_llm(prompt, json=True)                        # auto-pick provider, JSON out

Provider is chosen by: explicit arg > $LLM_PROVIDER > first provider with an API key.
Model is chosen by: explicit arg > $<PROVIDER>_MODEL_<LEVEL> > built-in routing table.
SDKs are imported lazily, so importing this package never requires any of them.
"""
from __future__ import annotations

from . import config
from .errors import LLMError, NoProviderConfigured, ProviderUnavailable

__version__ = "0.1.0"
__all__ = ["call_llm", "available_providers", "LLMError", "NoProviderConfigured", "ProviderUnavailable"]


def available_providers() -> list[str]:
    return [p for p in config.PROVIDERS if config.has_key(p)]


def _dispatch(provider: str):
    if provider == "gemini":
        from .providers import gemini
        return gemini.complete
    if provider in ("openai", "deepseek"):
        from .providers import openai_compat
        return openai_compat.make(provider)
    if provider == "claude":
        from .providers import claude
        return claude.complete
    raise NoProviderConfigured(f"Unknown provider: {provider}")


def call_llm(
    prompt: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    json: bool = False,
    task_level: str = "standard",
    system: str | None = None,
) -> str:
    """Send a prompt to an LLM and return the response text.

    Raises NoProviderConfigured if no provider can be resolved, or
    ProviderUnavailable if the chosen provider's SDK/key is missing.
    """
    chosen = config.resolve_provider(provider)
    if not chosen:
        raise NoProviderConfigured(
            "No LLM provider configured. Set LLM_PROVIDER and an API key "
            "(GEMINI_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY / ANTHROPIC_API_KEY)."
        )
    if chosen not in config.PROVIDERS:
        raise NoProviderConfigured(f"Unknown provider: {chosen}")

    complete = _dispatch(chosen)
    models = config.resolve_models(chosen, task_level, model)
    if not models:
        raise LLMError(f"No model resolved for provider={chosen} level={task_level}")

    last_err: Exception | None = None
    for m in models:
        try:
            return complete(prompt, model=m, json_mode=json, system=system)
        except ProviderUnavailable:
            raise
        except Exception as e:  # try next model in the routing fallback chain
            last_err = e
    raise LLMError(f"All models failed for provider={chosen}: {last_err}")
