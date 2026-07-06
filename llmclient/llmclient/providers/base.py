from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    """A provider turns (prompt, model, json_mode, system) into response text."""

    def complete(self, prompt: str, *, model: str, json_mode: bool = False,
                 system: str | None = None) -> str:
        ...
