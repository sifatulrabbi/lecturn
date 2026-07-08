"""Chunking engine.

Splits cleaned text into TTS request units that satisfy StepFun's hard cap of
1000 characters per request. Guarantees:

  * Every emitted chunk is at most ``max_chars`` characters long.
  * Chunks never cross chapter boundaries — each chunk belongs to exactly one
    chapter. A chapter only spans multiple chunks when it exceeds ``max_chars``.
  * Splits land on sentence boundaries whenever possible, to preserve context.
    Only when a single sentence alone exceeds the limit does the engine fall
    back to sub-sentence splitting (clause boundaries, then words, then a hard
    character cut as a last resort).

The sentence splitter is a dependency-free heuristic tuned to avoid breaking on
common abbreviations, decimals, and initials.
"""

from __future__ import annotations

import re

from textbook_audiobook.config import HARD_CHAR_LIMIT
from textbook_audiobook.models import Chunk, Document

# Common abbreviations that end in a period but do not end a sentence.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "al",
    "fig", "eq", "no", "vol", "pp", "ch", "sec", "approx", "inc", "ltd",
    "co", "corp", "dept", "univ", "ed", "eds", "trans", "cf", "ibid",
    "e.g", "i.e", "viz", "esp", "min", "max", "avg",
    # Single-letter initials (handled generically below) plus month abbrevs.
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
}

# Candidate sentence terminators followed by whitespace.
_SENTENCE_END = re.compile(r"([.!?][\"')\]]?)(\s+)")

# Sub-sentence clause boundaries, in descending order of preference.
_CLAUSE_BOUNDARY = re.compile(r"([:;])(\s+)")
_COMMA_BOUNDARY = re.compile(r"(,)(\s+)")


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences using a heuristic boundary detector."""

    text = text.strip()
    if not text:
        return []

    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end(1)  # include the terminator + optional closing quote
        candidate = text[start:end]

        if _is_false_boundary(candidate, text, match):
            continue

        sentence = text[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()  # skip the whitespace after the terminator

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)

    return sentences


def _is_false_boundary(candidate: str, text: str, match: re.Match[str]) -> bool:
    """Return True if the period at ``match`` does not actually end a sentence."""

    # Only '.' is ambiguous; '!' and '?' reliably end sentences.
    terminator = match.group(1)[0]
    if terminator != ".":
        return False

    # Grab the token immediately preceding the period.
    word_match = re.search(r"([A-Za-z][A-Za-z.\-]*)\.?$", candidate.rstrip())
    if not word_match:
        return False
    word = word_match.group(1).rstrip(".").lower()

    # Known abbreviation.
    if word in _ABBREVIATIONS:
        return True

    # Single-letter initial, e.g. "J. R. R. Tolkien".
    if len(word) == 1 and word.isalpha():
        return True

    # Decimal number: digit . digit (e.g. "3.14").
    idx = match.start(1)
    if idx > 0 and idx + 1 < len(text):
        if text[idx - 1].isdigit() and text[idx + 1].isdigit():
            return True

    return False


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last-resort: split a too-long fragment that has no usable boundary."""

    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _split_on(
    text: str, pattern: re.Pattern[str], max_chars: int
) -> list[str] | None:
    """Split ``text`` on ``pattern`` boundaries and greedily repack.

    Returns ``None`` if the pattern produced no usable boundary (so the caller
    can try the next strategy).
    """

    parts: list[str] = []
    start = 0
    for m in pattern.finditer(text):
        parts.append(text[start : m.end(1)])
        start = m.end()
    tail = text[start:]
    if tail:
        parts.append(tail)

    if len(parts) <= 1:
        return None

    return _pack(parts, max_chars, recurse=True)


def _split_oversized_sentence(sentence: str, max_chars: int) -> list[str]:
    """Break a single sentence that exceeds ``max_chars`` into safe pieces."""

    for pattern in (_CLAUSE_BOUNDARY, _COMMA_BOUNDARY):
        result = _split_on(sentence, pattern, max_chars)
        if result is not None:
            return result

    # Fall back to word boundaries.
    words = sentence.split(" ")
    if len(words) > 1:
        return _pack(words, max_chars, joiner=" ", recurse=True)

    # No whitespace at all — hard character cut.
    return _hard_split(sentence, max_chars)


def _pack(
    fragments: list[str],
    max_chars: int,
    *,
    joiner: str = " ",
    recurse: bool = False,
) -> list[str]:
    """Greedily combine fragments into chunks of at most ``max_chars``.

    Any single fragment longer than ``max_chars`` is recursively sub-split when
    ``recurse`` is True (used for sentence-level packing); otherwise it is hard
    split.
    """

    chunks: list[str] = []
    current = ""

    for frag in fragments:
        frag = frag.strip()
        if not frag:
            continue

        if len(frag) > max_chars:
            # Flush whatever we have, then break the oversized fragment.
            if current:
                chunks.append(current)
                current = ""
            pieces = (
                _split_oversized_sentence(frag, max_chars)
                if recurse
                else _hard_split(frag, max_chars)
            )
            chunks.extend(pieces)
            continue

        candidate = frag if not current else f"{current}{joiner}{frag}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = frag

    if current:
        chunks.append(current)

    return [c.strip() for c in chunks if c.strip()]


def chunk_text(text: str, max_chars: int = HARD_CHAR_LIMIT) -> list[str]:
    """Split ``text`` into chunks of at most ``max_chars`` on sentence bounds."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    sentences = split_sentences(text)
    if not sentences:
        return []

    return _pack(sentences, max_chars, recurse=True)


def chunk_document(
    document: Document, max_chars: int = HARD_CHAR_LIMIT
) -> list[Chunk]:
    """Produce the ordered list of :class:`Chunk` for the whole document.

    Chunks never cross chapter boundaries.
    """

    if max_chars > HARD_CHAR_LIMIT:
        raise ValueError(
            f"max_chars={max_chars} exceeds StepFun's hard limit of "
            f"{HARD_CHAR_LIMIT} characters per request."
        )

    chunks: list[Chunk] = []
    global_index = 0
    for chapter in document.chapters:
        pieces = chunk_text(chapter.text, max_chars=max_chars)
        for chapter_chunk_index, piece in enumerate(pieces):
            chunks.append(
                Chunk(
                    index=global_index,
                    chapter_index=chapter.index,
                    chapter_chunk_index=chapter_chunk_index,
                    text=piece,
                )
            )
            global_index += 1

    # Defensive invariant: nothing exceeds the hard limit.
    for chunk in chunks:
        assert chunk.char_count <= max_chars, (
            f"Chunk {chunk.index} is {chunk.char_count} chars "
            f"(> {max_chars}); chunker invariant violated."
        )

    return chunks


def chunks_by_chapter(chunks: list[Chunk]) -> dict[int, list[Chunk]]:
    """Group chunks by their chapter index, preserving order."""

    grouped: dict[int, list[Chunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.chapter_index, []).append(chunk)
    return grouped
