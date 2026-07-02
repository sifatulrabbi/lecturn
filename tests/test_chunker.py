"""Tests for the chunking engine — the deterministic core of the pipeline."""

from __future__ import annotations

import pytest

from textbook_audiobook.chunker import (
    DEFAULT_MAX_CHARS as HARD_CHAR_LIMIT,
    chunk_document,
    chunk_text,
    chunks_by_chapter,
    split_sentences,
)
from textbook_audiobook.models import Chapter, Document


def test_split_sentences_basic():
    text = "Hello world. How are you? I am fine!"
    assert split_sentences(text) == [
        "Hello world.",
        "How are you?",
        "I am fine!",
    ]


def test_split_sentences_respects_abbreviations():
    text = "Dr. Smith met Mr. Jones at 3 p.m. They talked."
    sentences = split_sentences(text)
    # "Dr." and "Mr." must not create sentence breaks.
    assert sentences[0].startswith("Dr. Smith met Mr. Jones")
    assert len(sentences) == 2


def test_split_sentences_decimals_and_initials():
    text = "The value is 3.14 exactly. J. R. R. Tolkien wrote it."
    sentences = split_sentences(text)
    assert len(sentences) == 2
    assert "3.14" in sentences[0]
    assert "J. R. R. Tolkien" in sentences[1]


def test_chunk_text_never_exceeds_limit():
    sentence = "This is a fairly ordinary sentence used for testing. "
    text = sentence * 200  # ~10k+ chars
    chunks = chunk_text(text, max_chars=HARD_CHAR_LIMIT)
    assert chunks
    assert all(len(c) <= HARD_CHAR_LIMIT for c in chunks)


def test_chunk_text_splits_on_sentence_boundaries():
    text = "First sentence here. Second sentence here. Third one now."
    chunks = chunk_text(text, max_chars=40)
    # Each chunk should end with terminal punctuation (boundary-respecting).
    assert all(c.rstrip().endswith((".", "!", "?")) for c in chunks)


def test_oversized_single_sentence_is_split():
    # One sentence with no internal sentence boundary, longer than the limit.
    long_sentence = "word " * 500 + "end."  # ~2500 chars, no '.' until the end
    chunks = chunk_text(long_sentence, max_chars=HARD_CHAR_LIMIT)
    assert all(len(c) <= HARD_CHAR_LIMIT for c in chunks)
    assert len(chunks) > 1


def test_oversized_sentence_with_no_spaces_hard_cut():
    blob = "x" * 2500
    chunks = chunk_text(blob, max_chars=HARD_CHAR_LIMIT)
    assert all(len(c) <= HARD_CHAR_LIMIT for c in chunks)
    assert "".join(chunks) == blob


def test_chunk_document_never_crosses_chapters():
    doc = Document(
        title="T",
        author="A",
        source_path=__import__("pathlib").Path("x.txt"),
        chapters=[
            Chapter(index=0, title="One", text="Alpha sentence. Beta sentence."),
            Chapter(index=1, title="Two", text="Gamma sentence. Delta sentence."),
        ],
    )
    chunks = chunk_document(doc, max_chars=HARD_CHAR_LIMIT)
    grouped = chunks_by_chapter(chunks)
    assert set(grouped) == {0, 1}
    # No chunk text from chapter 0 leaks into chapter 1 and vice versa.
    for chunk in grouped[0]:
        assert "Gamma" not in chunk.text and "Delta" not in chunk.text
    for chunk in grouped[1]:
        assert "Alpha" not in chunk.text and "Beta" not in chunk.text


def test_chunk_indices_are_sequential_and_global():
    doc = Document(
        title="T",
        author="A",
        source_path=__import__("pathlib").Path("x.txt"),
        chapters=[
            Chapter(index=0, title="One", text="A. B. C."),
            Chapter(index=1, title="Two", text="D. E. F."),
        ],
    )
    chunks = chunk_document(doc, max_chars=10)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # Per-chapter index resets.
    first_chapter = [c for c in chunks if c.chapter_index == 0]
    assert first_chapter[0].chapter_chunk_index == 0


def test_chunk_document_rejects_over_hard_limit():
    doc = Document(
        title="T", author="A",
        source_path=__import__("pathlib").Path("x.txt"),
        chapters=[Chapter(index=0, title="", text="hi.")],
    )
    # With an explicit provider hard_limit, max_chars may not exceed it.
    with pytest.raises(ValueError):
        chunk_document(
            doc, max_chars=HARD_CHAR_LIMIT + 1, hard_limit=HARD_CHAR_LIMIT
        )
