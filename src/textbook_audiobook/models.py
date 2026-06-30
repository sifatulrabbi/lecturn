"""Core data structures shared across the pipeline.

These are deliberately plain dataclasses so they are trivial to construct in
loaders, transform in the cleaner/chunker, and consume in the assembler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chapter:
    """A structural unit of the source document.

    A "chapter" is any top-level structural boundary the loader detected
    (a heading in Markdown/EPUB, a delimiter in plain text, a bookmark or
    heuristic break in PDF). Documents without any detectable structure
    collapse to a single chapter spanning the whole text.
    """

    index: int
    title: str
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class Document:
    """A fully-loaded source document, ready for cleaning and chunking."""

    title: str
    author: str
    source_path: Path
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(c.char_count for c in self.chapters)

    @property
    def slug(self) -> str:
        return slugify(self.title)


@dataclass
class Chunk:
    """A single TTS request unit.

    Every chunk belongs to exactly one chapter and is guaranteed by the
    chunker to be at most ``max_chars`` characters long (the StepFun hard
    limit of 1000). ``index`` is the global, zero-based ordering across the
    whole document; ``chapter_chunk_index`` is the order within its chapter.
    """

    index: int
    chapter_index: int
    chapter_chunk_index: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


def slugify(value: str, *, fallback: str = "audiobook") -> str:
    """Produce a filesystem-safe slug for output naming."""

    import re

    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "_", value)
    value = value.strip("_")
    return value or fallback
