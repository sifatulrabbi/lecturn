"""Token counting for provider input-size guards.

Some TTS models cap the number of *tokens* per request, not just characters
(Kokoro-82M's ceiling is 4096 tokens). To enforce that before spending a call we
need a chunk's token count. Kokoro has no public tiktoken-compatible tokenizer,
so we approximate with OpenAI's ``o200k_base`` encoding — close enough to guard a
4k ceiling with a safety margin.

⚠️ tiktoken downloads its encoding files on first use, which touches the network.
Acquisition is therefore lazy and defensive: when tiktoken (or its encoding)
cannot be loaded — offline, e.g. the test suite — :func:`count_tokens` falls back
to a conservative character-based heuristic instead of raising or downloading.
The resolved encoder is cached (encoding load is not free) and the acquisition
seam (:func:`_load_tiktoken_encoding`) is monkeypatchable so tests can exercise
the real counting path with a fake encoding and the heuristic path directly —
never a live download.
"""

from __future__ import annotations

from typing import Callable

# tiktoken encoding used as a stand-in for Kokoro's (unpublished) tokenizer.
_ENCODING_NAME = "o200k_base"

# A resolved text -> token-count function.
_Encoder = Callable[[str], int]

# Cached module-level encoder, resolved lazily on first real use so we pay the
# encoding-load cost at most once per process.
_cached_encoder: _Encoder | None = None


def _heuristic(text: str) -> int:
    """Conservative token estimate when no real tokenizer is available.

    English averages roughly four characters per token, so ~3 chars/token
    deliberately *over*-counts: for an input-size guard, erring toward rejecting
    a borderline chunk is safe, silently overrunning a token ceiling is not.
    """

    return max(1, len(text) // 3)


def _load_tiktoken_encoding():
    """Return a tiktoken ``Encoding`` (exposing ``.encode``), or ``None``.

    Isolated behind one function so tests can monkeypatch it to inject a fake
    encoding (exercising the real counting path) or force the heuristic
    fallback. Returns ``None`` — never raises — when tiktoken is absent or its
    encoding cannot be fetched (e.g. no network for the first-use download).
    """

    try:
        import tiktoken  # type: ignore
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:  # pragma: no cover - network/download failure path
        # Any failure fetching the encoding (offline, unknown name, corrupt
        # cache) must degrade to the heuristic, not blow up a synthesis run.
        return None


def _default_encoder() -> _Encoder:
    """Resolve (and cache) the module-level encoder: tiktoken, else heuristic."""

    global _cached_encoder
    if _cached_encoder is not None:
        return _cached_encoder
    encoding = _load_tiktoken_encoding()
    if encoding is None:
        _cached_encoder = _heuristic
    else:
        _cached_encoder = lambda text: len(encoding.encode(text))
    return _cached_encoder


def count_tokens(text: str, *, encoder: _Encoder | None = None) -> int:
    """Return the approximate token count for ``text``.

    ``encoder`` overrides the resolved default — tests inject a fake to avoid a
    live tiktoken download. Otherwise the cached default encoder is used
    (tiktoken's ``o200k_base`` when available, the character heuristic when not).
    """

    fn = encoder if encoder is not None else _default_encoder()
    return fn(text)
