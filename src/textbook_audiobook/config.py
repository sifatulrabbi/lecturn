"""Configuration and constants for the TTS providers.

Two OpenAI-compatible providers are supported: StepFun (the original) and
OpenRouter (Kokoro-82M). Each has its own base URL, API-key env var, model
pricing, and voice catalogue, but both speak the same ``POST /audio/speech``
shape so a single transport (the OpenAI SDK) drives both.

Secrets are read from the environment only — never hard-coded. See PLAN.md
("API Key") for the accepted environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# StepFun's documented hard cap, per the Audio Usage Limits page. This is an
# API constraint, NOT a tuning parameter — chunks must never exceed it. It is
# applied uniformly across providers: OpenRouter documents no hard limit, but a
# single cap keeps chunking (and therefore the resume cache) portable between
# providers.
HARD_CHAR_LIMIT: int = 1000

DEFAULT_BASE_URL: str = "https://api.stepfun.ai/v1"
DEFAULT_MODEL: str = "stepaudio-2.5-tts"
ECONOMY_MODEL: str = "step-tts-2"
# StepFun uses its own voice IDs (NOT OpenAI names like "alloy"). This default
# is the voice used in StepFun's current TTS quick-start example, and is one of
# the English-keyed voices that appear to be broadly available across accounts.
# (The previous default, "cixingnansheng", is in the catalogue but returned
# voice_id_invalid / "you do not have access to it" on a test account — voice
# entitlement is per-account, so prefer a default with the widest availability.)
DEFAULT_VOICE: str = "lively-girl"
DEFAULT_RESPONSE_FORMAT: str = "mp3"

# --- OpenRouter (Kokoro-82M) -------------------------------------------------
# OpenRouter exposes an OpenAI-compatible POST /api/v1/audio/speech endpoint, so
# the same transport is reused with a different base URL, key, and catalogue.
OPENROUTER_BASE_URL_DEFAULT: str = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL: str = "hexgrad/kokoro-82m"
# Kokoro's highest-graded voice (US female, grade A in hexgrad's VOICES.md).
OPENROUTER_DEFAULT_VOICE: str = "af_heart"


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a TTS model, including pricing for cost estimation."""

    name: str
    # USD per 10,000 characters (PLAN.md "Pricing").
    price_per_10k_chars: float
    description: str


# Confirmed models and pricing from PLAN.md.
MODELS: dict[str, ModelInfo] = {
    "stepaudio-2.5-tts": ModelInfo(
        name="stepaudio-2.5-tts",
        price_per_10k_chars=0.85,
        description="Default model, best audio quality.",
    ),
    "step-tts-2": ModelInfo(
        name="step-tts-2",
        price_per_10k_chars=0.40,
        description="Economy alternative, lower cost.",
    ),
    # NOTE: "step-tts-mini" was previously listed here but is NOT returned by the
    # live GET /v1/models endpoint, so it has been removed — offering it would
    # only produce a model-rejected error. The two models above are confirmed
    # present in the live model list.
}

