"""Audio assembler.

Concatenates per-chunk MP3 files into the final output. Supports either a
single combined file or one file per chapter (``--split-by-chapter``). Writes
ID3v2 metadata (title, author/artist, album, track numbers) via mutagen.

Concatenation uses pydub, which requires ffmpeg to be installed and on PATH.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textbook_audiobook.models import Chunk, Document


class AssemblerError(RuntimeError):
    """Raised when audio assembly fails."""


@dataclass
class AssemblyResult:
    output_files: list[Path]
    split_by_chapter: bool


def _import_pydub():
    try:
        from pydub import AudioSegment  # type: ignore

        return AudioSegment
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise AssemblerError(
            "pydub is required for audio assembly. Install with `uv add pydub` "
            "and ensure ffmpeg is installed and on PATH."
        ) from exc


def _concat(segment_paths: list[Path], AudioSegment) -> object:
    if not segment_paths:
        raise AssemblerError("No audio segments to concatenate.")
    combined = AudioSegment.empty()
    for path in segment_paths:
        if not path.exists():
            raise AssemblerError(f"Missing audio segment: {path}")
        try:
            combined += AudioSegment.from_file(path, format="mp3")
        except Exception as exc:  # pragma: no cover - ffmpeg/decoder errors
            raise AssemblerError(
                f"Failed to decode {path}: {exc}. Is ffmpeg installed?"
            ) from exc
    return combined


def _export(combined, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        combined.export(out_path, format="mp3")
    except Exception as exc:  # pragma: no cover - ffmpeg errors
        raise AssemblerError(f"Failed to export {out_path}: {exc}") from exc


def assemble(
    document: Document,
    chunks: list[Chunk],
    chunk_files: dict[int, Path],
    output_dir: Path,
    *,
    split_by_chapter: bool = False,
) -> AssemblyResult:
    """Assemble synthesized chunk files into final MP3 output.

    ``chunk_files`` maps a chunk's global ``index`` to its on-disk MP3 path.
    """

    AudioSegment = _import_pydub()
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = document.slug

    ordered = sorted(chunks, key=lambda c: c.index)
    missing = [c.index for c in ordered if c.index not in chunk_files]
    if missing:
        raise AssemblerError(f"Missing synthesized audio for chunk(s): {missing}")

    if split_by_chapter:
        outputs = _assemble_per_chapter(
            document, ordered, chunk_files, output_dir, slug, AudioSegment
        )
    else:
        out_path = output_dir / f"{slug}_audiobook.mp3"
        paths = [chunk_files[c.index] for c in ordered]
        combined = _concat(paths, AudioSegment)
        _export(combined, out_path)
        write_id3_tags(out_path, title=document.title, author=document.author,
                       album=document.title)
        outputs = [out_path]

    return AssemblyResult(output_files=outputs, split_by_chapter=split_by_chapter)


def _assemble_per_chapter(
    document, ordered, chunk_files, output_dir, slug, AudioSegment
) -> list[Path]:
    # Preserve chapter order as it first appears in the chunk stream.
    chapter_order: list[int] = []
    by_chapter: dict[int, list[Chunk]] = {}
    for chunk in ordered:
        if chunk.chapter_index not in by_chapter:
            by_chapter[chunk.chapter_index] = []
            chapter_order.append(chunk.chapter_index)
        by_chapter[chunk.chapter_index].append(chunk)

    title_by_index = {c.index: c.title for c in document.chapters}
    outputs: list[Path] = []
    total = len(chapter_order)

    for track, chapter_index in enumerate(chapter_order, start=1):
        chapter_chunks = by_chapter[chapter_index]
        paths = [chunk_files[c.index] for c in chapter_chunks]
        combined = _concat(paths, AudioSegment)
        out_path = output_dir / f"{slug}_chapter_{track:03d}.mp3"
        _export(combined, out_path)

        chapter_title = title_by_index.get(chapter_index) or f"Chapter {track}"
        write_id3_tags(
            out_path,
            title=chapter_title,
            author=document.author,
            album=document.title,
            track=(track, total),
        )
        outputs.append(out_path)

    return outputs


def write_id3_tags(
    path: Path,
    *,
    title: str,
    author: str,
    album: str,
    track: tuple[int, int] | None = None,
) -> None:
    """Write ID3v2 tags to an MP3 file using mutagen."""

    try:
        from mutagen.id3 import (  # type: ignore
            ID3,
            TALB,
            TIT2,
            TPE1,
            TRCK,
            ID3NoHeaderError,  # type: ignore
        )
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise AssemblerError(
            "mutagen is required for ID3 tagging. Install with `uv add mutagen`."
        ) from exc

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.setall("TIT2", [TIT2(encoding=3, text=title or "")])
    tags.setall("TPE1", [TPE1(encoding=3, text=author or "Unknown")])
    tags.setall("TALB", [TALB(encoding=3, text=album or "")])
    if track is not None:
        number, total = track
        tags.setall("TRCK", [TRCK(encoding=3, text=f"{number}/{total}")])

    try:
        tags.save(path, v2_version=3)
    except Exception as exc:  # pragma: no cover - io errors
        raise AssemblerError(f"Failed to write ID3 tags to {path}: {exc}") from exc
