"""CLI-level tests using Typer's CliRunner.

These cover argument validation, the catalogue commands, and the dry-run path —
none of which touch the network. The one place we would hit the API
(`convert` without --dry-run) is covered in test_pipeline.py instead.
"""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from textbook_audiobook import __version__, cli
from textbook_audiobook.cli import _format_price, _resolve_fallback, app
from textbook_audiobook.config import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    LOCAL_BASE_URL_DEFAULT,
    LOCAL_DEFAULT_MODEL,
    LOCAL_DEFAULT_VOICE,
    MODELS,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_VOICE,
    LocalConfig,
    OpenRouterConfig,
    StepFunConfig,
    VOICES,
)
from textbook_audiobook.tts import (
    LocalTTSClient,
    OpenRouterTTSClient,
    StepFunTTSClient,
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


# -- local provider ---------------------------------------------------------


def test_local_dry_run_needs_no_key(tmp_path, monkeypatch):
    """`--dry-run --provider local` works with NO API keys set at all."""

    # Prove no key is required by clearing every provider key from the env,
    # including the optional local ones.
    for var in (
        "STEPFUN_API_KEY", "STEPFUN_STEP_PLAN_API_KEY", "OPENROUTER_API_KEY",
        "LOCAL_TTS_API_KEY", "LOCAL_TTS_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    book = tmp_path / "book.txt"
    book.write_text(
        "Local Kokoro Book\n\nHello world. A short paragraph to narrate.\n",
        "utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["convert", str(book), "--dry-run", "--provider", "local",
         "--output", str(out)],
    )
    assert result.exit_code == 0
    assert "Dry run" in result.stdout
    # Reports the local provider and its default model + voice.
    assert "local" in result.stdout
    assert LOCAL_DEFAULT_MODEL in result.stdout          # kokoro
    assert LOCAL_DEFAULT_VOICE in result.stdout          # af_heart
    assert not out.exists() or not any(out.glob("*.mp3"))


def test_local_unknown_model_checks_its_own_catalogue(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# T\n\nText here.\n", "utf-8")
    # A StepFun model is 'unknown' when the provider is local.
    result = runner.invoke(
        app,
        ["convert", str(book), "--dry-run", "--provider", "local",
         "--model", DEFAULT_MODEL],
    )
    assert result.exit_code == 0
    assert "not a known model" in result.stdout


def test_list_models_shows_local_group():
    result = runner.invoke(app, ["list-models"])
    assert result.exit_code == 0
    assert "Local (Kokoro) models" in result.stdout
    assert LOCAL_DEFAULT_MODEL in result.stdout          # kokoro


def test_list_models_provider_filter_local():
    result = runner.invoke(app, ["list-models", "--provider", "local"])
    assert result.exit_code == 0
    assert "Local (Kokoro) models" in result.stdout
    assert "$0.0000" in result.stdout                    # self-hosted => free
    # Other providers' groups are filtered out.
    assert "StepFun models" not in result.stdout
    assert "OpenRouter models" not in result.stdout


def test_list_voices_provider_filter_local():
    result = runner.invoke(app, ["list-voices", "--provider", "local"])
    assert result.exit_code == 0
    assert "Local (Kokoro) voices" in result.stdout
    assert LOCAL_DEFAULT_VOICE in result.stdout          # af_heart
    # StepFun voices are filtered out.
    assert "lively-girl" not in result.stdout


# -- provider validation ----------------------------------------------------


def test_invalid_provider_rejected(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# T\n\nText.\n", "utf-8")
    result = runner.invoke(
        app, ["convert", str(book), "--dry-run", "--provider", "bogus"]
    )
    # Typer rejects an out-of-enum choice with a usage error (exit 2).
    assert result.exit_code == 2


def test_provider_flag_case_insensitive(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate.\n", "utf-8")
    result = runner.invoke(
        app, ["convert", str(book), "--dry-run", "--provider", "STEPFUN"]
    )
    assert result.exit_code == 0
    assert "stepfun" in result.stdout


# -- _resolve_fallback (pure) -----------------------------------------------


def test_resolve_fallback_defaults_to_kokoro_for_both_providers():
    assert _resolve_fallback(None, DEFAULT_MODEL) == OPENROUTER_DEFAULT_MODEL
    assert _resolve_fallback(None, "step-tts-2") == OPENROUTER_DEFAULT_MODEL


def test_resolve_fallback_none_sentinel_disables():
    assert _resolve_fallback("none", DEFAULT_MODEL) is None
    assert _resolve_fallback("NONE", DEFAULT_MODEL) is None
    assert _resolve_fallback("  none  ", DEFAULT_MODEL) is None
    assert _resolve_fallback("", DEFAULT_MODEL) is None


def test_resolve_fallback_equal_to_primary_is_noop():
    # OpenRouter primary with the default Kokoro fallback: nothing to fall to.
    assert _resolve_fallback(None, OPENROUTER_DEFAULT_MODEL) is None
    assert _resolve_fallback(OPENROUTER_DEFAULT_MODEL, OPENROUTER_DEFAULT_MODEL) is None


def test_resolve_fallback_explicit_value_passes_through():
    assert _resolve_fallback("vendor/x", DEFAULT_MODEL) == "vendor/x"
    assert _resolve_fallback("  vendor/x  ", DEFAULT_MODEL) == "vendor/x"


def test_resolve_fallback_local_default_is_none():
    # A local primary defaults the fallback to disabled (default=None): a dead
    # local server must not silently spend OpenRouter money.
    assert _resolve_fallback(None, LOCAL_DEFAULT_MODEL, default=None) is None


def test_resolve_fallback_local_explicit_reenables():
    # An explicit --fallback-model re-enables the OpenRouter fallback even when
    # the local primary's default is None.
    assert (
        _resolve_fallback(
            OPENROUTER_DEFAULT_MODEL, LOCAL_DEFAULT_MODEL, default=None
        )
        == OPENROUTER_DEFAULT_MODEL
    )


# -- _format_price precision ------------------------------------------------


def test_format_price_precision_at_boundary():
    assert _format_price(0.85) == "$0.85"
    assert _format_price(0.40) == "$0.40"
    assert _format_price(0.10) == "$0.10"      # >= 0.10 -> two decimals
    assert _format_price(0.099) == "$0.0990"   # < 0.10 -> four decimals
    assert _format_price(0.0062) == "$0.0062"  # Kokoro's sub-cent price


def test_list_models_renders_kokoro_subcent_price():
    result = runner.invoke(app, ["list-models"])
    assert result.exit_code == 0
    # A revert to .2f would collapse this to $0.01 and fail here.
    assert "$0.0062" in result.stdout


# -- credential / factory wiring (run_pipeline stubbed, no network) ---------


_FAKE_RESULT = SimpleNamespace(
    assembly=SimpleNamespace(output_files=[]), estimated_cost_usd=0.0
)


def _capture_run_pipeline(monkeypatch) -> dict:
    """Stub cli.pipeline.run_pipeline, capturing its args. Never hits network."""

    captured: dict = {}

    def fake_run_pipeline(input_file, output_dir, config, **kwargs):
        captured["input_file"] = input_file
        captured["output_dir"] = output_dir
        captured["config"] = config
        captured.update(kwargs)
        return _FAKE_RESULT

    monkeypatch.setattr(cli.pipeline, "run_pipeline", fake_run_pipeline)
    # Building any client must not construct a real OpenAI client.
    monkeypatch.setattr(StepFunTTSClient, "_build_client", lambda self: object())
    monkeypatch.setattr(OpenRouterTTSClient, "_build_client", lambda self: object())
    monkeypatch.setattr(LocalTTSClient, "_build_client", lambda self: object())
    return captured


def test_convert_stepfun_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("STEPFUN_API_KEY", "sk-step-dummy")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")  # for the fallback build
    captured = _capture_run_pipeline(monkeypatch)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")
    result = runner.invoke(app, ["convert", str(book), "-y", "--output", str(tmp_path / "o")])
    assert result.exit_code == 0

    cfg = captured["config"]
    assert isinstance(cfg, StepFunConfig)
    assert cfg.model == DEFAULT_MODEL
    assert cfg.voice == DEFAULT_VOICE
    # StepFun uses the pipeline's default client factory.
    assert captured["client_factory"] is None
    # Fallback ALWAYS routes to OpenRouter/Kokoro (voice af_heart), built lazily.
    fallback = captured["fallback_factory"]()
    assert isinstance(fallback, OpenRouterTTSClient)
    assert fallback.config.model == OPENROUTER_DEFAULT_MODEL
    assert fallback.config.voice == OPENROUTER_DEFAULT_VOICE


def test_convert_openrouter_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    captured = _capture_run_pipeline(monkeypatch)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")
    result = runner.invoke(
        app,
        ["convert", str(book), "-y", "--provider", "openrouter",
         "--voice", "bf_emma", "--output", str(tmp_path / "o")],
    )
    assert result.exit_code == 0

    cfg = captured["config"]
    assert isinstance(cfg, OpenRouterConfig)
    assert cfg.model == OPENROUTER_DEFAULT_MODEL
    assert cfg.voice == "bf_emma"
    # OpenRouter supplies a config-taking primary factory.
    primary = captured["client_factory"](cfg)
    assert isinstance(primary, OpenRouterTTSClient)
    assert primary.config.voice == "bf_emma"
    # Default fallback == primary Kokoro model -> no-op (no second client built).
    assert captured["fallback_factory"] is None


def test_convert_local_wiring_defaults(tmp_path, monkeypatch):
    # No LOCAL_TTS_* set: LocalConfig uses the placeholder key + localhost, and
    # the local primary defaults the fallback to disabled.
    for var in (
        "STEPFUN_API_KEY", "STEPFUN_STEP_PLAN_API_KEY", "OPENROUTER_API_KEY",
        "LOCAL_TTS_API_KEY", "LOCAL_TTS_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    captured = _capture_run_pipeline(monkeypatch)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")
    result = runner.invoke(
        app,
        ["convert", str(book), "-y", "--provider", "local",
         "--output", str(tmp_path / "o")],
    )
    assert result.exit_code == 0

    cfg = captured["config"]
    assert isinstance(cfg, LocalConfig)
    assert cfg.model == LOCAL_DEFAULT_MODEL
    assert cfg.voice == LOCAL_DEFAULT_VOICE
    assert cfg.api_key == "local"                        # placeholder, no key set
    assert cfg.base_url == LOCAL_BASE_URL_DEFAULT
    # Local supplies a config-taking primary factory.
    primary = captured["client_factory"](cfg)
    assert isinstance(primary, LocalTTSClient)
    # A dead local server must not silently spend OpenRouter money: no fallback.
    assert captured["fallback_factory"] is None


def test_convert_local_base_url_override_via_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_TTS_BASE_URL", raising=False)
    captured = _capture_run_pipeline(monkeypatch)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")
    result = runner.invoke(
        app,
        ["convert", str(book), "-y", "--provider", "local",
         "--base-url", "http://10.0.0.9:8880/v1", "--output", str(tmp_path / "o")],
    )
    assert result.exit_code == 0
    assert captured["config"].base_url == "http://10.0.0.9:8880/v1"


def test_convert_local_base_url_override_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_TTS_BASE_URL", "http://kokoro.lan:8880/v1")
    captured = _capture_run_pipeline(monkeypatch)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")
    result = runner.invoke(
        app,
        ["convert", str(book), "-y", "--provider", "local",
         "--output", str(tmp_path / "o")],
    )
    assert result.exit_code == 0
    assert captured["config"].base_url == "http://kokoro.lan:8880/v1"


def test_convert_local_explicit_fallback_reenables_openrouter(tmp_path, monkeypatch):
    # An explicit --fallback-model on a local primary re-enables the OpenRouter
    # fallback (built lazily; needs OPENROUTER_API_KEY at fallback time).
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    captured = _capture_run_pipeline(monkeypatch)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")
    result = runner.invoke(
        app,
        ["convert", str(book), "-y", "--provider", "local",
         "--fallback-model", OPENROUTER_DEFAULT_MODEL, "--output", str(tmp_path / "o")],
    )
    assert result.exit_code == 0
    assert isinstance(captured["config"], LocalConfig)
    fallback = captured["fallback_factory"]()
    assert isinstance(fallback, OpenRouterTTSClient)
    assert fallback.config.model == OPENROUTER_DEFAULT_MODEL
    assert fallback.config.voice == OPENROUTER_DEFAULT_VOICE


def test_convert_keep_cache_flag_wiring(tmp_path, monkeypatch):
    """--keep-cache maps to cleanup_cache=False; default maps to True."""

    monkeypatch.setenv("STEPFUN_API_KEY", "sk-step-dummy")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")  # for the fallback build
    captured = _capture_run_pipeline(monkeypatch)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")

    # Default: cleanup enabled.
    result = runner.invoke(
        app, ["convert", str(book), "-y", "--output", str(tmp_path / "o")]
    )
    assert result.exit_code == 0
    assert captured["cleanup_cache"] is True

    # --keep-cache disables cleanup.
    result = runner.invoke(
        app,
        ["convert", str(book), "-y", "--keep-cache", "--output", str(tmp_path / "o2")],
    )
    assert result.exit_code == 0
    assert captured["cleanup_cache"] is False


def test_convert_missing_stepfun_key_exits_1(tmp_path, monkeypatch):
    for var in ("STEPFUN_API_KEY", "STEPFUN_STEP_PLAN_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    book = tmp_path / "book.md"
    book.write_text("# T\n\nSome text to narrate here.\n", "utf-8")
    # -y skips the confirm prompt; credentials fail before any network call.
    result = runner.invoke(app, ["convert", str(book), "-y"])
    assert result.exit_code == 1
    assert "STEPFUN_API_KEY" in result.stdout
