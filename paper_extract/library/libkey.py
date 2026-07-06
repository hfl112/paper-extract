"""LibKey Nomad support.

The user's institutional access is provided by the LibKey Nomad Chrome extension
(no SSO, no plain proxy). We stage a copy of that extension and load it into the
cloakbrowser (Chromium) persistent context via extension_paths, so LibKey resolves
authenticated full-text links inside the tool's browser too.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..paths import data_root

# LibKey Nomad extension id on the Chrome Web Store.
LIBKEY_EXT_ID = "lkoeejijapdihgbegpljiehpnlkadljb"

_CHROME_EXTENSIONS = Path.home() / "Library/Application Support/Google/Chrome/Default/Extensions"


def _staged_dir() -> Path:
    return data_root() / "library_extensions" / "libkey"


def find_chrome_libkey() -> Path | None:
    """Locate the newest installed LibKey Nomad version dir in the Chrome profile."""
    base = _CHROME_EXTENSIONS / LIBKEY_EXT_ID
    if not base.exists():
        return None
    versions = [d for d in base.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
    if not versions:
        return None
    return sorted(versions, key=lambda d: d.name)[-1]


def stage_extension(force: bool = False) -> Path | None:
    """Copy LibKey out of the Chrome profile to a stable, loadable location.

    Strips Chrome's `_metadata` (its verified_contents.json makes Chromium reject
    an unpacked load). Returns the staged path, or None if LibKey isn't installed.
    Falls back to an already-staged copy if Chrome no longer has it.
    """
    dest = _staged_dir()
    src = find_chrome_libkey()
    if src is None:
        return dest if (dest / "manifest.json").exists() else None
    if force or not (dest / "manifest.json").exists():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        meta = dest / "_metadata"
        if meta.exists():
            shutil.rmtree(meta, ignore_errors=True)
    return dest


def staged_extension() -> Path | None:
    """Return the staged LibKey path if present, else None (no copy attempted)."""
    dest = _staged_dir()
    return dest if (dest / "manifest.json").exists() else None
