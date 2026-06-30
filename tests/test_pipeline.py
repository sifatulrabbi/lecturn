"""End-to-end pipeline tests.

The network is replaced by a stub that returns *real* MP3 bytes, so every stage
except the actual HTTP call runs for real: load -> clean -> chunk -> synthesize
(stubbed transport) -> assemble (pydub/ffmpeg) -> ID3 tags. This is the closest
we can get to a live run without consuming StepFun quota.
"""

from __future__ import annotations

import pytest

from textbook_audiobook import pipeline
from textbook_audiobook.config import StepFunConfig
from textbook_audiobook.tts import StepFunTTSClient


BOOK = """# The Art of Clear Thinking

## Chapter 1: Terrain

The beginning of all skill is understanding the terrain. We must see what is
there, not what we wish were there.

## Chapter 2: Slow Thinking

There are two systems at work in every decision. One is fast; one is slow.
"""


@pytest.fixture
def stub_network(monkeypatch, mp3_bytes):
    """Make StepFunTTSClient return real MP3 bytes instead of calling the API.

    Returns a counter dict so tests can assert how many synth requests happened.
    """

    counter = {"requests": 0}

    monkeypatch.setattr(StepFunTTSClient, "_build_client", lambda self: object())

    def fake_request(self, text, model):
        counter["requests"] += 1
        return mp3_bytes

    monkeypatch.setattr(StepFunTTSClient, "_request_audio", fake_request)
    return counter


def _config() -> StepFunConfig:
    return StepFunConfig(
        api_key="x",
        base_url="http://x",
        model="stepaudio-2.5-tts",
        voice="lively-girl",
    )


def _write_book(tmp_path):
    src = tmp_path / "book.md"
    src.write_text(BOOK, encoding="utf-8")
    return src


def test_full_pipeline_single_file(tmp_path, stub_network, mp3_duration_ms):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"

    result = pipeline.run_pipeline(
        src, out_dir, _config(), max_chars=1000, split_by_chapter=False
    )

    assert len(result.assembly.output_files) == 1
    out = result.assembly.output_files[0]
    assert out.exists() and out.stat().st_size > 0
    assert mp3_duration_ms(out) > 0
    # One request per chunk.
    assert stub_network["requests"] == len(result.chunks) > 0
    # Cost is computed from the (primary) model actually used.
    assert result.estimated_cost_usd > 0

    from mutagen.id3 import ID3

    tags = ID3(out)
    assert tags["TIT2"].text[0] == "The Art of Clear Thinking"


def test_full_pipeline_split_by_chapter(tmp_path, stub_network):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"

    result = pipeline.run_pipeline(
        src, out_dir, _config(), max_chars=1000, split_by_chapter=True
    )

    # Two chapters in the source -> two files.
    assert len(result.assembly.output_files) == 2
    for p in result.assembly.output_files:
        assert p.exists() and p.stat().st_size > 0


def test_resume_skips_cached_chunks(tmp_path, stub_network):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"

    first = pipeline.run_pipeline(src, out_dir, _config(), max_chars=1000)
    n_chunks = len(first.chunks)
    assert stub_network["requests"] == n_chunks

    # Second run with resume (default) should hit the cache for every chunk.
    pipeline.run_pipeline(src, out_dir, _config(), max_chars=1000, resume=True)
    assert stub_network["requests"] == n_chunks  # unchanged: nothing re-synthesized


def test_no_resume_resynthesizes(tmp_path, stub_network):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"

    first = pipeline.run_pipeline(src, out_dir, _config(), max_chars=1000)
    n_chunks = len(first.chunks)
    assert stub_network["requests"] == n_chunks

    pipeline.run_pipeline(src, out_dir, _config(), max_chars=1000, resume=False)
    assert stub_network["requests"] == 2 * n_chunks  # everything re-synthesized


def test_empty_document_raises(tmp_path, stub_network):
    # A file that cleans down to nothing (only a page number) -> no chunks.
    src = tmp_path / "empty.txt"
    src.write_text("   \n\n   \n", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        pipeline.run_pipeline(src, tmp_path / "out", _config(), max_chars=1000)
    assert "narratable" in str(exc.value).lower()


def test_plan_only_no_network(tmp_path, monkeypatch):
    # plan_only must never construct a client or hit the network.
    def boom(self):  # pragma: no cover - should never be called
        raise AssertionError("plan_only must not build a TTS client")

    monkeypatch.setattr(StepFunTTSClient, "_build_client", boom)

    src = _write_book(tmp_path)
    doc, chunks, cost = pipeline.plan_only(
        src, title=None, author=None, max_chars=1000, model="stepaudio-2.5-tts"
    )
    assert len(chunks) > 0
    assert cost > 0
    assert doc.title == "The Art of Clear Thinking"
