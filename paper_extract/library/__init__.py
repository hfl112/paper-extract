"""Institutional-library (EZProxy) access.

Step 2 ships hooks that report 'not configured'; Step 3 wires the real
persistent-browser + cookie routes in browser.py.
"""
from __future__ import annotations

from typing import Any


def begin_session(landing_url: str | None = None) -> bool:
    """Open one browser and let the user establish a live access session (option A)."""
    try:
        from .browser import begin_live_session
    except Exception:
        return False
    return begin_live_session(landing_url)


def doctor() -> dict:
    """Read-only library-access readiness check (never opens a browser or waits)."""
    from .browser import doctor as _doctor
    return _doctor()


def prepare_session(interactive: bool) -> tuple[bool, str]:
    """Get ready for a library fetch batch.

    Interactive (a real terminal): open the browser once and let the user make the
    session live. Non-interactive (agent/no TTY): never open a blocking prompt —
    require an already-configured session and fail fast with guidance otherwise.
    Returns (ok, message).
    """
    from .browser import begin_live_session, set_interactive

    set_interactive(interactive)
    if interactive:
        ok = begin_live_session()
        return (True, "") if ok else (False, "library browser unavailable; install with pip install \".[browser]\"")
    d = doctor()
    if d.get("ready"):
        return True, ""
    return False, (f"library access not ready ({d.get('reason')}). {d.get('next_action')}. "
                   "Non-interactive fetch cannot log in for you.")


def fetch_json_library(store, article: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    try:
        from .browser import fetch_json_library as _impl
    except Exception:
        return None, "library_not_configured"
    return _impl(store, article)


def fetch_pdf_library(store, article: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    try:
        from .browser import fetch_pdf_library as _impl
    except Exception:
        return None, "library_not_configured"
    return _impl(store, article)
