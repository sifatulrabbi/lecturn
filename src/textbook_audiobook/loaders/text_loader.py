"""Plain-text loader.

Plain text has no inherent structure, so chapters are detected via an
explicit delimiter. By default a line containing only ``---`` (a horizontal
rule) separates chapters, matching the convention in PLAN.md. If no delimiter
is present the whole file becomes a single chapter.
"""

from __future__ import annotations

import re
from pathlib import Path

from textbook_audiobook.loaders.base import LoaderError
from textbook_audiobook.models import Chapter, Document

# A line that is only dashes (3+), optionally surrounded by whitespace.
_DELIMITER = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fall back to a permissive decode for non-UTF-8 sources.
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - filesystem errors
        raise LoaderError(f"Could not read {path}: {exc}") from exc


def _derive_title(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return path.stem


def load(path: Path, *, title: str | None, author: str | None) -> Document:
    text = _read(path)

    segments = _DELIMITER.split(text)
    segments = [s.strip() for s in segments if s.strip()]

    if len(segments) <= 1:
        chapters = [Chapter(index=0, title="", text=text.strip())]
    else:
        chapters = [
            Chapter(index=i, title=f"Section {i + 1}", text=seg)
            for i, seg in enumerate(segments)
        ]

    return Document(
        title=title or _derive_title(text, path),
        author=author or "Unknown",
        source_path=path,
        chapters=chapters,
    )
