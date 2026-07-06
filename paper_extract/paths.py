from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


_MARKERS = ("pyproject.toml",)


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Resolve the project root independent of the current working directory.

    Priority:
    1. PAPER_EXTRACT_ROOT environment variable, if set.
    2. Nearest ancestor of this file that contains a marker (pyproject.toml).
    3. Fallback to the package parent directory.
    """
    env = os.environ.get("PAPER_EXTRACT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if any((parent / marker).exists() for marker in _MARKERS):
            return parent
    return here.parent.parent


def data_root() -> Path:
    return project_root() / "data"


def collections_root() -> Path:
    return data_root() / "collections"


def library_config_path() -> Path:
    """Location of the (gitignored) institutional-library config."""
    return data_root() / "library.json"
