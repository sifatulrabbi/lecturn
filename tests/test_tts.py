"""Tests for the TTS client's retry, error classification, and fallback logic.

These never touch the network: the client is constructed without building a
real OpenAI client, and ``_request_audio`` is stubbed per test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from textbook_audiobook.config import StepFunConfig
from textbook_audiobook.tts import (
    StepFunTTSClient,
    TTSError,
    _FatalError,
    _RetryableError,
    _is_quota_error,
    _is_voice_error,
)


def _client(monkeypatch, **kw) -> StepFunTTSClient:
    # Skip building a real OpenAI client.
    monkeypatch.setattr(StepFunTTSClient, "_build_client", lambda self: object())
    cfg = StepFunConfig(
        api_key="x", base_url="http://x", model="stepaudio-2.5-tts", voice="alloy"
    )
    return StepFunTTSClient(config=cfg, base_backoff=0.001, max_backoff=0.005, **kw)


def test_retries_then_succeeds(monkeypatch, tmp_path):
    c = _client(monkeypatch)
    calls = {"n": 0}

    def req(text, model):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RetryableError("429", retry_after=0.001)
        return b"AUDIO"

    c._request_audio = req
    out = c.synthesize_text("hello", tmp_path / "a.mp3")
    assert out.read_bytes() == b"AUDIO"
    assert c.stats.retries == 2
    assert c.stats.requests == 1


def test_quota_error_fails_fast_without_fallback(monkeypatch, tmp_path):
    c = _client(monkeypatch, fallback_model=None)
    calls = {"n": 0}

    def req(text, model):
        calls["n"] += 1
        raise _FatalError("quota exceeded", category="quota", fallback_eligible=True)

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    # Exactly one attempt — no wasted retries on a permanent error.
    assert calls["n"] == 1
    assert "quota" in str(exc.value).lower()
    assert c.stats.failures == 1


def test_quota_error_triggers_model_fallback(monkeypatch, tmp_path):
    c = _client(monkeypatch, fallback_model="step-tts-2")
    seen_models: list[str] = []

    def req(text, model):
        seen_models.append(model)
        if model == "stepaudio-2.5-tts":
            raise _FatalError("quota", category="quota", fallback_eligible=True)
        return b"FALLBACK_AUDIO"

    c._request_audio = req
    out = c.synthesize_text("hello", tmp_path / "a.mp3")
    assert out.read_bytes() == b"FALLBACK_AUDIO"
    assert seen_models == ["stepaudio-2.5-tts", "step-tts-2"]
    assert c.stats.fallbacks == 1
    assert c.active_model == "step-tts-2"


def test_auth_error_does_not_fall_back(monkeypatch, tmp_path):
    c = _client(monkeypatch, fallback_model="step-tts-2")
    calls = {"n": 0}

    def req(text, model):
        calls["n"] += 1
        raise _FatalError("bad key", category="auth", fallback_eligible=False)

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    # Auth failures are not retried and never trigger a model swap.
    assert calls["n"] == 1
    assert c.stats.fallbacks == 0
    assert "authentication" in str(exc.value).lower() or "key" in str(exc.value).lower()


def test_attempt_count_in_message_is_accurate(monkeypatch, tmp_path):
    c = _client(monkeypatch, max_retries=2)

    def req(text, model):
        raise _RetryableError("503")

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    # max_retries=2 -> 3 attempts total, reported accurately.
    assert "after 3 attempts" in str(exc.value)


class _FakeStatusError(Exception):
    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code


def test_voice_error_fails_fast_no_fallback(monkeypatch, tmp_path):
    c = _client(monkeypatch, fallback_model="step-tts-2")
    calls = {"n": 0}

    def req(text, model):
        calls["n"] += 1
        raise _FatalError(
            "The voice_id (alloy) does not exist",
            category="voice",
            fallback_eligible=False,
        )

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    # A bad voice can't be fixed by swapping models — one attempt, no fallback.
    assert calls["n"] == 1
    assert c.stats.fallbacks == 0
    assert "voice" in str(exc.value).lower()


def test_is_voice_error_detection():
    assert _is_voice_error(Exception("The voice_id (alloy) does not exist"))
    assert _is_voice_error(Exception("invalid voice"))
    assert not _is_voice_error(Exception("model not found"))


def test_is_quota_error_by_status_code():
    assert _is_quota_error(_FakeStatusError(402))
    assert not _is_quota_error(_FakeStatusError(403))


def test_is_quota_error_by_message():
    assert _is_quota_error(Exception("You exceeded your current quota"))
    assert _is_quota_error(Exception("insufficient balance"))
    assert not _is_quota_error(Exception("model not found"))
