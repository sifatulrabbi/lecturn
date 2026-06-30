"""EPUB loader using ebooklib + BeautifulSoup.

Each EPUB spine document (typically one per chapter) becomes a chapter. HTML
tags are stripped to text; headings are kept as chapter titles where they can
be derived from the document's first heading element.
"""

from __future__ import annotations

from pathlib import Path

from textbook_audiobook.loaders.base import LoaderError
from textbook_audiobook.models import Chapter, Document


def _imports():
    try:
        from ebooklib import epub  # type: ignore
        import ebooklib  # type: ignore
        from bs4 import BeautifulSoup  # type: ignore

        return ebooklib, epub, BeautifulSoup
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LoaderError(
            "ebooklib and beautifulsoup4 are required for EPUB input. "
            "Install with `uv add ebooklib beautifulsoup4 lxml`."
        ) from exc


def _html_to_text(html: bytes | str, BeautifulSoup) -> tuple[str, str]:
    """Return (title, body_text) for an HTML document.

    The title is taken from the first heading element if present.
    """

    soup = BeautifulSoup(html, "html.parser")

    # Drop non-narratable elements.
    for tag in soup(["script", "style", "head", "nav"]):
        tag.decompose()

    heading = soup.find(["h1", "h2", "h3"])
    title = heading.get_text(strip=True) if heading else ""

    text = soup.get_text(separator="\n")
    return title, text


def load(path: Path, *, title: str | None, author: str | None) -> Document:
    ebooklib, epub, BeautifulSoup = _imports()

    try:
        book = epub.read_epub(str(path))
    except Exception as exc:  # pragma: no cover - corrupt files
        raise LoaderError(f"Could not read EPUB {path}: {exc}") from exc

    chapters: list[Chapter] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        chapter_title, body = _html_to_text(item.get_content(), BeautifulSoup)
        body = body.strip()
        if not body:
            continue
        chapters.append(
            Chapter(index=len(chapters), title=chapter_title, text=body)
        )

    if not chapters:
        raise LoaderError(f"No readable text found in EPUB {path}.")

    # Metadata: ebooklib returns lists of (value, attrs) tuples.
    def _meta(field: str) -> str:
        try:
            data = book.get_metadata("DC", field)
            if data:
                return str(data[0][0]).strip()
        except Exception:  # pragma: no cover - defensive
            pass
        return ""

    resolved_title = title or _meta("title") or path.stem
    resolved_author = author or _meta("creator") or "Unknown"

    return Document(
        title=resolved_title,
        author=resolved_author,
        source_path=path,
        chapters=chapters,
    )