# StepFun's own voice catalogue (voice IDs accepted by the "voice" parameter),
# mirrored from StepFun's published TTS docs voice table. These are NOT OpenAI
# voice names. Surfaced via `--list-voices`. StepFun also supports voice cloning
# (out of scope for v1).
#
# IMPORTANT: presence here means the voice exists in StepFun's catalogue, NOT
# that it is enabled for your account. Voice access is granted per-account; an
# ID can return `voice_id_invalid` ("you do not have access to it") even though
# it is a valid catalogue entry. The English-keyed voices (lively-girl,
# elegantgentle-female, vibrant-youth, etc.) appear in StepFun's quick-start
# examples and tend to be the most widely available — prefer them if a
# Pinyin-keyed voice is rejected.
VOICES: dict[str, str] = {
    # Male voices
    "cixingnansheng": "Magnetic Male",
    "wenrounansheng": "Gentle Male",
    "wenrougongzi": "Tender Gentleman",
    "yuanqinansheng": "Spirited Male",
    "ruyananshi": "Scholarly Gentleman",
    "boyinnansheng": "Broadcast Male",
    "shenchennanyin": "Deep Male",
    "zixinnansheng": "Confident Male",
    "shuangkuainansheng": "Straightforward Male",
    "zhengpaiqingnian": "Upright Youth",
    "qingniandaxuesheng": "College Student",
    "magnetic-voiced-male": "Magnetic Male (English-keyed)",
    "soft-spoken-gentleman": "Soft-spoken Gentleman (English-keyed)",
    # Female voices
    "qingchunshaonv": "Pure Girl",
    "yuanqishaonv": "Spirited Girl",
    "linjiajiejie": "Older Sister Next Door",
    "linjiameimei": "Younger Sister Next Door",
    "jingdiannvsheng": "Classic Female",
    "tianmeinvsheng": "Sweet Female",
    "wenrounvsheng": "Gentle Female",
    "wenroushunv": "Gentle Maiden",
    "wenjingxuejie": "Quiet Senior Schoolmate",
    "ruanmengnvsheng": "Cute Soft Female",
    "ganliannvsheng": "Capable Female",
    "qinhenvsheng": "Warm Female",
    "qinqienvsheng": "Friendly Female",
    "huolinvsheng": "Energetic Female",
    "youyanvsheng": "Elegant Female",
    "lengyanyujie": "Cool Beauty",
    "zhixingjiejie": "Intellectual Lady",
    "shuangkuaijiejie": "Bold Sister",
    "jilingshaonv": "Clever Girl",
    "lively-girl": "Lively Girl (English-keyed, docs default)",
    "livelybreezy-female": "Lively Breezy Female (English-keyed)",
    "elegantgentle-female": "Elegant Gentle Female (English-keyed)",
    "vibrant-youth": "Vibrant Youth (English-keyed)",
}

# Ordered list of voice IDs (keys of VOICES) for convenience.
KNOWN_VOICES: list[str] = list(VOICES)


# OpenRouter's TTS catalogue. Only Kokoro-82M is offered for now. Pricing is
# $0.62 per 1M characters => $0.0062 per 10k, versus StepFun's premium ($0.85)
# and economy ($0.40). Names are namespaced (``hexgrad/kokoro-82m``) so they can
# never collide with StepFun's flat model IDs — see estimate_cost().
OPENROUTER_MODELS: dict[str, ModelInfo] = {
    "hexgrad/kokoro-82m": ModelInfo(
        name="hexgrad/kokoro-82m",
        price_per_10k_chars=0.0062,
        description="Kokoro-82M — lightweight open-weight TTS (via OpenRouter)",
    ),
}

# Kokoro's built-in English voices, keyed by voice ID with a human label and a
# quality grade taken from hexgrad's VOICES.md. Prefix convention: ``af_``/``am_``
# = US female/male, ``bf_``/``bm_`` = UK female/male. Kokoro voice IDs cannot
# collide with StepFun's, so the resume cache needs no provider tag. The default
# is ``af_heart`` (grade A). Mirrors the {id: label} shape of VOICES.
KOKORO_VOICES: dict[str, str] = {
    # US female
    "af_heart": "US Female — Heart (A, default)",
    "af_bella": "US Female — Bella (A-)",
    "af_nicole": "US Female — Nicole (B-)",
    "af_aoede": "US Female — Aoede",
    "af_kore": "US Female — Kore",
    "af_sarah": "US Female — Sarah (C+)",
    "af_alloy": "US Female — Alloy",
    "af_nova": "US Female — Nova (C)",
    "af_sky": "US Female — Sky (C-)",
    # US male
    "am_michael": "US Male — Michael",
    "am_fenrir": "US Male — Fenrir",
    "am_puck": "US Male — Puck (C+)",
    "am_onyx": "US Male — Onyx",
    "am_echo": "US Male — Echo (D)",
    # UK female
    "bf_emma": "UK Female — Emma (B-)",
    "bf_isabella": "UK Female — Isabella (C)",
    # UK male
    "bm_fable": "UK Male — Fable",
    "bm_george": "UK Male — George (C)",
}

