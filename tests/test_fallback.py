"""Tests for the cross-provider fallback: FallbackTTSClient + the pipeline seam.

The network is never touched: clients are built without a real OpenAI client and
``_request_audio`` is stubbed per test. Covers the wrapper's switch semantics
(one-way, exactly-once, thread-safe, skip-on-missing-key) and the pipeline's
finding-4 fix — post-switch chunks are fingerprinted with the *fallback* config.
"""

from __future__ import annotations

import threading
import time

import pytest

from textbook_audiobook import pipeline
from textbook_audiobook.config import (
    MissingApiKeyError,
    OpenRouterConfig,
    StepFunConfig,
    estimate_cost,
)
from textbook_audiobook.pipeline import _chunk_fingerprint
from textbook_audiobook.tts import (
    FallbackTTSClient,
    OpenRouterTTSClient,
    StepFunTTSClient,
    TTSError,
    _FatalError,
)


def _stepfun(monkeypatch, req) -> StepFunTTSClient:
    monkeypatch.setattr(StepFunTTSClient, "_build_client", lambda self: object())
    cfg = StepFunConfig(
        api_key="x", base_url="http://x",
        model="stepaudio-2.5-tts", voice="lively-girl",
    )
    c = StepFunTTSClient(config=cfg, base_backoff=0.001, max_backoff=0.005)
    c._request_audio = req
    return c


def _openrouter(monkeypatch, req) -> OpenRouterTTSClient:
    monkeypatch.setattr(OpenRouterTTSClient, "_build_client", lambda self: object())
    cfg = OpenRouterConfig(
        api_key="x", base_url="http://x",
        model="hexgrad/kokoro-82m", voice="af_heart",
    )
    c = OpenRouterTTSClient(config=cfg, base_backoff=0.001, max_backoff=0.005)
    c._request_audio = req
    return c


def _quota(text, model):
    raise _FatalError("out of credit", category="quota", fallback_eligible=True)


# -- FallbackTTSClient unit tests -------------------------------------------


def test_switches_to_openrouter_on_eligible_error(monkeypatch, tmp_path, mp3_bytes):
    primary = _stepfun(monkeypatch, _quota)
    built = {"n": 0}

    def fallback_factory():
        built["n"] += 1
        return _openrouter(monkeypatch, lambda text, model: mp3_bytes)

    wrapper = FallbackTTSClient(primary=primary, fallback_factory=fallback_factory)
    # Before any failure the wrapper reflects the primary.
    assert wrapper.active_model == "stepaudio-2.5-tts"
    assert wrapper.active_config.voice == "lively-girl"

    out = wrapper.synthesize_text("hello", tmp_path / "a.mp3")
    assert pipeline._is_playable_mp3(out)
    assert built["n"] == 1
    assert wrapper.stats.fallbacks == 1
    # After the switch the wrapper reflects the fallback (OpenRouter/Kokoro).
    assert wrapper.active_model == "hexgrad/kokoro-82m"
    assert wrapper.active_config.voice == "af_heart"

    # Subsequent chunks go straight to the fallback — no second client built.
    out2 = wrapper.synthesize_text("world", tmp_path / "b.mp3")
    assert pipeline._is_playable_mp3(out2)
    assert built["n"] == 1


def test_no_switch_on_non_eligible_error(monkeypatch, tmp_path):
    def auth_fail(text, model):
        raise _FatalError("bad key", category="auth", fallback_eligible=False)

    primary = _stepfun(monkeypatch, auth_fail)
    built = {"n": 0}

    def fallback_factory():  # pragma: no cover - must never run
        built["n"] += 1
        return _openrouter(monkeypatch, lambda t, m: b"x")

    wrapper = FallbackTTSClient(primary=primary, fallback_factory=fallback_factory)
    with pytest.raises(TTSError):
        wrapper.synthesize_text("hi", tmp_path / "a.mp3")
    assert built["n"] == 0
    assert wrapper.stats.fallbacks == 0


def test_no_switch_when_fallback_disabled(monkeypatch, tmp_path):
    primary = _stepfun(monkeypatch, _quota)
    wrapper = FallbackTTSClient(primary=primary, fallback_factory=None)
    with pytest.raises(TTSError) as exc:
        wrapper.synthesize_text("hi", tmp_path / "a.mp3")
    # Still tagged eligible, just not acted on (no fallback configured).
    assert exc.value.fallback_eligible is True
    assert wrapper.stats.fallbacks == 0


