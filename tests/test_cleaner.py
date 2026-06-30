"""Tests for the text cleaner."""

from __future__ import annotations

from pathlib import Path

from textbook_audiobook.cleaner import clean_document, clean_text
from textbook_audiobook.models import Chapter, Document


def test_removes_page_number_lines():
    text = "Real content here.\n42\nMore content here."
    cleaned = clean_text(text)
    assert "42" not in cleaned.split()
    assert "Real content here." in cleaned
    assert "More content here." in cleaned


def test_repairs_hyphenation():
    text = "This is an exam-\nple of hyphenation."
    cleaned = clean_text(text)
    assert "example" in cleaned
    assert "exam-" not in cleaned


def test_strips_urls():
    text = "Visit https://example.com/page for details."
    cleaned = clean_text(text)
    assert "http" not in cleaned


def test_joins_wrapped_lines_within_paragraph():
    text = "This sentence was\nwrapped across lines."
    cleaned = clean_text(text)
    assert cleaned == "This sentence was wrapped across lines."


def test_preserves_paragraph_breaks():
    text = "Paragraph one.\n\nParagraph two."
    cleaned = clean_text(text)
    assert "\n\n" in cleaned


def test_removes_running_headers():
    # A short line repeated across many "pages" should be dropped.
    header = "CHAPTER 1 — INTRODUCTION"
    chapters = [
        Chapter(index=i, title="", text=f"{header}\nBody text {i} here.")
        for i in range(6)
    ]
    doc = Document(title="T", author="A", source_path=Path("x.txt"), chapters=chapters)
    cleaned = clean_document(doc)
    for ch in cleaned.chapters:
        assert header not in ch.text


def test_clean_document_keeps_chapter_count_when_nonempty():
    doc = Document(
        title="T", author="A", source_path=Path("x.txt"),
        chapters=[
            Chapter(index=0, title="A", text="Content alpha."),
            Chapter(index=1, title="B", text="Content beta."),
        ],
    )
    cleaned = clean_document(doc)
    assert len(cleaned.chapters) == 2
    assert [c.index for c in cleaned.chapters] == [0, 1]
