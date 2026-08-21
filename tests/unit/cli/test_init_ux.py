"""First-run `repowise init` screen behaviour.

Covers the two pieces of the UX pass that are wiring rather than copy: the
stage-header signal the pipeline emits for phases 1 and 2, and the keyless
detection that decides whether picking a provider asks for an API key.
"""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from rich.console import Console
from rich.progress import Progress, TextColumn

from repowise.cli.ui import provider_selection
from repowise.cli.ui.progress import RichProgressCallback
from repowise.core.pipeline.progress import STAGE_ANALYSIS, STAGE_INGESTION, emit_stage


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=100), buf


# --- stage headers ---------------------------------------------------------


def test_stage_renders_the_same_numbered_rule_as_the_later_phases() -> None:
    """Phases 1 and 2 used to be plain green lines while 3 and 4 got rules,
    so the first separator a first-time user saw read "Phase 3 of 4"."""
    console, buf = _console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        RichProgressCallback(bar, console, total_phases=4).on_stage(STAGE_INGESTION)

    out = buf.getvalue()
    assert "Phase 1 of 4" in out
    assert "Ingestion" in out
    assert "Walking the tree" in out


def test_analysis_is_phase_two() -> None:
    console, buf = _console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        RichProgressCallback(bar, console, total_phases=4).on_stage(STAGE_ANALYSIS)

    assert "Phase 2 of 4" in buf.getvalue()


def test_a_callback_without_a_phase_count_draws_no_header() -> None:
    """The workspace flow reuses this callback per repo and prints its own
    per-repo header; a "Phase 1 of 4" rule a dozen times over would be wrong."""
    console, buf = _console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        RichProgressCallback(bar, console).on_stage(STAGE_INGESTION)

    assert "Phase" not in buf.getvalue()


def test_unknown_stage_is_ignored() -> None:
    console, buf = _console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        RichProgressCallback(bar, console, total_phases=4).on_stage("not_a_stage")

    assert "Phase" not in buf.getvalue()


def test_emit_stage_no_ops_for_a_callback_that_does_not_render_headers() -> None:
    """Headless callbacks (Modal, the server job executor) never grew the
    method; the pipeline must not require it of them."""

    class Headless:
        def on_message(self, level: str, text: str) -> None:  # pragma: no cover - unused
            raise AssertionError("on_stage must not fall through to on_message")

    emit_stage(Headless(), STAGE_INGESTION)
    emit_stage(None, STAGE_INGESTION)


def test_emit_stage_reaches_through_a_wrapping_callback() -> None:
    """The real init path wraps the Rich callback in PhaseTimingRecorder and
    HookProgressCallback, both of which forward by ``__getattr__``."""
    from repowise.core.pipeline import PhaseTimingRecorder
    from repowise.core.registry import HookProgressCallback

    seen: list[str] = []

    class Inner:
        def on_stage(self, stage: str) -> None:
            seen.append(stage)

    emit_stage(HookProgressCallback(PhaseTimingRecorder(Inner())), STAGE_ANALYSIS)
    assert seen == [STAGE_ANALYSIS]


def test_stage_header_keys_match_the_core_stage_constants() -> None:
    """progress.py spells the keys out rather than importing them, to keep the
    pipeline package off the CLI's import path. This is what stops that from
    becoming a silent drift."""
    from repowise.cli.ui.progress import _STAGE_HEADERS

    assert set(_STAGE_HEADERS) == {STAGE_INGESTION, STAGE_ANALYSIS}


def test_the_progress_module_does_not_drag_in_the_pipeline_package() -> None:
    """``repowise.core.pipeline.__init__`` eagerly imports the orchestrator and
    persist layer. Every CLI call site defers it into a function body; importing
    it at module scope to read two string constants put ~170ms on the front of
    every ``repowise`` invocation, including the post-commit hook."""
    import subprocess
    import sys

    probe = "import repowise.cli.ui.progress, sys; print('repowise.core.pipeline' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False", (
        "importing repowise.cli.ui.progress pulled in repowise.core.pipeline"
    )


def test_the_cli_entry_point_does_not_drag_in_the_pipeline_package() -> None:
    """The guard above covers one module; this covers the whole entry point.

    ``repowise.cli.main`` now registers commands lazily, so this no longer
    covers the command modules themselves — but the entry point still imports
    the root group, the registry and click, and a single
    ``from repowise.core.pipeline...`` on that path would be paid by every
    invocation, ``repowise --help`` and each post-commit hook run included.
    Generation is the only thing that needs the package, and it is always
    reached through a function body, so nothing here should load it.
    (Per-command import cost is guarded in ``test_lazy_commands.py``.)
    """
    import subprocess
    import sys

    probe = (
        "import repowise.cli.main, sys; "
        "print([m for m in sys.modules if m.startswith('repowise.core.pipeline')])"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", (
        f"importing repowise.cli.main pulled in the pipeline package: {out.stdout.strip()}"
    )


# --- keyless provider detection --------------------------------------------


