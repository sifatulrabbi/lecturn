"""Format loaders and dispatch by file extension."""

from __future__ import annotations

from pathlib import Path

from textbook_audiobook.loaders import (
    epub_loader,
    markdown_loader,
    pdf_loader,
    text_loader,
)
from textbook_audiobook.loaders.base import LoaderError
from textbook_audiobook.models import Document

_EXTENSION_MAP = {
    ".pdf": pdf_loader.load,
    ".epub": epub_loader.load,
    ".md": markdown_loader.load,
    ".markdown": markdown_loader.load,
    ".txt": text_loader.load,
    ".text": text_loader.load,
}

SUPPORTED_EXTENSIONS = tuple(sorted(_EXTENSION_MAP))


def load_document(
    path: Path,
    *,
    title: str | None = None,
    author: str | None = None,
) -> Document:
    """Load ``path`` into a :class:`Document`, dispatching on file extension."""

    if not path.exists():
        raise LoaderError(f"Input file not found: {path}")
    if not path.is_file():
        raise LoaderError(f"Input path is not a file: {path}")

    loader = _EXTENSION_MAP.get(path.suffix.lower())
    if loader is None:
        raise LoaderError(
            f"Unsupported file type '{path.suffix}'. "
            f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}."
        )

    return loader(path, title=title, author=author)


__all__ = [
    "load_document",
    "LoaderError",
    "SUPPORTED_EXTENSIONS",
]
