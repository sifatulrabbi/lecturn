"""Tests for the dependency-free loaders (Markdown and plain text)."""

from __future__ import annotations

from textbook_audiobook.loaders import load_document, SUPPORTED_EXTENSIONS
from textbook_audiobook.loaders.base import LoaderError


def test_markdown_headings_become_chapters(tmp_path):
    md = tmp_path / "book.md"
    md.write_text(
        "# My Book\n\nIntro paragraph.\n\n## Chapter One\n\nText one.\n\n"
        "## Chapter Two\n\nText two.\n",
        encoding="utf-8",
    )
    doc = load_document(md)
    titles = [c.title for c in doc.chapters]
    assert "Chapter One" in titles
    assert "Chapter Two" in titles
    assert doc.title == "My Book"


def test_markdown_strips_inline_syntax(tmp_path):
    md = tmp_path / "b.md"
    md.write_text("# T\n\nThis is **bold** and a [link](http://x.com).\n", "utf-8")
    doc = load_document(md)
    body = " ".join(c.text for c in doc.chapters)
    assert "**" not in body
    assert "link" in body
    assert "http" not in body


def test_plain_text_delimiter_splits_sections(tmp_path):
    txt = tmp_path / "b.txt"
    txt.write_text("Section A body.\n\n---\n\nSection B body.\n", "utf-8")
    doc = load_document(txt)
    assert len(doc.chapters) == 2


def test_plain_text_single_chapter_without_delimiter(tmp_path):
    txt = tmp_path / "b.txt"
    txt.write_text("Just one block of text.\n", "utf-8")
    doc = load_document(txt)
    assert len(doc.chapters) == 1


def test_unsupported_extension_raises(tmp_path):
    bad = tmp_path / "b.xyz"
    bad.write_text("x", "utf-8")
    try:
        load_document(bad)
    except LoaderError as exc:
        assert "Unsupported" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected LoaderError")


def test_missing_file_raises(tmp_path):
    try:
        load_document(tmp_path / "nope.txt")
    except LoaderError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected LoaderError")


def test_supported_extensions_listed():
    assert ".pdf" in SUPPORTED_EXTENSIONS
    assert ".epub" in SUPPORTED_EXTENSIONS
    assert ".md" in SUPPORTED_EXTENSIONS
    assert ".txt" in SUPPORTED_EXTENSIONS
