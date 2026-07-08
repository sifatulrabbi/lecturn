"""The single source of truth for lecturn's local Kokoro TTS contract.

Both apps in this repo speak an OpenAI-compatible Kokoro surface but live in
separate environments (the CLI ``textbook_audiobook`` and the heavy, torch-bound
``lecturn_kokoro_server``). Before this package they each *re-declared* the voice
catalogue, the model id, the default voice, the port, and the token cap — and the
two copies drifted (the CLI knew 18 voices while the server served 54). This
module holds those shared facts ONCE so drift is impossible by construction:

* the server imports it to build its ``/v1/audio/voices`` / ``/v1/models``
  responses and its request defaults;
* the CLI imports it for the ``local``/``openrouter`` (Kokoro) catalogue, the
  default voice/model, and the default base URL.

It is intentionally **dependency-free** and broad on Python version so it imports
cleanly in either environment without pulling anything heavy.
"""

from __future__ import annotations

# --- Model / server defaults ------------------------------------------------

# The bare model id our server advertises and Kokoro-FastAPI reports. The server
# is single-model and accepts any value, but this is the canonical one.
MODEL_ID: str = "kokoro"

# The recommended default voice (US female, grade A in hexgrad's VOICES.md).
DEFAULT_VOICE: str = "af_heart"

# Where the server binds and how the CLI reaches it by default (Kokoro-FastAPI's
# convention). ``DEFAULT_BASE_URL`` is what ``--provider local`` targets unless
# overridden by ``--base-url`` / ``LOCAL_TTS_BASE_URL``.
DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 8880
DEFAULT_BASE_URL: str = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1"

# Audio formats the server can encode. lecturn always requests mp3 explicitly
# (both Kokoro servers default to raw PCM, which would break mp3 cache validation
# and pydub stitching), so mp3 is the shared default.
DEFAULT_RESPONSE_FORMAT: str = "mp3"
SUPPORTED_FORMATS: tuple[str, ...] = ("mp3", "wav")

# --- Limits -----------------------------------------------------------------

# Kokoro accepts up to this many input tokens per request. The CLI enforces it
# client-side with a safety margin (see textbook_audiobook.tts); the constant
# lives here so both sides cite one number.
KOKORO_MAX_INPUT_TOKENS: int = 4096

# Defense-in-depth character cap the server applies to a single request's text.
# The server is unauthenticated, so it must not trust callers to bound their
# input; lecturn itself chunks to <=1000 chars, so this generous ceiling never
# rejects a real lecturn request. NOT the token guard above.
MAX_INPUT_CHARS: int = 10_000

# --- Voice catalogue (single source of truth) -------------------------------

# All 54 built-in Kokoro-82M voices from hexgrad's VOICES.md. Voice IDs are
# namespaced by a two-letter ``<lang><gender>`` prefix; the FIRST letter is the
# KPipeline ``lang_code`` (``a`` US English, ``b`` UK English, ``e`` Spanish,
# ``f`` French, ``h`` Hindi, ``i`` Italian, ``j`` Japanese, ``p`` Brazilian
# Portuguese, ``z`` Mandarin). English voices work out of the box; non-English
# voices need the matching misaki extra on the server. ``af_heart`` (grade A) is
# the default, ``af_bella`` (A-) the runner-up. Dict order drives listings.
KOKORO_VOICES: dict[str, str] = {
    # --- American English (lang_code "a") ---
    "af_heart": "US Female — Heart (A, default)",
    "af_bella": "US Female — Bella (A-)",
    "af_nicole": "US Female — Nicole (B-)",
    "af_aoede": "US Female — Aoede (C+)",
    "af_kore": "US Female — Kore (C+)",
    "af_sarah": "US Female — Sarah (C+)",
    "af_nova": "US Female — Nova (C)",
    "af_sky": "US Female — Sky (C-)",
    "af_alloy": "US Female — Alloy (C)",
    "af_jessica": "US Female — Jessica (D)",
    "af_river": "US Female — River (D)",
    "am_michael": "US Male — Michael (C+)",
    "am_fenrir": "US Male — Fenrir (C+)",
    "am_puck": "US Male — Puck (C+)",
    "am_echo": "US Male — Echo (D)",
    "am_eric": "US Male — Eric (D)",
    "am_liam": "US Male — Liam (D)",
    "am_onyx": "US Male — Onyx (D)",
    "am_santa": "US Male — Santa (D-)",
    "am_adam": "US Male — Adam (F+)",
    # --- British English (lang_code "b") ---
    "bf_emma": "UK Female — Emma (B-)",
    "bf_isabella": "UK Female — Isabella (C)",
    "bf_alice": "UK Female — Alice (D)",
    "bf_lily": "UK Female — Lily (D)",
    "bm_george": "UK Male — George (C)",
    "bm_fable": "UK Male — Fable (C)",
    "bm_lewis": "UK Male — Lewis (D+)",
    "bm_daniel": "UK Male — Daniel (D)",
    # --- Japanese (lang_code "j" — requires misaki[ja]) ---
    "jf_alpha": "JP Female — Alpha (C+)",
    "jf_gongitsune": "JP Female — Gongitsune (C)",
    "jf_nezumi": "JP Female — Nezumi (C-)",
    "jf_tebukuro": "JP Female — Tebukuro (C)",
    "jm_kumo": "JP Male — Kumo (C-)",
    # --- Mandarin Chinese (lang_code "z" — requires misaki[zh]) ---
    "zf_xiaobei": "ZH Female — Xiaobei (D)",
    "zf_xiaoni": "ZH Female — Xiaoni (D)",
    "zf_xiaoxiao": "ZH Female — Xiaoxiao (D)",
    "zf_xiaoyi": "ZH Female — Xiaoyi (D)",
    "zm_yunjian": "ZH Male — Yunjian (D)",
    "zm_yunxi": "ZH Male — Yunxi (D)",
    "zm_yunxia": "ZH Male — Yunxia (D)",
    "zm_yunyang": "ZH Male — Yunyang (D)",
    # --- Spanish (lang_code "e") ---
    "ef_dora": "ES Female — Dora",
    "em_alex": "ES Male — Alex",
    "em_santa": "ES Male — Santa",
    # --- French (lang_code "f") ---
    "ff_siwis": "FR Female — Siwis (B-)",
    # --- Hindi (lang_code "h") ---
    "hf_alpha": "HI Female — Alpha (C)",
    "hf_beta": "HI Female — Beta (C)",
    "hm_omega": "HI Male — Omega (C)",
    "hm_psi": "HI Male — Psi (C)",
    # --- Italian (lang_code "i") ---
    "if_sara": "IT Female — Sara (C)",
    "im_nicola": "IT Male — Nicola (C)",
    # --- Brazilian Portuguese (lang_code "p") ---
    "pf_dora": "BR Female — Dora",
    "pm_alex": "BR Male — Alex",
    "pm_santa": "BR Male — Santa",
}

# Ordered list of voice IDs, e.g. for the /v1/audio/voices listing.
VOICE_IDS: list[str] = list(KOKORO_VOICES)


def is_known_voice(voice: str) -> bool:
    """Return whether ``voice`` is a known Kokoro voice ID."""

    return voice in KOKORO_VOICES
