"""Collection package.

`CollectionStore` is exported lazily (PEP 562) so that importing the leaf
submodule `collection.ids` — which the Article module depends on — does not
eagerly pull in `store` (and through it `article`), which would form an
import cycle.
"""
__all__ = ["CollectionStore"]


def __getattr__(name):
    if name == "CollectionStore":
        from .store import CollectionStore
        return CollectionStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
