"""Tests for provider configuration and cost estimation.

Network-free and key-free: ``from_env`` reads only from the (monkeypatched)
environment, and cost estimation is a pure lookup.
"""

from __future__ import annotations

import pytest

from textbook_audiobook import config
from textbook_audiobook.config import (
    OPENROUTER_BASE_URL_DEFAULT,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_VOICE,
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
    # Disjoint naming is what lets estimate_cost look across both safely and lets
    # the resume cache skip a provider tag.
    assert set(config.MODELS).isdisjoint(config.OPENROUTER_MODELS)
    assert set(config.VOICES).isdisjoint(config.KOKORO_VOICES)
