"""Kokoro-82M voice catalogue (all 54 built-in voices).

The full multilingual set published in hexgrad's VOICES.md. Voice IDs are
namespaced by a two-letter ``<lang><gender>`` prefix; the **first letter** is
the KPipeline ``lang_code`` (``a`` = American English, ``b`` = British English,
``e`` = Spanish, ``f`` = French, ``h`` = Hindi, ``i`` = Italian, ``j`` =
Japanese, ``p`` = Brazilian Portuguese, ``z`` = Mandarin). See
:func:`lecturn_kokoro_server.engine.lang_code_for`.

English voices work out of the box (``misaki[en]``). Non-English voices require
the matching misaki extra installed (e.g. ``misaki[ja]``, ``misaki[zh]``) — the
server lists them all but a request for one whose G2P is missing surfaces a
clear error from the engine. ``af_heart`` (grade A) is the recommended default,
``af_bella`` (A-) the runner-up.
"""

from __future__ import annotations

# {voice_id: human label}. Order is used for the /v1/audio/voices listing.
VOICES: dict[str, str] = {
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

# Ordered list of voice IDs for the /v1/audio/voices response.
VOICE_IDS: list[str] = list(VOICES)

DEFAULT_VOICE: str = "af_heart"


def is_known_voice(voice: str) -> bool:
    """Return whether ``voice`` is a known Kokoro voice ID."""

    return voice in VOICES
