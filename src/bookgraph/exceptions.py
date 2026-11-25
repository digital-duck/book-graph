from __future__ import annotations


class BookError(Exception):
    """A book spec, chapter graph or path that is not usable. Raised before any model call."""
