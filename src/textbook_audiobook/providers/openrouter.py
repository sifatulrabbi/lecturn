"""OpenRouter provider.

OpenRouter exposes an OpenAI-compatible TTS surface at
``https://openrouter.ai/api/v1`` (``POST /audio/speech``), so the shared client
in :mod:`textbook_audiobook.tts` drives it unchanged.

Two non-obvious OpenRouter facts shape the config below:

- **Voices are model-specific.** Each TTS model exposes its own voice set
  (OpenAI models use ``alloy``/``nova``/…; Google Gemini uses ``Zephyr``/``Puck``/…;
  Azure MAI uses names like ``en-US-Harper``). Because a voice valid for one model
  is rejected by another, **automatic model fallback is off by default** here — a
  silent model swap would break the voice.
- **Pricing is per-token, and varies by model/provider.** The
  ``price_per_10k_chars`` figures below are best-effort *approximations* for the
  pre-run estimate only. ``openai/gpt-4o-mini-tts`` is the one confirmed figure
  ($0.60 / 1M input characters ≈ $0.006 / 10k).

The catalogue is a curated static snapshot (keeps the test suite network-free).
The live list can be confirmed for free — without spending TTS quota — via
``GET https://openrouter.ai/api/v1/models?output_modalities=speech``; voices for
a given model are listed on its page at ``https://openrouter.ai/<model-id>``.
"""

from __future__ import annotations

from textbook_audiobook.providers.base import ModelInfo, Provider

# Curated catalogue. Prices are approximate USD per 10k characters (OpenRouter
# bills per token); only openai/gpt-4o-mini-tts is a confirmed figure.
_MODELS: dict[str, ModelInfo] = {
    "openai/gpt-4o-mini-tts": ModelInfo(
        name="openai/gpt-4o-mini-tts",
        price_per_10k_chars=0.006,
        description="OpenAI GPT-4o mini TTS. Default. ~$0.60/1M chars.",
    ),
    # Pinned/dated alias of the above; OpenRouter also accepts this exact slug.
    "openai/gpt-4o-mini-tts-2025-12-15": ModelInfo(
        name="openai/gpt-4o-mini-tts-2025-12-15",
        price_per_10k_chars=0.006,
        description="OpenAI GPT-4o mini TTS (pinned build).",
    ),
    "google/gemini-3.1-flash-tts-preview": ModelInfo(
        name="google/gemini-3.1-flash-tts-preview",
        price_per_10k_chars=0.010,  # approximate
        description="Google Gemini Flash TTS (preview). 30 voices.",
    ),
    "mistralai/voxtral-mini-tts-2603": ModelInfo(
        name="mistralai/voxtral-mini-tts-2603",
        price_per_10k_chars=0.016,  # approximate
        description="Mistral Voxtral Mini TTS. English/French voices.",
    ),
    "hexgrad/kokoro-82m": ModelInfo(
        name="hexgrad/kokoro-82m",
        price_per_10k_chars=0.001,  # approximate; cheapest, open-weights
        description="Kokoro 82M (open-weights). Cheapest; 54 multilingual voices.",
    ),
}

# Voices for the DEFAULT model (openai/gpt-4o-mini-tts). Voices are
# model-specific on OpenRouter — for any other model, check its page:
# https://openrouter.ai/<model-id>
_VOICES: dict[str, str] = {
    "alloy": "Alloy (OpenAI, default)",
    "ash": "Ash (OpenAI)",
    "ballad": "Ballad (OpenAI)",
    "coral": "Coral (OpenAI)",
    "echo": "Echo (OpenAI)",
    "fable": "Fable (OpenAI)",
    "onyx": "Onyx (OpenAI)",
    "nova": "Nova (OpenAI)",
    "sage": "Sage (OpenAI)",
    "shimmer": "Shimmer (OpenAI)",
    "verse": "Verse (OpenAI)",
    "marin": "Marin (OpenAI)",
    "cedar": "Cedar (OpenAI)",
}


class OpenRouterProvider(Provider):
    """OpenRouter TTS (OpenAI-compatible, ``openrouter.ai``)."""

    name = "openrouter"
    label = "OpenRouter"
    default_base_url = "https://openrouter.ai/api/v1"
    base_url_env = "OPENROUTER_BASE_URL"
    api_key_env = ("OPENROUTER_API_KEY",)
    default_model = "openai/gpt-4o-mini-tts"
    default_voice = "alloy"
    # Off by default: OpenRouter voices are model-specific, so swapping the model
    # would break the requested voice. Users can still opt in with --fallback-model.
    default_fallback_model = None
    # OpenAI /audio/speech allows 4096; kept conservative and tunable via --max-chars.
    hard_char_limit = 2000
    concurrency_limit = 4
    default_rpm = 20
    models = _MODELS
    voices = _VOICES

    def explain(self, category: str, exc: Exception) -> str:
        if category == "quota":
            return (
                f"OpenRouter rejected the request: {exc} "
                "Your account is out of credit for this model. Add credit at "
                "https://openrouter.ai/settings/credits, or pick a cheaper model "
                "with --model (e.g. hexgrad/kokoro-82m)."
            )
        if category == "auth":
            return (
                f"OpenRouter authentication failed: {exc} "
                "Check that OPENROUTER_API_KEY is set to a valid key."
            )
        if category == "voice":
            return (
                f"OpenRouter rejected the voice: {exc} "
                "Voices on OpenRouter are model-specific. Run "
                "`list-voices --provider openrouter` for the default model's "
                "voices, or check the model's page at "
                "https://openrouter.ai/<model-id> for the voices it supports."
            )
        if category == "model":
            return (
                f"OpenRouter rejected the model: {exc} "
                "Verify the --model slug (e.g. openai/gpt-4o-mini-tts). See the "
                "live list: GET /api/v1/models?output_modalities=speech."
            )
        return f"OpenRouter request failed: {exc}"
