"""Tests for the OpenRouter TTS client.

Mirrors ``tests/test_tts.py``: the network is never touched — ``_build_client``
is replaced with a fake and ``_request_audio`` is stubbed per test. One test
drives the *real* ``_request_audio`` through a fake OpenAI client to prove the
request always carries ``response_format="mp3"`` (guarding OpenRouter's PCM
default) plus the configured voice and model.
"""

from __future__ import annotations

import pytest

from textbook_audiobook import pipeline
from textbook_audiobook import tts as tts_module
from textbook_audiobook.config import OpenRouterConfig
from textbook_audiobook.tts import (
    OpenRouterTTSClient,
    TTSError,
    _FatalError,
    _RetryableError,
)


def _config() -> OpenRouterConfig:
    return OpenRouterConfig(
        api_key="x",
        base_url="http://x",
        model="hexgrad/kokoro-82m",
        voice="af_heart",
    )


def _client(monkeypatch, **kw) -> OpenRouterTTSClient:
    # Skip building a real OpenAI client.
    monkeypatch.setattr(OpenRouterTTSClient, "_build_client", lambda self: object())
    return OpenRouterTTSClient(
        config=_config(), base_backoff=0.001, max_backoff=0.005, **kw
    )


# -- happy path + atomic write ---------------------------------------------


def test_happy_path_writes_playable_mp3_atomically(monkeypatch, tmp_path, mp3_bytes):
    c = _client(monkeypatch)
    c._request_audio = lambda text, model: mp3_bytes

    out = c.synthesize_text("hello", tmp_path / "a.mp3")

    assert out.exists()
    assert pipeline._is_playable_mp3(out)          # real, reusable MP3
    assert list(tmp_path.glob("*.part")) == []     # no temp left behind
    assert c.stats.requests == 1
    assert c.stats.characters == len("hello")


# -- retry / backoff --------------------------------------------------------


def test_retries_then_succeeds(monkeypatch, tmp_path, mp3_bytes):
    c = _client(monkeypatch)
    calls = {"n": 0}

    def req(text, model):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RetryableError("429", retry_after=0.001)
        return mp3_bytes

    c._request_audio = req
    out = c.synthesize_text("hello", tmp_path / "a.mp3")
    assert pipeline._is_playable_mp3(out)
    assert c.stats.retries == 2
    assert c.stats.requests == 1


# -- fatal errors -----------------------------------------------------------


def test_auth_error_message_names_openrouter_key(monkeypatch, tmp_path):
    c = _client(monkeypatch)

    def req(text, model):
        raise _FatalError("invalid key", category="auth", fallback_eligible=False)

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    assert "OPENROUTER_API_KEY" in str(exc.value)
    assert c.stats.failures == 1


def test_no_fallback_attempted_by_default(monkeypatch, tmp_path):
    # Default fallback_model=None: a fallback-eligible quota error must NOT retry
    # a second model — Kokoro is the only OpenRouter model.
    c = _client(monkeypatch)
    seen_models: list[str] = []

    def req(text, model):
        seen_models.append(model)
        raise _FatalError("out of credits", category="quota", fallback_eligible=True)

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    assert seen_models == ["hexgrad/kokoro-82m"]   # exactly one model tried
    assert c.stats.fallbacks == 0
    assert "credit" in str(exc.value).lower()


# -- input token guard ------------------------------------------------------


def test_over_token_limit_raises_before_any_request(monkeypatch, tmp_path):
    """A chunk over Kokoro's token cap fails fast, before spending a call.

    HARD_CHAR_LIMIT keeps real chunks far under the cap, so we force a large
    token count to exercise the guard. It must raise a clear TTSError and never
    reach the network.
    """

    c = _client(monkeypatch)

    def must_not_run(text, model):  # pragma: no cover - asserts it's unreachable
        raise AssertionError("guard must reject the chunk before any request")

    c._request_audio = must_not_run
    # Report a token count above Kokoro's 4096 cap (well past the safety margin).
    monkeypatch.setattr(tts_module, "count_tokens", lambda text: 5000)

    with pytest.raises(TTSError) as exc:
        c.synthesize_text("does not matter", tmp_path / "a.mp3")
    msg = str(exc.value).lower()
    assert "token" in msg
    assert "hexgrad/kokoro-82m" in str(exc.value)


def test_within_token_limit_synthesizes(monkeypatch, tmp_path, mp3_bytes):
    """A normal-sized chunk passes the guard (heuristic count, no download)."""

    c = _client(monkeypatch)
    c._request_audio = lambda text, model: mp3_bytes
    # Default (offline) heuristic count for a short string is tiny — guard inert.
    out = c.synthesize_text("a short chunk of text", tmp_path / "a.mp3")
    assert pipeline._is_playable_mp3(out)


def test_voice_error_message_points_at_list_voices(monkeypatch, tmp_path):
    c = _client(monkeypatch)

    def req(text, model):
        raise _FatalError("bad voice", category="voice", fallback_eligible=False)

    c._request_audio = req
    with pytest.raises(TTSError) as exc:
        c.synthesize_text("hello", tmp_path / "a.mp3")
    assert "list-voices --provider openrouter" in str(exc.value)


# -- request shape (guards the PCM-default gotcha) --------------------------


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content


class _FakeSpeech:
    def __init__(self, captured: dict, payload: bytes) -> None:
        self._captured = captured
        self._payload = payload

    def create(self, **kwargs):
        self._captured.clear()
        self._captured.update(kwargs)
        return _FakeResponse(self._payload)


class _FakeAudio:
    def __init__(self, speech: _FakeSpeech) -> None:
        self.speech = speech


class _FakeOpenAIClient:
    def __init__(self, captured: dict, payload: bytes) -> None:
        self.audio = _FakeAudio(_FakeSpeech(captured, payload))


def test_request_sends_mp3_and_configured_voice_model(monkeypatch, tmp_path, mp3_bytes):
    """The real _request_audio must always send response_format='mp3'.

    OpenRouter defaults to raw PCM, which would break MP3 cache validation and
    pydub stitching, so this asserts on the kwargs actually passed to the client.
    """

    captured: dict = {}
    monkeypatch.setattr(
        OpenRouterTTSClient,
        "_build_client",
        lambda self: _FakeOpenAIClient(captured, mp3_bytes),
    )
    c = OpenRouterTTSClient(config=_config())

    out = c.synthesize_text("read this", tmp_path / "a.mp3")

    assert pipeline._is_playable_mp3(out)
    assert captured["response_format"] == "mp3"
    assert captured["voice"] == "af_heart"
    assert captured["model"] == "hexgrad/kokoro-82m"
    assert captured["input"] == "read this"
