"""Compatibility wrapper for the bundled llmclient package."""

from .llmclient import (
    LLMError,
    NoProviderConfigured,
    ProviderUnavailable,
    available_providers,
    call_llm,
)

__all__ = [
    "call_llm",
    "available_providers",
    "LLMError",
    "NoProviderConfigured",
    "ProviderUnavailable",
]
