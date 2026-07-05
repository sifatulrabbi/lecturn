"""CLI-level tests using Typer's CliRunner.

These cover argument validation, the catalogue commands, and the dry-run path —
none of which touch the network. The one place we would hit the API
(`convert` without --dry-run) is covered in test_pipeline.py instead.
"""

from __future__ import annotations

from typer.testing import CliRunner

from textbook_audiobook import __version__
from textbook_audiobook.cli import app
from textbook_audiobook.config import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    MODELS,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_VOICE,
    VOICES,
)

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_list_models_lists_real_models():
    result = runner.invoke(app, ["list-models"])
    assert result.exit_code == 0
    for name in MODELS:
        assert name in result.stdout
    # The removed phantom model must not reappear.
    assert "step-tts-mini" not in result.stdout


def test_list_voices_includes_default():
    result = runner.invoke(app, ["list-voices"])
    assert result.exit_code == 0
    assert DEFAULT_VOICE in result.stdout
    # Spot-check a couple of catalogue entries render.
    assert "lively-girl" in result.stdout


def test_default_voice_is_in_catalogue():
    assert DEFAULT_VOICE in VOICES


def test_dry_run_produces_plan_and_no_output(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# Title\n\n## Ch1\n\nHello world. This is a test.\n", "utf-8")
    out = tmp_path / "out"

    result = runner.invoke(
        app, ["convert", str(book), "--dry-run", "--output", str(out)]
    )
    assert result.exit_code == 0
    assert "Dry run" in result.stdout
    # No audio written.
    assert not out.exists() or not any(out.glob("*.mp3"))


def test_max_chars_over_limit_rejected(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# T\n\nText.\n", "utf-8")
    result = runner.invoke(
        app, ["convert", str(book), "--dry-run", "--max-chars", "5000"]
    )
    assert result.exit_code == 2
    assert "hard limit" in result.stdout


def test_max_chars_zero_rejected(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# T\n\nText.\n", "utf-8")
    result = runner.invoke(
        app, ["convert", str(book), "--dry-run", "--max-chars", "0"]
    )
    assert result.exit_code == 2
    assert "positive" in result.stdout


def test_unsupported_file_exits_with_error(tmp_path):
    bad = tmp_path / "book.xyz"
    bad.write_text("hello", "utf-8")
    result = runner.invoke(app, ["convert", str(bad), "--dry-run"])
    assert result.exit_code == 1
    assert "load" in result.stdout.lower() or "unsupported" in result.stdout.lower()


def test_unknown_model_warns_but_proceeds_in_dry_run(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# T\n\nText here.\n", "utf-8")
    result = runner.invoke(
        app, ["convert", str(book), "--dry-run", "--model", "made-up-model"]
    )
    # Dry run still succeeds; a warning is shown.
    assert result.exit_code == 0
    assert "not a known model" in result.stdout


# -- provider selection -----------------------------------------------------


def test_default_provider_is_stepfun(tmp_path):
    """No --provider ⇒ the StepFun provider, model, and voice (unchanged path)."""

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate.\n", "utf-8")
    result = runner.invoke(app, ["convert", str(book), "--dry-run"])
    assert result.exit_code == 0
    assert "stepfun" in result.stdout
    assert DEFAULT_MODEL in result.stdout       # stepaudio-2.5-tts
    assert DEFAULT_VOICE in result.stdout       # lively-girl


def test_openrouter_dry_run_needs_no_key(tmp_path, monkeypatch):
    """`--dry-run --provider openrouter` works with NO API keys set."""

    # Prove no key is required by clearing every provider key from the env.
    for var in (
        "STEPFUN_API_KEY", "STEPFUN_STEP_PLAN_API_KEY", "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    book = tmp_path / "book.txt"
    book.write_text(
        "Kokoro Test Book\n\nHello world. A short paragraph to narrate.\n",
        "utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["convert", str(book), "--dry-run", "--provider", "openrouter",
         "--output", str(out)],
    )
    assert result.exit_code == 0
    assert "Dry run" in result.stdout
    # Reports the OpenRouter provider, its default model + voice (⇒ kokoro pricing).
    assert "openrouter" in result.stdout
    assert OPENROUTER_DEFAULT_MODEL in result.stdout     # hexgrad/kokoro-82m
    assert OPENROUTER_DEFAULT_VOICE in result.stdout     # af_heart
    # No audio written.
    assert not out.exists() or not any(out.glob("*.mp3"))


def test_openrouter_unknown_model_checks_its_own_catalogue(tmp_path):
    """The model warning validates against the OpenRouter catalogue, not StepFun."""

    book = tmp_path / "book.md"
    book.write_text("# T\n\nText here.\n", "utf-8")
    # A StepFun model is 'unknown' when the provider is OpenRouter.
    result = runner.invoke(
        app,
        ["convert", str(book), "--dry-run", "--provider", "openrouter",
         "--model", DEFAULT_MODEL],
    )
    assert result.exit_code == 0
    assert "not a known model" in result.stdout


def test_list_models_shows_both_provider_groups():
    result = runner.invoke(app, ["list-models"])
    assert result.exit_code == 0
    assert "StepFun models" in result.stdout
    assert "OpenRouter models" in result.stdout
    for name in MODELS:
        assert name in result.stdout
    assert OPENROUTER_DEFAULT_MODEL in result.stdout     # hexgrad/kokoro-82m
    assert "step-tts-mini" not in result.stdout


def test_list_models_provider_filter_openrouter():
    result = runner.invoke(app, ["list-models", "--provider", "openrouter"])
    assert result.exit_code == 0
    assert OPENROUTER_DEFAULT_MODEL in result.stdout
    # StepFun models are filtered out.
    assert DEFAULT_MODEL not in result.stdout
    assert "StepFun models" not in result.stdout


def test_list_voices_openrouter_shows_kokoro_with_grades():
    result = runner.invoke(app, ["list-voices", "--provider", "openrouter"])
    assert result.exit_code == 0
    assert OPENROUTER_DEFAULT_VOICE in result.stdout     # af_heart
    # Quality grade hint from the catalogue description renders.
    assert "(A" in result.stdout
    # StepFun voices are filtered out.
    assert "lively-girl" not in result.stdout


def test_list_voices_default_shows_both_providers():
    result = runner.invoke(app, ["list-voices"])
    assert result.exit_code == 0
    assert "lively-girl" in result.stdout                # StepFun group
    assert OPENROUTER_DEFAULT_VOICE in result.stdout     # Kokoro group
