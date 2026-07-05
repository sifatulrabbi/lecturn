"""Tests for the token-counting helper.

Never touches the network: the tiktoken path is exercised with a fake encoding
injected via ``_load_tiktoken_encoding``, and the heuristic path is driven
directly — no live download ever happens (the autouse ``_offline_tokenizer``
fixture also forces the heuristic by default).
"""

from __future__ import annotations

from textbook_audiobook import tokens
from textbook_audiobook.tokens import count_tokens


class _FakeEncoding:
    """Stand-in for a tiktoken Encoding: one token per whitespace-split word."""

    def encode(self, text: str) -> list[int]:
        return list(range(len(text.split())))


def test_count_tokens_uses_injected_encoder():
    # An injected encoder wins over any resolution — no download, no heuristic.
    calls = {"n": 0}

    def encoder(text: str) -> int:
        calls["n"] += 1
        return 42

    assert count_tokens("whatever the text is", encoder=encoder) == 42
    assert calls["n"] == 1


def test_count_tokens_heuristic_fallback_directly():
    # ~3 chars/token, with a floor of 1 for any non-empty input.
    assert tokens._heuristic("abcdef") == 2       # 6 // 3
    assert tokens._heuristic("abcdefg") == 2      # 7 // 3
    assert tokens._heuristic("a") == 1            # floor
    assert tokens._heuristic("") == 1             # floor


def test_count_tokens_heuristic_when_tiktoken_unavailable(monkeypatch):
    # With no tiktoken encoding available, the default encoder is the heuristic.
    monkeypatch.setattr(tokens, "_cached_encoder", None)
    monkeypatch.setattr(tokens, "_load_tiktoken_encoding", lambda: None)

    text = "abcdefghijkl"  # 12 chars -> 4 tokens under the heuristic
    assert count_tokens(text) == 4


def test_count_tokens_uses_tiktoken_encoding_when_available(monkeypatch):
    # Real counting code path, but with a fake encoding (no live download).
    monkeypatch.setattr(tokens, "_cached_encoder", None)
    monkeypatch.setattr(tokens, "_load_tiktoken_encoding", _FakeEncoding)

    # Fake encoding tokenises on words: five words -> five tokens.
    assert count_tokens("one two three four five") == 5
    # Result is cached: a second call reuses the resolved encoder.
    assert tokens._cached_encoder is not None


def test_load_tiktoken_encoding_swallows_download_failure(monkeypatch):
    # Simulate tiktoken present but its encoding fetch failing (offline).
    import sys
    import types

    fake_tiktoken = types.ModuleType("tiktoken")

    def boom(name):
        raise RuntimeError("network unavailable")

    fake_tiktoken.get_encoding = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tiktoken", fake_tiktoken)

    # Must degrade to None (heuristic), never raise.
    assert tokens._load_tiktoken_encoding() is None
