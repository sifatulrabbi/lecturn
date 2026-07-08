"""End-to-end pipeline tests driving the OpenRouter provider through the seam.

Proves ``run_pipeline``'s ``client_factory`` seam works with
``OpenRouterConfig`` + ``OpenRouterTTSClient``, and that the resume cache — keyed
on voice+format+text, not provider — hits across runs. The network is stubbed to
return real MP3 bytes, exactly as the StepFun pipeline tests do.
"""

from __future__ import annotations

import pytest

from textbook_audiobook import pipeline
from textbook_audiobook.config import OpenRouterConfig
from textbook_audiobook.tts import OpenRouterTTSClient

BOOK = """# A Small Book

## Chapter 1

The first chapter has a single, simple sentence to narrate.

## Chapter 2

The second chapter, likewise, is short and sweet.
"""


@pytest.fixture
def stub_network(monkeypatch, mp3_bytes):
    """Make OpenRouterTTSClient return real MP3 bytes instead of calling the API."""

    counter = {"requests": 0}

    monkeypatch.setattr(OpenRouterTTSClient, "_build_client", lambda self: object())

    def fake_request(self, text, model):
        counter["requests"] += 1
        return mp3_bytes

    monkeypatch.setattr(OpenRouterTTSClient, "_request_audio", fake_request)
    return counter


def _config() -> OpenRouterConfig:
    return OpenRouterConfig(
        api_key="x",
        base_url="http://x",
        model="hexgrad/kokoro-82m",
        voice="af_heart",
    )


def _factory(cfg):
    # The run_pipeline seam now passes the config to the factory, so the client
    # can never drift from the config the cache is fingerprinted against.
    return OpenRouterTTSClient(config=cfg)


def _write_book(tmp_path):
    src = tmp_path / "book.md"
    src.write_text(BOOK, encoding="utf-8")
    return src


def test_full_pipeline_via_openrouter_seam(tmp_path, stub_network, mp3_duration_ms):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"
    cfg = _config()

    result = pipeline.run_pipeline(
        src,
        out_dir,
        cfg,
        max_chars=1000,
        split_by_chapter=False,
        client_factory=_factory,
    )

    assert len(result.assembly.output_files) == 1
    out = result.assembly.output_files[0]
    assert out.exists() and mp3_duration_ms(out) > 0
    assert stub_network["requests"] == len(result.chunks) > 0
    # Cost is computed from Kokoro pricing ($0.0062 / 10k), so a real book costs
    # something — proves estimate_cost resolved the OpenRouter model.
    assert result.estimated_cost_usd > 0


def test_resume_hits_cache_across_runs(tmp_path, stub_network):
    src = _write_book(tmp_path)
    out_dir = tmp_path / "out"
    cfg = _config()

    # cleanup_cache=False: keep the first run's cache so the second run can hit
    # it (a successful run now prunes its own cache by default).
    first = pipeline.run_pipeline(
        src, out_dir, cfg, max_chars=1000, client_factory=_factory,
        cleanup_cache=False,
    )
    n = len(first.chunks)
    assert stub_network["requests"] == n

    # Second run reuses every cached chunk — nothing re-synthesized/re-billed.
    pipeline.run_pipeline(
        src, out_dir, cfg, max_chars=1000, resume=True, client_factory=_factory
    )
    assert stub_network["requests"] == n
