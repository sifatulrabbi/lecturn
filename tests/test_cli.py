"""CLI-level tests using Typer's CliRunner.

These cover argument validation, the catalogue commands, and the dry-run path —
none of which touch the network. The one place we would hit the API
(`convert` without --dry-run) is covered in test_pipeline.py instead.
"""

from __future__ import annotations

from typer.testing import CliRunner

from textbook_audiobook import __version__
from textbook_audiobook.cli import app
from textbook_audiobook.config import DEFAULT_VOICE, MODELS, VOICES

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