# Ordered list of Kokoro voice IDs for convenience.
KNOWN_KOKORO_VOICES: list[str] = list(KOKORO_VOICES)


@dataclass
class StepFunConfig:
    """Resolved runtime configuration for talking to StepFun."""

    api_key: str
    base_url: str
    model: str
    voice: str
    response_format: str = DEFAULT_RESPONSE_FORMAT

    @classmethod
    def from_env(
        cls,
        *,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        response_format: str = DEFAULT_RESPONSE_FORMAT,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> "StepFunConfig":
        resolved_key = api_key or resolve_api_key()
        if not resolved_key:
            raise MissingApiKeyError(
                "No StepFun API key found. Set the STEPFUN_API_KEY environment "
                "variable (or STEPFUN_STEP_PLAN_API_KEY)."
            )
        resolved_base = (
            base_url
            or os.environ.get("STEPFUN_BASE_URL")
            or DEFAULT_BASE_URL
        )
        return cls(
            api_key=resolved_key,
            base_url=resolved_base,
            model=model,
            voice=voice,
            response_format=response_format,
        )


@dataclass
class OpenRouterConfig:
    """Resolved runtime configuration for talking to OpenRouter.

    Mirrors :class:`StepFunConfig` (same duck-typed ``.model`` / ``.voice`` /
    ``.response_format`` surface the pipeline relies on) so both providers flow
    through the same transport and cache.
    """

    api_key: str
    base_url: str
    model: str
    voice: str
    # MUST default to (and always send) "mp3": OpenRouter's /audio/speech
    # defaults to raw PCM, which would break the pipeline's MP3 magic-byte cache
    # validation and pydub stitching. Never omit response_format in a request.
    response_format: str = DEFAULT_RESPONSE_FORMAT

    @classmethod
    def from_env(
        cls,
        *,
        model: str = OPENROUTER_DEFAULT_MODEL,
        voice: str = OPENROUTER_DEFAULT_VOICE,
        response_format: str = DEFAULT_RESPONSE_FORMAT,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> "OpenRouterConfig":
        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not resolved_key:
            raise MissingApiKeyError(
                "No OpenRouter API key found. Set the OPENROUTER_API_KEY "
                "environment variable."
            )
        resolved_base = (
            base_url
            or os.environ.get("OPENROUTER_BASE_URL")
            or OPENROUTER_BASE_URL_DEFAULT
        )
        return cls(
            api_key=resolved_key,
            base_url=resolved_base,
            model=model,
            voice=voice,
            response_format=response_format,
        )


# The pipeline and transport only touch ``.model`` / ``.voice`` /
# ``.response_format`` / ``.api_key`` / ``.base_url``, which both configs share.
# This union is the type used wherever "a config for either provider" is meant.
TTSConfig = StepFunConfig | OpenRouterConfig


class MissingApiKeyError(RuntimeError):
    """Raised when no usable StepFun API key is present in the environment."""


def resolve_api_key() -> str | None:
    """Return the first available StepFun API key from the environment."""

    return (
        os.environ.get("STEPFUN_API_KEY")
        or os.environ.get("STEPFUN_STEP_PLAN_API_KEY")
    )


def estimate_cost(char_count: int, model: str) -> float:
    """Estimate USD cost for narrating ``char_count`` characters with ``model``.

    Looks the model up in the StepFun catalogue first, then OpenRouter's. The
    two catalogues use disjoint naming (StepFun's flat IDs vs OpenRouter's
    ``vendor/model`` form), so lookup order is immaterial — an unknown model in
    either still returns 0.0, unchanged.
    """

    info = MODELS.get(model) or OPENROUTER_MODELS.get(model)
    if info is None:
        return 0.0
    return (char_count / 10_000) * info.price_per_10k_chars
