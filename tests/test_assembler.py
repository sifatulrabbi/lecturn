"""Tests for the audio assembler.

These use real ffmpeg-encoded MP3 segments (via the conftest fixtures) so that
pydub concatenation and mutagen ID3 tagging are exercised for real — no mocking
of the audio layer.
"""

from __future__ import annotations

import pytest

from textbook_audiobook.assembler import AssemblerError, assemble, write_id3_tags
from textbook_audiobook.models import Chapter, Chunk, Document


def _doc(chapters: list[Chapter]) -> Document:
    from pathlib import Path

    return Document(
        title="Clear Thinking",
        author="Jane Doe",
        source_path=Path("book.md"),
        chapters=chapters,
    )


def _read_tags(path):
    from mutagen.id3 import ID3

    return ID3(path)


def test_single_file_concat_and_tags(
    tmp_path, mp3_bytes, mp3_segment_ms, mp3_duration_ms
):
    # Two chapters, three chunks total -> one combined file.
    doc = _doc([Chapter(0, "One", "a"), Chapter(1, "Two", "b")])
    chunks = [
        Chunk(index=0, chapter_index=0, chapter_chunk_index=0, text="a"),
        Chunk(index=1, chapter_index=0, chapter_chunk_index=1, text="b"),
        Chunk(index=2, chapter_index=1, chapter_chunk_index=0, text="c"),
    ]
    files = {}
    for c in chunks:
        p = tmp_path / f"chunk_{c.index}.mp3"
        p.write_bytes(mp3_bytes)
        files[c.index] = p

    result = assemble(doc, chunks, files, tmp_path / "out")
    assert not result.split_by_chapter
    assert len(result.output_files) == 1
    out = result.output_files[0]
    assert out.exists() and out.stat().st_size > 0
    assert out.name == "clear_thinking_audiobook.mp3"

    # Concatenated duration is roughly the sum of the three segments.
    dur = mp3_duration_ms(out)
    assert dur >= mp3_segment_ms * 3 * 0.7

    tags = _read_tags(out)
    assert tags["TIT2"].text[0] == "Clear Thinking"
    assert tags["TPE1"].text[0] == "Jane Doe"
    assert tags["TALB"].text[0] == "Clear Thinking"


def test_split_by_chapter_emits_one_file_per_chapter_with_tracks(tmp_path, mp3_bytes):
    doc = _doc([Chapter(0, "First", "x"), Chapter(1, "Second", "y")])
    chunks = [
        Chunk(index=0, chapter_index=0, chapter_chunk_index=0, text="x"),
        Chunk(index=1, chapter_index=1, chapter_chunk_index=0, text="y"),
        Chunk(index=2, chapter_index=1, chapter_chunk_index=1, text="z"),
    ]
    files = {}
    for c in chunks:
        p = tmp_path / f"chunk_{c.index}.mp3"
        p.write_bytes(mp3_bytes)
        files[c.index] = p

    result = assemble(doc, chunks, files, tmp_path / "out", split_by_chapter=True)
    assert result.split_by_chapter
    assert len(result.output_files) == 2

    names = sorted(p.name for p in result.output_files)
    assert names == [
        "clear_thinking_chapter_001.mp3",
        "clear_thinking_chapter_002.mp3",
    ]

    t1 = _read_tags(result.output_files[0])
    assert t1["TIT2"].text[0] == "First"
    assert t1["TRCK"].text[0] == "1/2"
    t2 = _read_tags(result.output_files[1])
    assert t2["TIT2"].text[0] == "Second"
    assert t2["TRCK"].text[0] == "2/2"


def test_missing_chunk_audio_raises(tmp_path, mp3_bytes):
    doc = _doc([Chapter(0, "One", "a")])
    chunks = [
        Chunk(index=0, chapter_index=0, chapter_chunk_index=0, text="a"),
        Chunk(index=1, chapter_index=0, chapter_chunk_index=1, text="b"),
    ]
    p = tmp_path / "chunk_0.mp3"
    p.write_bytes(mp3_bytes)
    files = {0: p}  # chunk 1 deliberately absent

    with pytest.raises(AssemblerError) as exc:
        assemble(doc, chunks, files, tmp_path / "out")
    assert "1" in str(exc.value)


def test_chapter_order_follows_chunk_stream_not_index(tmp_path, mp3_bytes):
    # Chapter indices appear out of natural order in the stream; output tracks
    # should follow first-appearance order.
    doc = _doc([Chapter(0, "Alpha", "a"), Chapter(1, "Beta", "b")])
    chunks = [
        Chunk(index=0, chapter_index=1, chapter_chunk_index=0, text="b"),
        Chunk(index=1, chapter_index=0, chapter_chunk_index=0, text="a"),
    ]
    files = {}
    for c in chunks:
        p = tmp_path / f"chunk_{c.index}.mp3"
        p.write_bytes(mp3_bytes)
        files[c.index] = p

    result = assemble(doc, chunks, files, tmp_path / "out", split_by_chapter=True)
    # First file = Beta (chapter_index 1 appeared first), track 1.
    t1 = _read_tags(result.output_files[0])
    assert t1["TIT2"].text[0] == "Beta"
    assert t1["TRCK"].text[0] == "1/2"


def test_write_id3_tags_handles_missing_author(tmp_path, mp3_bytes):
    p = tmp_path / "a.mp3"
    p.write_bytes(mp3_bytes)
    write_id3_tags(p, title="T", author="", album="A")
    tags = _read_tags(p)
    # Empty author falls back to "Unknown".
    assert tags["TPE1"].text[0] == "Unknown"
