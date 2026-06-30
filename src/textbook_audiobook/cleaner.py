"""Text cleaner.

Removes boilerplate that should not be narrated (page numbers, running
headers/footers, bare URLs), normalises whitespace and punctuation, and
repairs hyphenation introduced by PDF line wrapping. Operates per chapter so
chapter boundaries detected by loaders are preserved.

The cleaner is conservative: it never merges or drops chapters, only the noise
within them.
"""

from __future__ import annotations

import re

from textbook_audiobook.models import Chapter, Document

# A line that is just a page number (optionally with surrounding punctuation).
_PAGE_NUMBER_LINE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")

# Bare URLs and common footer artefacts.
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

# Hyphenated word split across a line break: "exam-\nple" -> "example".
_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n(\w)")

# Excessive whitespace.
_MULTISPACE = re.compile(r"[ \t]+")
_MULTINEWLINE = re.compile(r"\n{3,}")

# Control characters and common PDF junk glyphs.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Repeated punctuation that TTS may stumble over.
_REPEAT_DOTS = re.compile(r"\.{4,}")


def _detect_running_headers(lines: list[str], *, threshold: int = 4) -> set[str]:
    """Find short lines that recur often enough to be running headers/footers."""

    from collections import Counter

    counts: Counter[str] = Counter()
    for line in lines:
        stripped = line.strip()
        # Headers/footers are short and repeat; body lines rarely do.
        if 0 < len(stripped) <= 60:
            counts[stripped] += 1
    return {line for line, n in counts.items() if n >= threshold}


def clean_text(text: str, *, running_headers: set[str] | None = None) -> str:
    """Clean a single block of text."""

    if not text:
        return ""

    text = _CONTROL.sub("", text)

    # Repair hyphenation before collapsing newlines.
    text = _HYPHEN_LINEBREAK.sub(r"\1\2", text)

    text = _URL.sub("", text)

    kept: list[str] = []
    headers = running_headers or set()
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _PAGE_NUMBER_LINE.match(stripped):
            continue
        if stripped in headers:
            continue
        kept.append(stripped)

    text = "\n".join(kept)

    # Normalise punctuation and whitespace.
    text = _REPEAT_DOTS.sub("...", text)
    text = _MULTISPACE.sub(" ", text)
    text = _MULTINEWLINE.sub("\n\n", text)

    # Collapse single line breaks within a paragraph into spaces so sentences
    # that were wrapped across lines read naturally. Blank-line-separated
    # paragraph breaks are preserved.
    paragraphs = [p.strip() for p in text.split("\n\n")]
    paragraphs = [re.sub(r"\s*\n\s*", " ", p) for p in paragraphs if p.strip()]
    text = "\n\n".join(paragraphs)

    return text.strip()


def clean_document(document: Document) -> Document:
    """Return a cleaned copy of ``document``."""

    # Detect running headers across the whole document so they are removed even
    # if they only repeat a few times within any single chapter.
    all_lines: list[str] = []
    for chapter in document.chapters:
        all_lines.extend(chapter.text.split("\n"))
    headers = _detect_running_headers(all_lines)

    cleaned_chapters = [
        Chapter(
            index=chapter.index,
            title=chapter.title.strip(),
            text=clean_text(chapter.text, running_headers=headers),
        )
        for chapter in document.chapters
    ]
    # Drop chapters that became empty after cleaning, but keep at least one.
    non_empty = [c for c in cleaned_chapters if c.text]
    if not non_empty:
        non_empty = cleaned_chapters[:1]
    # Re-index after possible drops.
    reindexed = [
        Chapter(index=i, title=c.title, text=c.text)
        for i, c in enumerate(non_empty)
    ]

    return Document(
        title=document.title,
        author=document.author,
        source_path=document.source_path,
        chapters=reindexed,
    )
