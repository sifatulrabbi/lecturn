"""Self-tests for the shared contract (dependency-free, instant)."""

from __future__ import annotations

import lecturn_tts_contract as contract


def test_voice_catalogue_is_the_full_kokoro_set():
    assert len(contract.KOKORO_VOICES) == 54
    assert contract.VOICE_IDS == list(contract.KOKORO_VOICES)
    # Order-preserving and the default is first.
    assert contract.VOICE_IDS[0] == contract.DEFAULT_VOICE == "af_heart"


def test_is_known_voice():
    assert contract.is_known_voice("af_heart")
    assert contract.is_known_voice("ff_siwis")  # a non-English voice is still known
    assert not contract.is_known_voice("not_a_voice")
    assert not contract.is_known_voice("alloy")  # an OpenAI name, not a Kokoro id


def test_defaults_are_internally_consistent():
    assert contract.MODEL_ID == "kokoro"
    assert contract.DEFAULT_RESPONSE_FORMAT in contract.SUPPORTED_FORMATS
    assert contract.DEFAULT_BASE_URL == (
        f"http://{contract.DEFAULT_HOST}:{contract.DEFAULT_PORT}/v1"
    )
    assert contract.KOKORO_MAX_INPUT_TOKENS == 4096
    assert contract.MAX_INPUT_CHARS > 1000  # comfortably above lecturn's chunk cap
