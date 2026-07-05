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


def test_quota_error_fails_fast_and_is_fallback_eligible(monkeypatch, tmp_path):
    # The base client no longer swaps models itself: on a fallback-eligible
    # fatal error it fails fast, tagging the TTSError so a FallbackTTSClient
    # wrapper can decide whether to switch providers.
    c = _client(monkeypatch)
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
    assert exc.value.fallback_eligible is True
    assert c.stats.failures == 1


def test_auth_error_not_fallback_eligible(monkeypatch, tmp_path):
    c = _client(monkeypatch)
    calls = {"n": 0}

    def req(text, model):
        calls["n"] += 1
        raise _FatalError("bad key", category="auth", fallback_eligible=False)

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    # Auth failures are not retried and are never fallback-eligible.
    assert calls["n"] == 1
    assert exc.value.fallback_eligible is False
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


def test_voice_error_not_fallback_eligible(monkeypatch, tmp_path):
    c = _client(monkeypatch)
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
    # A bad voice can't be fixed by switching providers — one attempt, not
    # fallback-eligible.
    assert calls["n"] == 1
    assert exc.value.fallback_eligible is False
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


# -- atomic write -----------------------------------------------------------


def test_atomic_write_success(tmp_path):
    from textbook_audiobook import tts

    target = tmp_path / "chunk.mp3"
    tts._atomic_write_bytes(target, b"hello-audio")
    assert target.read_bytes() == b"hello-audio"
    assert list(tmp_path.glob("*.part")) == []  # no temp left behind


def test_atomic_write_leaves_no_partial_on_failure(tmp_path, monkeypatch):
    """If the rename fails mid-write, no partial destination and no temp remain."""

    from textbook_audiobook import tts

    def boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(tts.os, "replace", boom)
    target = tmp_path / "chunk.mp3"
    with pytest.raises(OSError):
        tts._atomic_write_bytes(target, b"data")

    assert not target.exists()                   # destination never appears partial
    assert list(tmp_path.glob("*.part")) == []   # temp cleaned up
