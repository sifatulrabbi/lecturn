"""Tests for the provider abstraction and registry.

Network-free and key-free: every environment interaction is monkeypatched.
"""

from __future__ import annotations

import pytest

from textbook_audiobook.config import MissingApiKeyError, TTSConfig
from textbook_audiobook.providers import (
    UnknownProviderError,
    available_providers,
    get_provider,
)
from textbook_audiobook.providers.base import Provider
from textbook_audiobook.providers.openrouter import OpenRouterProvider
from textbook_audiobook.providers.stepfun import StepFunProvider

ALL = [StepFunProvider(), OpenRouterProvider()]


# -- registry ---------------------------------------------------------------


def test_available_providers():
    assert available_providers() == ["stepfun", "openrouter"]


def test_get_provider_returns_instance():
    assert get_provider("stepfun").name == "stepfun"
    assert get_provider("openrouter").name == "openrouter"


def test_get_provider_unknown_raises_with_valid_names():
    with pytest.raises(UnknownProviderError) as exc:
        get_provider("bogus")
    msg = str(exc.value)
    assert "bogus" in msg
    assert "stepfun" in msg and "openrouter" in msg


# -- required attributes & catalogues ---------------------------------------


@pytest.mark.parametrize("prov", ALL, ids=lambda p: p.name)
def test_provider_has_required_shape(prov: Provider):
    assert isinstance(prov.name, str) and prov.name
    assert isinstance(prov.label, str) and prov.label
    assert prov.default_base_url.startswith("http")
    assert prov.api_key_env  # non-empty
    assert prov.default_model in prov.models
    assert prov.default_voice in prov.voices
    assert prov.hard_char_limit > 0
    assert prov.concurrency_limit >= 1
    assert prov.default_rpm >= 0
    assert prov.models and prov.voices


def test_char_limits():
    assert StepFunProvider().hard_char_limit == 1000
    assert OpenRouterProvider().hard_char_limit == 2000


def test_fallback_defaults():
    # StepFun voices work across both models -> economy fallback is safe.
    assert StepFunProvider().default_fallback_model == "step-tts-2"
    # OpenRouter voices are model-specific -> no automatic fallback.
    assert OpenRouterProvider().default_fallback_model is None


def test_openrouter_defaults():
    prov = OpenRouterProvider()
    assert prov.default_model == "openai/gpt-4o-mini-tts"
    assert prov.default_voice == "alloy"


# -- credentials ------------------------------------------------------------


def test_resolve_api_key_reads_first_env(monkeypatch):
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
    monkeypatch.delenv("STEPFUN_STEP_PLAN_API_KEY", raising=False)
    prov = StepFunProvider()
    assert prov.resolve_api_key() is None
    monkeypatch.setenv("STEPFUN_STEP_PLAN_API_KEY", "second")
    assert prov.resolve_api_key() == "second"
    monkeypatch.setenv("STEPFUN_API_KEY", "first")
    assert prov.resolve_api_key() == "first"  # first in the tuple wins


def test_openrouter_reads_its_own_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    prov = OpenRouterProvider()
    assert prov.resolve_api_key() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert prov.resolve_api_key() == "or-key"


def test_tts_config_resolve_missing_key_names_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError) as exc:
        TTSConfig.resolve(OpenRouterProvider())
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_tts_config_resolve_fills_defaults(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    cfg = TTSConfig.resolve(OpenRouterProvider())
    assert cfg.api_key == "or-key"
    assert cfg.base_url == "https://openrouter.ai/api/v1"
    assert cfg.model == "openai/gpt-4o-mini-tts"
    assert cfg.voice == "alloy"
    assert cfg.hard_char_limit == 2000


def test_resolve_base_url_precedence(monkeypatch):
    prov = StepFunProvider()
    monkeypatch.delenv("STEPFUN_BASE_URL", raising=False)
    assert prov.resolve_base_url() == prov.default_base_url
    monkeypatch.setenv("STEPFUN_BASE_URL", "http://env")
    assert prov.resolve_base_url() == "http://env"
    assert prov.resolve_base_url("http://explicit") == "http://explicit"  # arg wins


# -- pricing ----------------------------------------------------------------


def test_estimate_cost_uses_catalogue():
    prov = StepFunProvider()
    price = prov.models["stepaudio-2.5-tts"].price_per_10k_chars
    assert prov.estimate_cost(10_000, "stepaudio-2.5-tts") == pytest.approx(price)
    # Unknown model estimates to 0.0 (CLI warns separately).
    assert prov.estimate_cost(10_000, "does-not-exist") == 0.0


# -- error advice -----------------------------------------------------------


def test_explain_is_provider_specific():
    step = StepFunProvider().explain("quota", Exception("402"))
    assert "stepfun" in step.lower()

    orouter = OpenRouterProvider().explain("quota", Exception("402"))
    assert "openrouter" in orouter.lower()

    voice_msg = OpenRouterProvider().explain("voice", Exception("bad voice"))
    assert "model-specific" in voice_msg.lower()
