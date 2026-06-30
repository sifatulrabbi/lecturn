"""PDF loader using PyMuPDF (fitz).

Extracts the text layer only. Image-only PDFs (no extractable text) are
flagged for a future OCR pass rather than silently producing empty output,
per PLAN.md.

Chapter detection strategy:
  1. Prefer the embedded table of contents (PDF bookmarks / outline) when
     present — these map cleanly to chapter boundaries by page range.
  2. Otherwise fall back to a single chapter spanning the whole document.
     (Heuristic heading detection from font sizes is intentionally left for
     a future iteration to avoid fragile splits.)
"""

from __future__ import annotations

from pathlib import Path

from textbook_audiobook.loaders.base import LoaderError
from textbook_audiobook.models import Chapter, Document


class ImageOnlyPdfError(LoaderError):
    """Raised when a PDF has no extractable text layer (needs OCR)."""


def _import_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LoaderError(
            "PyMuPDF is required for PDF input. Install with `uv add pymupdf`."
        ) from exc


def _page_text(page) -> str:
    return page.get_text("text")


def _chapters_from_toc(doc, page_texts: list[str]) -> list[Chapter] | None:
    """Build chapters from the PDF outline (table of contents), if any."""

    toc = doc.get_toc(simple=True)  # list of [level, title, page]
    if not toc:
        return None

    # Keep only top-level entries to avoid over-fragmenting into subsections.
    top = [entry for entry in toc if entry[0] == 1]
    if len(top) < 2:
        return None

    chapters: list[Chapter] = []
    for i, (_level, ctitle, start_page) in enumerate(top):
        start = max(0, start_page - 1)  # TOC pages are 1-based
        end = (top[i + 1][2] - 1) if i + 1 < len(top) else len(page_texts)
        end = max(start, min(end, len(page_texts)))
        body = "\n".join(page_texts[start:end]).strip()
        chapters.append(Chapter(index=i, title=ctitle.strip(), text=body))
    return chapters


def load(path: Path, *, title: str | None, author: str | None) -> Document:
    fitz = _import_fitz()

    try:
        doc = fitz.open(path)
    except Exception as exc:  # pragma: no cover - corrupt files
        raise LoaderError(f"Could not open PDF {path}: {exc}") from exc

    try:
        page_texts = [_page_text(page) for page in doc]
        full_text = "\n".join(page_texts).strip()

        if not full_text:
            raise ImageOnlyPdfError(
                f"{path} appears to be an image-only PDF with no text layer. "
                "OCR is out of scope for v1 — flag for a future OCR pass."
            )

        chapters = _chapters_from_toc(doc, page_texts)
        if chapters is None:
            chapters = [Chapter(index=0, title="", text=full_text)]

        meta = doc.metadata or {}
        resolved_title = title or (meta.get("title") or "").strip() or path.stem
        resolved_author = author or (meta.get("author") or "").strip() or "Unknown"
    finally:
        doc.close()

    return Document(
        title=resolved_title,
        author=resolved_author,
        source_path=path,
        chapters=chapters,
    )
