"""Tests for the loaders (Markdown, plain text, PDF, and EPUB)."""

from __future__ import annotations

import pytest

from textbook_audiobook.loaders import load_document, SUPPORTED_EXTENSIONS
from textbook_audiobook.loaders.base import LoaderError
from textbook_audiobook.loaders.pdf_loader import ImageOnlyPdfError


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


# -- PDF loader -------------------------------------------------------------


def _make_pdf(path, *, pages, toc=None, metadata=None):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        if body:
            page.insert_text((72, 72), body)
    if metadata:
        doc.set_metadata(metadata)
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


def test_pdf_toc_becomes_chapters(tmp_path):
    pdf = tmp_path / "book.pdf"
    _make_pdf(
        pdf,
        pages=["Chapter one body text here.", "Chapter two body text here."],
        toc=[[1, "Chapter One", 1], [1, "Chapter Two", 2]],
        metadata={"title": "TOC Book", "author": "A. Writer"},
    )
    doc = load_document(pdf)
    assert doc.title == "TOC Book"
    assert doc.author == "A. Writer"
    titles = [c.title for c in doc.chapters]
    assert titles == ["Chapter One", "Chapter Two"]


def test_pdf_without_toc_is_single_chapter(tmp_path):
    pdf = tmp_path / "book.pdf"
    _make_pdf(pdf, pages=["Just one page of text, no outline."])
    doc = load_document(pdf)
    assert len(doc.chapters) == 1
    assert "one page of text" in doc.chapters[0].text


def test_pdf_metadata_falls_back_to_stem(tmp_path):
    pdf = tmp_path / "fallback_name.pdf"
    _make_pdf(pdf, pages=["Body with no metadata title."])
    doc = load_document(pdf)
    assert doc.title == "fallback_name"
    assert doc.author == "Unknown"


def test_pdf_image_only_raises(tmp_path):
    # A page with no text layer -> no extractable text -> flagged for OCR.
    pdf = tmp_path / "scanned.pdf"
    _make_pdf(pdf, pages=[""])
    with pytest.raises(ImageOnlyPdfError) as exc:
        load_document(pdf)
    assert "image-only" in str(exc.value).lower()


# -- EPUB loader ------------------------------------------------------------


def _make_epub(path, *, title, author, chapters):
    epub = pytest.importorskip("ebooklib.epub", reason="ebooklib required")
    from ebooklib import epub as epub_mod

    book = epub_mod.EpubBook()
    book.set_identifier("test-id")
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    items = []
    for i, (ctitle, body) in enumerate(chapters):
        item = epub_mod.EpubHtml(title=ctitle, file_name=f"c{i}.xhtml", lang="en")
        item.content = f"<h1>{ctitle}</h1><p>{body}</p>"
        book.add_item(item)
        items.append(item)

    book.toc = tuple(items)
    book.add_item(epub_mod.EpubNcx())
    book.add_item(epub_mod.EpubNav())
    book.spine = ["nav", *items]
    epub_mod.write_epub(str(path), book)


def test_epub_spine_documents_become_chapters(tmp_path):
    ep = tmp_path / "book.epub"
    _make_epub(
        ep,
        title="EPUB Title",
        author="E. Author",
        chapters=[("Opening", "First chapter prose."), ("Closing", "Second chapter prose.")],
    )
    doc = load_document(ep)
    assert doc.title == "EPUB Title"
    assert doc.author == "E. Author"
    # At least the two content documents become chapters (nav may be skipped as
    # it has no narratable body once stripped).
    bodies = " ".join(c.text for c in doc.chapters)
    assert "First chapter prose" in bodies
    assert "Second chapter prose" in bodies


def test_epub_title_override(tmp_path):
    ep = tmp_path / "book.epub"
    _make_epub(
        ep,
        title="Original",
        author="E. Author",
        chapters=[("Ch", "Some body text.")],
    )
    doc = load_document(ep, title="Overridden", author="Me")
    assert doc.title == "Overridden"
    assert doc.author == "Me"
