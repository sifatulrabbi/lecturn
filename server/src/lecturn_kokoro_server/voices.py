"""Kokoro-82M voice catalogue (all 54 built-in voices).

Sourced from the shared, dependency-free ``lecturn_tts_contract`` package — the
single source of truth both apps import so the server and lecturn's CLI can never
drift on the catalogue (before the contract they each re-declared it and the two
copies diverged, 54 vs 18). This module keeps its own public names
(``VOICES`` / ``VOICE_IDS`` / ``DEFAULT_VOICE`` / :func:`is_known_voice`) that
:mod:`app` imports, but their values now come straight from the contract.

Voice IDs are namespaced by a two-letter ``<lang><gender>`` prefix; the **first
letter** is the KPipeline ``lang_code`` (``a`` = American English, ``b`` =
British English, ``e`` = Spanish, ``f`` = French, ``h`` = Hindi, ``i`` =
Italian, ``j`` = Japanese, ``p`` = Brazilian Portuguese, ``z`` = Mandarin). See
:func:`lecturn_kokoro_server.engine.lang_code_for`.

English voices work out of the box (``misaki[en]``). Non-English voices require
the matching misaki extra installed (e.g. ``misaki[ja]``, ``misaki[zh]``) — the
server lists them all but a request for one whose G2P is missing surfaces a
clear error from the engine. ``af_heart`` (grade A) is the recommended default,
``af_bella`` (A-) the runner-up.
"""

from __future__ import annotations

import lecturn_tts_contract as contract

# {voice_id: human label}. Order is used for the /v1/audio/voices listing. Copied
# into a fresh dict so the module owns its own object.
VOICES: dict[str, str] = dict(contract.KOKORO_VOICES)

# Ordered list of voice IDs for the /v1/audio/voices response.
VOICE_IDS: list[str] = contract.VOICE_IDS

DEFAULT_VOICE: str = contract.DEFAULT_VOICE


def is_known_voice(voice: str) -> bool:
    """Return whether ``voice`` is a known Kokoro voice ID."""

    return contract.is_known_voice(voice)
