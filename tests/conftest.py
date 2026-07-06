"""Shared test fixtures.

The audio-path tests need *real* MP3 bytes (not placeholder bytes) so that the
assembler's pydub/ffmpeg concatenation and mutagen ID3 tagging exercise the
genuine code path end to end. We synthesize tiny silent MP3 segments with
pydub (which shells out to ffmpeg), so these fixtures require ffmpeg on PATH —
the same requirement the tool itself has.
"""

from __future__ import annotations

import io

import pytest


@pytest.fixture(autouse=True)
def _offline_tokenizer(monkeypatch):
    """Keep the suite network-free: never let tiktoken download an encoding.

    ``tokens.count_tokens`` lazily fetches a tiktoken encoding on first use,
    which hits the network. Force the offline heuristic for every test (and
    clear any cached encoder). Tests that want the real tiktoken code path
    monkeypatch ``_load_tiktoken_encoding`` themselves with a fake encoding,
    which overrides this.
    """

    from textbook_audiobook import tokens

    monkeypatch.setattr(tokens, "_cached_encoder", None)
    monkeypatch.setattr(tokens, "_load_tiktoken_encoding", lambda: None)


def _silent_mp3(duration_ms: int) -> bytes:
    """Return real MP3-encoded bytes for ``duration_ms`` of silence."""

    from pydub import AudioSegment

    buf = io.BytesIO()
    AudioSegment.silent(duration=duration_ms).export(buf, format="mp3")
    return buf.getvalue()


@pytest.fixture(scope="session")
def mp3_segment_ms() -> int:
    """Nominal duration of each generated test segment, in milliseconds."""

    return 200


@pytest.fixture(scope="session")
def mp3_bytes(mp3_segment_ms: int) -> bytes:
    """A single, reusable real MP3 segment (200 ms of silence)."""

    return _silent_mp3(mp3_segment_ms)


@pytest.fixture
def make_mp3(tmp_path, mp3_bytes):
    """Factory: write a real MP3 file at ``name`` and return its Path."""

    def _make(name: str = "seg.mp3"):
        path = tmp_path / name
        path.write_bytes(mp3_bytes)
        return path

    return _make


def _mp3_duration_ms(path) -> float:
    from pydub import AudioSegment

    return len(AudioSegment.from_file(path, format="mp3"))


@pytest.fixture
def mp3_duration_ms():
    """Helper to read back the decoded duration of an MP3 file."""

    return _mp3_duration_ms
