"""Tests for provider configuration and cost estimation.

Network-free and key-free: ``from_env`` reads only from the (monkeypatched)
environment, and cost estimation is a pure lookup.
"""

from __future__ import annotations

import pytest

from textbook_audiobook import config
from textbook_audiobook.config import (
    LOCAL_BASE_URL_DEFAULT,
    LOCAL_DEFAULT_MODEL,
    LOCAL_DEFAULT_VOICE,
    OPENROUTER_BASE_URL_DEFAULT,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_VOICE,
    LocalConfig,
    MissingApiKeyError,
    OpenRouterConfig,
    estimate_cost,
)


def test_openrouter_from_env_happy_path(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    cfg = OpenRouterConfig.from_env()

    assert cfg.api_key == "sk-or-test"
    assert cfg.base_url == OPENROUTER_BASE_URL_DEFAULT
    assert cfg.model == OPENROUTER_DEFAULT_MODEL
    assert cfg.voice == OPENROUTER_DEFAULT_VOICE
    # OpenRouter defaults to PCM server-side; we must always send mp3.
    assert cfg.response_format == "mp3"


def test_openrouter_from_env_reads_base_url_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.example/api/v1")

    cfg = OpenRouterConfig.from_env()
    assert cfg.base_url == "https://proxy.example/api/v1"

    # An explicit base_url argument wins over the environment.
    cfg2 = OpenRouterConfig.from_env(base_url="https://arg.example/v1")
    assert cfg2.base_url == "https://arg.example/v1"


def test_openrouter_from_env_accepts_overrides(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = OpenRouterConfig.from_env(
        api_key="explicit-key", model="hexgrad/kokoro-82m", voice="af_bella"
    )
    assert cfg.api_key == "explicit-key"
    assert cfg.voice == "af_bella"


def test_openrouter_from_env_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError) as exc:
        OpenRouterConfig.from_env()
    # The message must name the specific env var the user needs to set.
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_estimate_cost_resolves_kokoro():
    # $0.62 / 1M chars -> $0.0062 / 10k. 20k chars => $0.0124.
    cost = estimate_cost(20_000, "hexgrad/kokoro-82m")
    assert cost == pytest.approx(0.0124)


def test_estimate_cost_still_resolves_stepfun_models():
    # Regression: adding OpenRouter lookup must not change StepFun estimates.
    premium = estimate_cost(10_000, "stepaudio-2.5-tts")
    assert premium == pytest.approx(config.MODELS["stepaudio-2.5-tts"].price_per_10k_chars)


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost(50_000, "made-up-model") == 0.0


def test_kokoro_default_voice_in_catalogue():
    assert OPENROUTER_DEFAULT_VOICE in config.KOKORO_VOICES
    assert OPENROUTER_DEFAULT_MODEL in config.OPENROUTER_MODELS


def test_provider_catalogues_do_not_collide():
    # Disjoint naming is what lets estimate_cost look across all three safely and
    # lets the resume cache skip a provider tag.
    assert set(config.MODELS).isdisjoint(config.OPENROUTER_MODELS)
    assert set(config.MODELS).isdisjoint(config.LOCAL_MODELS)
    assert set(config.OPENROUTER_MODELS).isdisjoint(config.LOCAL_MODELS)
    assert set(config.VOICES).isdisjoint(config.KOKORO_VOICES)


# -- local (self-hosted Kokoro) config --------------------------------------


def test_local_from_env_defaults_need_no_key(monkeypatch):
    """No LOCAL_TTS_* set: a usable config with a placeholder key + localhost."""

    monkeypatch.delenv("LOCAL_TTS_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_TTS_BASE_URL", raising=False)

    cfg = LocalConfig.from_env()

    # Never raises MissingApiKeyError — the key falls back to a placeholder that
    # satisfies the OpenAI SDK's non-empty requirement.
    assert cfg.api_key == "local"
    assert cfg.base_url == LOCAL_BASE_URL_DEFAULT
    assert cfg.model == LOCAL_DEFAULT_MODEL
    assert cfg.voice == LOCAL_DEFAULT_VOICE
    # Kokoro servers default to PCM server-side; we must always send mp3.
    assert cfg.response_format == "mp3"


def test_local_from_env_reads_env_overrides(monkeypatch):
    monkeypatch.setenv("LOCAL_TTS_API_KEY", "proxy-token")
    monkeypatch.setenv("LOCAL_TTS_BASE_URL", "http://192.168.1.5:9000/v1")

    cfg = LocalConfig.from_env()
    assert cfg.api_key == "proxy-token"
    assert cfg.base_url == "http://192.168.1.5:9000/v1"

    # An explicit base_url argument wins over the environment.
    cfg2 = LocalConfig.from_env(base_url="http://arg.example/v1")
    assert cfg2.base_url == "http://arg.example/v1"


def test_estimate_cost_resolves_local_kokoro_to_zero():
    # Self-hosted => free. Resolves via LOCAL_MODELS (explicit 0.0), not a miss.
    assert estimate_cost(1_000_000, LOCAL_DEFAULT_MODEL) == 0.0
    assert LOCAL_DEFAULT_MODEL in config.LOCAL_MODELS
