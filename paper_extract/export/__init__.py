from pathlib import Path

from ..collection import CollectionStore
from .bib import export_bib
from .csv_export import export_csv
from .jsonl import export_jsonl
from .ris import export_ris

__all__ = ["export_bib", "export_ris", "export_csv", "export_jsonl", "export_collection"]

_EXPORTERS = {
    "bib": export_bib,
    "ris": export_ris,
    "csv": export_csv,
    "jsonl": export_jsonl,
}


def export_collection(store: CollectionStore, to: str, output: str | None = None) -> Path:
    if to not in _EXPORTERS:
        raise ValueError(f"Unsupported export format: {to} (choose from {', '.join(_EXPORTERS)})")
    return _EXPORTERS[to](store, output)
