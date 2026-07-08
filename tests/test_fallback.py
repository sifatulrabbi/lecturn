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
    LocalConfig,
    MissingApiKeyError,
    OpenRouterConfig,
    StepFunConfig,
    estimate_cost,
)
from textbook_audiobook.pipeline import _chunk_fingerprint
from textbook_audiobook.tts import (
    FallbackTTSClient,
    LocalTTSClient,
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


def _local(monkeypatch, req) -> LocalTTSClient:
    monkeypatch.setattr(LocalTTSClient, "_build_client", lambda self: object())
    cfg = LocalConfig(
        api_key="local", base_url="http://127.0.0.1:8880/v1",
        model="kokoro", voice="af_heart",
    )
    c = LocalTTSClient(config=cfg, base_backoff=0.001, max_backoff=0.005)
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
            results.append(
                wrapper.synthesize_text(f"chunk {i}", tmp_path / f"c{i}.mp3")
            )
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


# -- local primary --------------------------------------------------------


def test_local_primary_no_switch_when_fallback_disabled(monkeypatch, tmp_path):
    # The CLI defaults a local primary to fallback_factory=None, so a dead local
    # server (fallback-eligible error) must NOT silently switch to OpenRouter.
    primary = _local(monkeypatch, _quota)
    wrapper = FallbackTTSClient(primary=primary, fallback_factory=None)
    with pytest.raises(TTSError) as exc:
        wrapper.synthesize_text("hi", tmp_path / "a.mp3")
    assert exc.value.fallback_eligible is True   # eligible, just not acted on
    assert wrapper.stats.fallbacks == 0
    assert wrapper.active_model == "kokoro"


def test_local_primary_switches_when_fallback_explicit(
    monkeypatch, tmp_path, mp3_bytes
):
    # An explicit --fallback-model re-enables the OpenRouter fallback even for a
    # local primary: on an eligible error it switches to OpenRouter/Kokoro.
    primary = _local(monkeypatch, _quota)
    built = {"n": 0}

    def fallback_factory():
        built["n"] += 1
        return _openrouter(monkeypatch, lambda text, model: mp3_bytes)

    wrapper = FallbackTTSClient(primary=primary, fallback_factory=fallback_factory)
    assert wrapper.active_model == "kokoro"

    out = wrapper.synthesize_text("hello", tmp_path / "a.mp3")
    assert pipeline._is_playable_mp3(out)
    assert built["n"] == 1
    assert wrapper.stats.fallbacks == 1
    # After the switch the wrapper reflects the OpenRouter fallback.
    assert wrapper.active_model == "hexgrad/kokoro-82m"
    assert wrapper.active_config.voice == "af_heart"


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

    # cleanup_cache=False: this test inspects the on-disk cache fingerprints after
    # the run, so keep them (a successful run prunes its cache by default).
    result = pipeline.run_pipeline(
        src, out_dir, stepfun_cfg, max_chars=1000, fallback_factory=fallback_factory,
        cleanup_cache=False,
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


def test_pipeline_cost_mixes_primary_and_fallback_rates(
    tmp_path, monkeypatch, mp3_bytes
):
    # The primary (StepFun) narrates the FIRST chunk, then goes 402 (quota) on the
    # next, forcing a mid-book switch to OpenRouter/Kokoro for the remainder. The
    # cost estimate must bill chunk 0 at StepFun's rate and the rest at Kokoro's —
    # NOT price everything at the final (Kokoro) model's rate (the pre-fix bug).
    monkeypatch.setattr(StepFunTTSClient, "_build_client", lambda self: object())
    sf_calls = {"n": 0}

    def stepfun_req(self, text, model):
        n = sf_calls["n"]
        sf_calls["n"] += 1
        if n == 0:  # first chunk succeeds on the primary
            return mp3_bytes
        raise _FatalError("out of credit", category="quota", fallback_eligible=True)

    monkeypatch.setattr(StepFunTTSClient, "_request_audio", stepfun_req)

    monkeypatch.setattr(OpenRouterTTSClient, "_build_client", lambda self: object())
    monkeypatch.setattr(
        OpenRouterTTSClient, "_request_audio", lambda self, text, model: mp3_bytes
    )

    fb_cfg = OpenRouterConfig(
        api_key="x", base_url="http://x",
        model="hexgrad/kokoro-82m", voice="af_heart",
    )

    src = tmp_path / "book.md"
    src.write_text(_BOOK, encoding="utf-8")
    out_dir = tmp_path / "out"
    stepfun_cfg = StepFunConfig(
        api_key="x", base_url="http://x",
        model="stepaudio-2.5-tts", voice="lively-girl",
    )

    result = pipeline.run_pipeline(
        src, out_dir, stepfun_cfg, max_chars=1000,
        fallback_factory=lambda: OpenRouterTTSClient(config=fb_cfg),
    )

    chunks = result.chunks
    assert len(chunks) >= 2

    # Chunk 0 was billed at StepFun's rate; every later chunk at Kokoro's.
    primary_chars = chunks[0].char_count
    fallback_chars = sum(c.char_count for c in chunks[1:])
    expected = estimate_cost(primary_chars, "stepaudio-2.5-tts") + estimate_cost(
        fallback_chars, "hexgrad/kokoro-82m"
    )
    assert result.estimated_cost_usd == pytest.approx(expected)

    # The old bug priced ALL characters at the final (Kokoro) rate — >100x cheaper
    # for the StepFun-billed chunk — so the correct estimate is strictly larger.
    total = primary_chars + fallback_chars
    assert result.estimated_cost_usd > estimate_cost(total, "hexgrad/kokoro-82m")
