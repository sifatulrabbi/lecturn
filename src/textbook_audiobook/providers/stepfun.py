"""StepFun provider.

Talks to StepFun's OpenAI-compatible surface at ``https://api.stepfun.ai/v1``
via the shared client in :mod:`textbook_audiobook.tts`.

Non-obvious StepFun facts baked into the catalogue/advice below (all learned the
hard way — see ``docs/DEV.md``):

- **Voice access is per-account.** A valid catalogue voice can still return
  ``voice_id_invalid``. The English-keyed voices (e.g. ``lively-girl``, the
  default) tend to be the most broadly available.
- **Quota is per-model.** ``stepaudio-2.5-tts`` (premium) can be out of quota
  (``402``) while ``step-tts-2`` (economy) works — hence the premium→economy
  default fallback, which is safe because StepFun voices work across both models.
- ``step-tts-mini`` is **not** a live model and is deliberately absent.
"""

from __future__ import annotations

from textbook_audiobook.providers.base import ModelInfo, Provider

# Confirmed models and pricing (USD per 10k characters).
_MODELS: dict[str, ModelInfo] = {
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
    # NOTE: "step-tts-mini" is NOT returned by the live GET /v1/models endpoint,
    # so it is deliberately omitted — offering it would only produce a
    # model-rejected error.
}

# StepFun's own voice catalogue (voice IDs accepted by the "voice" parameter),
# mirrored from StepFun's published TTS docs voice table. These are NOT OpenAI
# voice names. StepFun also supports voice cloning (out of scope for v1).
#
# IMPORTANT: presence here means the voice exists in StepFun's catalogue, NOT
# that it is enabled for your account. Voice access is granted per-account; an
# ID can return `voice_id_invalid` ("you do not have access to it") even though
# it is a valid catalogue entry. The English-keyed voices (lively-girl,
# elegantgentle-female, vibrant-youth, etc.) appear in StepFun's quick-start
# examples and tend to be the most widely available — prefer them if a
# Pinyin-keyed voice is rejected.
_VOICES: dict[str, str] = {
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


class StepFunProvider(Provider):
    """StepFun TTS (OpenAI-compatible, ``api.stepfun.ai``)."""

    name = "stepfun"
    label = "StepFun"
    default_base_url = "https://api.stepfun.ai/v1"
    base_url_env = "STEPFUN_BASE_URL"
    api_key_env = ("STEPFUN_API_KEY", "STEPFUN_STEP_PLAN_API_KEY")
    default_model = "stepaudio-2.5-tts"
    # English-keyed and broadly available across accounts (the previous default,
    # "cixingnansheng", returned voice_id_invalid on a test account).
    default_voice = "lively-girl"
    # Premium→economy fallback is safe: StepFun voices work across both models.
    default_fallback_model = "step-tts-2"
    # StepFun's documented hard cap, per the Audio Usage Limits page.
    hard_char_limit = 1000
    concurrency_limit = 5
    default_rpm = 10
    models = _MODELS
    voices = _VOICES

    def explain(self, category: str, exc: Exception) -> str:
        if category == "quota":
            return (
                f"StepFun rejected the request: {exc} "
                "Your account has no remaining quota/credit for this model. "
                "Add credit or switch plans at https://platform.stepfun.ai, or "
                "try the economy model with --model step-tts-2."
            )
        if category == "auth":
            return (
                f"StepFun authentication failed: {exc} "
                "Check that STEPFUN_API_KEY is set to a valid key."
            )
        if category == "voice":
            return (
                f"StepFun rejected the voice: {exc} "
                "This voice ID may not be enabled for your account (access is "
                "per-account). Run `list-voices --provider stepfun` and try "
                "another — the English-keyed voices (e.g. 'lively-girl', "
                "'elegantgentle-female') are the most widely available. Note: "
                "OpenAI names like 'alloy' are not valid StepFun voices."
            )
        if category == "model":
            return (
                f"StepFun rejected the model: {exc} "
                "Verify the --model name (e.g. stepaudio-2.5-tts or step-tts-2)."
            )
        return f"StepFun request failed: {exc}"
