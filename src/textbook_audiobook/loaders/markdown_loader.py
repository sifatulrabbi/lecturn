"""Markdown loader.

Treats ``#`` and ``##`` headings as chapter markers (per PLAN.md). Deeper
headings are kept inline as part of the chapter body. Markdown syntax is
lightly stripped here; the cleaner does the heavy normalisation.
"""

from __future__ import annotations

import re
from pathlib import Path

from textbook_audiobook.loaders.base import LoaderError
from textbook_audiobook.models import Chapter, Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# Chapter-level headings are H1/H2 only.
_CHAPTER_LEVEL = 2


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem errors
        raise LoaderError(f"Could not read {path}: {exc}") from exc


def _strip_inline_markdown(text: str) -> str:
    """Remove the most common inline Markdown that would otherwise be read aloud."""

    # Images: ![alt](url) -> alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Links: [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # Inline code / bold / italic markers
    text = re.sub(r"[*_`]{1,3}", "", text)
    # Blockquote / list markers at line start
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}([-*+]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    return text


def load(path: Path, *, title: str | None, author: str | None) -> Document:
    raw = _read(path)

    chapters: list[Chapter] = []
    current_title = ""
    current_lines: list[str] = []
    doc_title: str | None = title
    in_code_fence = False

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body or current_title:
            chapters.append(
                Chapter(index=len(chapters), title=current_title, text=body)
            )

    for line in raw.splitlines():
        if line.strip().startswith("```"):
            in_code_fence = not in_code_fence
            current_lines.append(line)
            continue

        match = None if in_code_fence else _HEADING.match(line)
        if match and len(match.group(1)) <= _CHAPTER_LEVEL:
            heading_text = match.group(2).strip()
            if doc_title is None and len(match.group(1)) == 1:
                doc_title = heading_text
            # Starting a new chapter; flush the accumulated one.
            if current_lines or current_title:
                flush()
            current_title = heading_text
            current_lines = []
        else:
            current_lines.append(line)

    flush()

    if not chapters:
        chapters = [
            Chapter(index=0, title="", text=_strip_inline_markdown(raw).strip())
        ]
    else:
        chapters = [
            Chapter(
                index=ch.index,
                title=ch.title,
                text=_strip_inline_markdown(ch.text).strip(),
            )
            for ch in chapters
        ]

    return Document(
        title=doc_title or path.stem,
        author=author or "Unknown",
        source_path=path,
        chapters=chapters,
    )