def test_ollama_is_ready_when_the_endpoint_answers(monkeypatch: Any) -> None:
    """It takes no API key, so reachability is the only question worth asking."""
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(provider_selection, "_detect_ollama_status", lambda: True)
    monkeypatch.setattr(provider_selection, "_detect_codex_cli_status", lambda: (False, False))
    monkeypatch.setattr(provider_selection, "_detect_opencode_status", lambda: False)

    assert provider_selection._detect_provider_status()["ollama"] == "http://localhost:11434"


def test_ollama_is_not_ready_when_nothing_is_listening(monkeypatch: Any) -> None:
    monkeypatch.setattr(provider_selection, "_detect_ollama_status", lambda: False)
    monkeypatch.setattr(provider_selection, "_detect_codex_cli_status", lambda: (False, False))
    monkeypatch.setattr(provider_selection, "_detect_opencode_status", lambda: False)

    assert "ollama" not in provider_selection._detect_provider_status()


def test_ollama_base_url_honours_the_env_var(monkeypatch: Any) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:9999")
    assert provider_selection.ollama_base_url() == "http://gpu-box:9999"


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:11434", ("localhost", 11434)),
        ("http://gpu-box:9999", ("gpu-box", 9999)),
        ("http://gpu-box", ("gpu-box", 11434)),
        ("https://ollama.internal", ("ollama.internal", 443)),
        # A bare host:port is a natural thing to export; urlparse would
        # otherwise read "localhost" as the scheme.
        ("localhost:11434", ("localhost", 11434)),
    ],
)
def test_ollama_endpoint_parses_the_usable_forms(
    monkeypatch: Any, base_url: str, expected: tuple[str, int]
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", base_url)
    assert provider_selection._ollama_endpoint() == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:abc",  # ValueError from the .port property
        "http://localhost:99999",  # port out of range, also ValueError
        "unix:///var/run/ollama.sock",  # no TCP host to probe
        "http://",  # nothing left after the scheme
    ],
)
def test_an_unusable_ollama_url_is_rejected_rather_than_probing_localhost(
    monkeypatch: Any, base_url: str
) -> None:
    """``parsed.port`` raises rather than returning None, and falling back to
    localhost would report someone's typo'd remote box as ready and defer the
    failure to the first generation call."""
    monkeypatch.setenv("OLLAMA_BASE_URL", base_url)
    assert provider_selection._ollama_endpoint() is None
    # Must not raise, and must not claim the local daemon on this machine.
    assert provider_selection._detect_ollama_status() is False


def test_a_broken_provider_config_is_a_click_error_not_a_traceback(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """Everything the provider constructor reads is user input. httpx raises
    InvalidURL for a typo'd OLLAMA_BASE_URL, and that escaped every caller's
    handler and killed `repowise init` outright."""
    import click

    from repowise.cli.helpers import resolve_provider

    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:abc")

    with pytest.raises(click.ClickException) as caught:
        resolve_provider("ollama", None, tmp_path)

    assert "Could not set up the ollama provider" in str(caught.value)


def test_an_unusable_ollama_url_says_so_instead_of_blaming_the_daemon(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:abc")
    rendered = "\n".join(provider_selection._ollama_setup_lines())
    assert "not a host repowise can reach" in rendered
    assert "Nothing is listening" not in rendered


def test_the_keyless_providers_get_setup_help_instead_of_a_key_prompt() -> None:
    """Ollama used to be registered like a hosted provider, so selecting it
    asked for an API key it does not have and bounced forever on Enter."""
    assert set(provider_selection._LOCAL_PROVIDER_SETUP) == {
        "codex_cli",
        "claude_cli",
        "opencode",
        "ollama",
    }
    for name, lines in provider_selection._LOCAL_PROVIDER_SETUP.items():
        rendered = "\n".join(lines()).lower()
        assert rendered.strip(), f"{name} has no setup help"
        assert "no api key here" in rendered or "no key needed" in rendered, (
            f"{name}'s help does not say there is no key to paste"
        )


def test_selecting_an_unreachable_ollama_never_prompts_for_a_key(monkeypatch: Any) -> None:
    console, buf = _console()
    monkeypatch.setattr(provider_selection, "_detect_ollama_status", lambda: False)
    monkeypatch.setattr(provider_selection, "_detect_codex_cli_status", lambda: (False, False))
    monkeypatch.setattr(provider_selection, "_detect_opencode_status", lambda: False)

    def boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("ollama has no API key to prompt for")

    monkeypatch.setattr(provider_selection, "_prompt_api_key", boom)

    # Nothing configured on the first render; on the recursive re-render that
    # follows the ollama help, openai is configured so the run can terminate.
    renders: list[int] = []

    def detect() -> dict[str, str]:
        renders.append(1)
        return {} if len(renders) == 1 else {"openai": "OPENAI_API_KEY"}

    monkeypatch.setattr(provider_selection, "_detect_provider_status", detect)

    providers = list(provider_selection._PROVIDER_ENV)
    answers = iter([str(providers.index("ollama") + 1), str(providers.index("openai") + 1)])
    monkeypatch.setattr(provider_selection.Prompt, "ask", lambda *a, **kw: next(answers))

    chosen = provider_selection._interactive_provider_name(console, None)

    assert len(renders) == 2, "picking an unreachable ollama should re-render the table"

    out = buf.getvalue()
    assert chosen == "openai"
    assert "runs on your machine" in out
    assert "ollama serve" in out
