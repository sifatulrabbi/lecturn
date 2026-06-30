"""Loader base types and dispatch helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from textbook_audiobook.models import Document


class LoaderError(RuntimeError):
    """Raised when a source file cannot be loaded into a Document."""


class Loader(Protocol):
    """A format-specific loader.

    Each loader takes a path and produces a :class:`Document`. Loaders should
    preserve structural cues (chapter headings, section breaks) as chapter
    boundaries, but should NOT perform cleaning — that is the cleaner's job.
    """

    def load(self, path: Path, *, title: str | None, author: str | None) -> Document:
        ...