def test_missing_key_surfaces_original_error_with_skip_note(monkeypatch, tmp_path):
    primary = _stepfun(monkeypatch, _quota)

    def fallback_factory():
        raise MissingApiKeyError(
            "No OpenRouter API key found. Set the OPENROUTER_API_KEY variable."
        )

    wrapper = FallbackTTSClient(primary=primary, fallback_factory=fallback_factory)
    with pytest.raises(TTSError) as exc:
        wrapper.synthesize_text("hi", tmp_path / "a.mp3")
    msg = str(exc.value)
    assert "credit" in msg.lower()          # the ORIGINAL StepFun error is kept
    assert "OPENROUTER_API_KEY" in msg      # the skip note names the missing var
    assert "skipped" in msg.lower()
    # No switch was recorded — the run stays on the primary.
    assert wrapper.stats.fallbacks == 0
    assert wrapper.active_model == "stepaudio-2.5-tts"


def test_switch_is_exactly_once_under_concurrency(monkeypatch, tmp_path, mp3_bytes):
    def slow_quota(text, model):
        time.sleep(0.005)  # widen the race window
        raise _FatalError("out of credit", category="quota", fallback_eligible=True)

    primary = _stepfun(monkeypatch, slow_quota)
    built = {"n": 0}
    build_lock = threading.Lock()

    def fallback_factory():
        with build_lock:
            built["n"] += 1
        return _openrouter(monkeypatch, lambda text, model: mp3_bytes)

    wrapper = FallbackTTSClient(primary=primary, fallback_factory=fallback_factory)

    results: list = []

    def worker(i: int) -> None:
        try:
            results.append(wrapper.synthesize_text(f"chunk {i}", tmp_path / f"c{i}.mp3"))
        except Exception as exc:  # pragma: no cover - would fail the assert below
            results.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert built["n"] == 1                 # fallback built exactly once
    assert wrapper.stats.fallbacks == 1    # switch recorded exactly once
    assert all(pipeline._is_playable_mp3(r) for r in results)


# -- pipeline-level cross-provider fallback (finding-4 fix) ------------------


_BOOK = "# Fallback Book\n"
for _i in range(3):
    _BOOK += f"\n## Chapter {_i}\n\nBody of chapter {_i}. A short sentence.\n"


def test_pipeline_fallback_fingerprints_with_active_config(
    tmp_path, monkeypatch, mp3_bytes
):
    # StepFun primary always 402 (quota) -> every chunk is fallback-eligible.
    monkeypatch.setattr(StepFunTTSClient, "_build_client", lambda self: object())

    def stepfun_req(self, text, model):
        raise _FatalError("out of credit", category="quota", fallback_eligible=True)

    monkeypatch.setattr(StepFunTTSClient, "_request_audio", stepfun_req)

    # OpenRouter fallback returns real MP3 bytes.
    monkeypatch.setattr(OpenRouterTTSClient, "_build_client", lambda self: object())
    or_calls = {"n": 0}

    def or_req(self, text, model):
        or_calls["n"] += 1
        return mp3_bytes

    monkeypatch.setattr(OpenRouterTTSClient, "_request_audio", or_req)

    built = {"n": 0}
    fb_cfg = OpenRouterConfig(
        api_key="x", base_url="http://x",
        model="hexgrad/kokoro-82m", voice="af_heart",
    )

    def fallback_factory():
        built["n"] += 1
        return OpenRouterTTSClient(config=fb_cfg)

    src = tmp_path / "book.md"
    src.write_text(_BOOK, encoding="utf-8")
    out_dir = tmp_path / "out"
    stepfun_cfg = StepFunConfig(
        api_key="x", base_url="http://x",
        model="stepaudio-2.5-tts", voice="lively-girl",
    )

    result = pipeline.run_pipeline(
        src, out_dir, stepfun_cfg, max_chars=1000, fallback_factory=fallback_factory
    )

    chunks = result.chunks
    n = len(chunks)
    assert n >= 2
    assert built["n"] == 1            # fallback client built exactly once
    assert or_calls["n"] == n         # OpenRouter produced every chunk

    # Cost reflects the active (fallback) model — Kokoro pricing.
    total = sum(c.char_count for c in chunks)
    assert result.estimated_cost_usd == pytest.approx(
        estimate_cost(total, "hexgrad/kokoro-82m")
    )

    cache = out_dir / ".audiobook_cache" / result.document.slug
    # finding-4 fix: post-switch chunks are fingerprinted with the FALLBACK
    # config (af_heart), not the primary's (lively-girl).
    for chunk in chunks[1:]:
        fp = _chunk_fingerprint(fb_cfg, chunk.text)
        assert (cache / f"chunk_{chunk.index:05d}_{fp}.mp3").exists()

    # Accepted edge: the chunk DURING which the switch happens (index 0, since the
    # library default is sequential) is stored under the pre-switch fingerprint.
    fp0 = _chunk_fingerprint(stepfun_cfg, chunks[0].text)
    assert (cache / f"chunk_{chunks[0].index:05d}_{fp0}.mp3").exists()
