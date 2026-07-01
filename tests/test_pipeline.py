"""End-to-end pipeline tests.

The network is replaced by a stub that returns *real* MP3 bytes, so every stage
except the actual HTTP call runs for real: load -> clean -> chunk -> synthesize
(stubbed transport) -> assemble (pydub/ffmpeg) -> ID3 tags. This is the closest
we can get to a live run without consuming StepFun quota.
"""

from __future__ import annotations

import threading
import time

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


def _write_book_n_chunks(tmp_path, n):
    """Write a Markdown book with ``n`` short chapters -> ``n`` chunks."""

    parts = ["# Multi-Chapter Book\n"]
    for i in range(n):
        parts.append(f"\n## Chapter {i}\n\nBody of chapter {i}. A short sentence.\n")
    src = tmp_path / "multi.md"
    src.write_text("".join(parts), encoding="utf-8")
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


def _inflight_tracker(monkeypatch, mp3_bytes, *, sleep=0.03):
    """Stub _request_audio to record peak in-flight concurrency. Returns state."""

    monkeypatch.setattr(StepFunTTSClient, "_build_client", lambda self: object())
    lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0, "count": 0}

    def fake_request(self, text, model):
        with lock:
            state["in_flight"] += 1
            state["count"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        time.sleep(sleep)  # widen the overlap window so real concurrency is observable
        with lock:
            state["in_flight"] -= 1
        return mp3_bytes

    monkeypatch.setattr(StepFunTTSClient, "_request_audio", fake_request)
    return state


def test_default_and_concurrency_1_run_sequentially(tmp_path, monkeypatch, mp3_bytes):
    """The default (and explicit concurrency=1) must keep exactly one request in flight."""

    state = _inflight_tracker(monkeypatch, mp3_bytes)
    src = _write_book_n_chunks(tmp_path, 6)

    result = pipeline.run_pipeline(
        src, tmp_path / "out", _config(), max_chars=1000, concurrency=1, rpm=0
    )

    assert len(result.chunks) >= 4          # meaningful: several chunks to (not) overlap
    assert state["max_in_flight"] == 1      # never more than one at a time
    assert state["count"] == len(result.chunks)


def test_synthesis_concurrency_is_bounded(tmp_path, monkeypatch, mp3_bytes):
    """concurrency=3 must actually parallelise but never exceed the bound."""

    state = _inflight_tracker(monkeypatch, mp3_bytes)
    src = _write_book_n_chunks(tmp_path, 8)

    result = pipeline.run_pipeline(
        src, tmp_path / "out", _config(), max_chars=1000, concurrency=3, rpm=0
    )

    assert len(result.chunks) >= 6
    assert state["max_in_flight"] > 1       # genuinely concurrent
    assert state["max_in_flight"] <= 3      # never exceeds --concurrency
    assert state["count"] == len(result.chunks)


def test_rate_limiter_caps_starts_per_period():
    from textbook_audiobook.pipeline import _RateLimiter

    limiter = _RateLimiter(max_calls=3, period=0.3)
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()                   # first 3 proceed immediately
    assert time.monotonic() - start < 0.15
    limiter.acquire()                       # 4th must wait out the window
    assert time.monotonic() - start >= 0.25

    # max_calls <= 0 disables throttling entirely.
    unlimited = _RateLimiter(max_calls=0)
    t = time.monotonic()
    for _ in range(200):
        unlimited.acquire()
    assert time.monotonic() - t < 0.1


def test_resume_rejects_corrupt_cache(tmp_path, stub_network):
    """A cached file that isn't a valid MP3 must be re-synthesized, not trusted."""

    src = _write_book(tmp_path)
    out = tmp_path / "out"
    first = pipeline.run_pipeline(src, out, _config(), max_chars=1000)
    n = len(first.chunks)
    assert stub_network["requests"] == n

    cache = out / ".audiobook_cache" / first.document.slug
    files = sorted(cache.glob("chunk_*.mp3"))
    files[0].write_bytes(b"this is not an mp3")    # corrupt one cached chunk

    stub_network["requests"] = 0
    pipeline.run_pipeline(src, out, _config(), max_chars=1000, resume=True)
    # Only the corrupt chunk is redone; the valid ones are reused.
    assert stub_network["requests"] == 1


def test_resume_resynthesizes_when_voice_changes(tmp_path, stub_network):
    """Changing the voice changes the fingerprint, so stale audio isn't reused."""

    src = _write_book(tmp_path)
    out = tmp_path / "out"
    cfg_a = StepFunConfig(api_key="x", base_url="http://x", model="step-tts-2", voice="lively-girl")
    first = pipeline.run_pipeline(src, out, cfg_a, max_chars=1000)
    n = len(first.chunks)
    assert stub_network["requests"] == n

    stub_network["requests"] = 0
    cfg_b = StepFunConfig(api_key="x", base_url="http://x", model="step-tts-2", voice="vibrant-youth")
    pipeline.run_pipeline(src, out, cfg_b, max_chars=1000, resume=True)
    assert stub_network["requests"] == n    # every chunk re-synthesized for the new voice


def test_is_playable_mp3_accepts_id3_prefixed_audio(tmp_path):
    """Regression: StepFun MP3s start with ID3 and report mutagen length 0.0.

    They must be accepted for reuse — the old length>0 check rejected every one
    and forced a full regeneration.
    """

    from textbook_audiobook.pipeline import _is_playable_mp3

    # ID3v2-tagged file (StepFun's format) with a realistic size.
    id3 = tmp_path / "id3.mp3"
    id3.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 4000)
    assert _is_playable_mp3(id3)

    # Bare MPEG frame sync is also valid.
    framed = tmp_path / "framed.mp3"
    framed.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 4000)
    assert _is_playable_mp3(framed)

    # Rejected: missing, empty, too-small, and non-MP3 garbage.
    assert not _is_playable_mp3(tmp_path / "missing.mp3")
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    assert not _is_playable_mp3(empty)
    garbage = tmp_path / "g.mp3"
    garbage.write_bytes(b"this is not an mp3")
    assert not _is_playable_mp3(garbage)
    big_nonmagic = tmp_path / "n.mp3"
    big_nonmagic.write_bytes(b"\x00" * 4000)
    assert not _is_playable_mp3(big_nonmagic)


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
