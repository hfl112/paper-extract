class LLMError(Exception):
    """Base class for llmclient errors."""


class NoProviderConfigured(LLMError):
    """No provider could be resolved (no explicit choice and no API key found)."""


class ProviderUnavailable(LLMError):
    """The chosen provider's SDK is not installed or its key is missing."""
