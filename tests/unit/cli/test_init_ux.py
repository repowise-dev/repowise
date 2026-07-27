"""First-run `repowise init` screen behaviour.

Covers the two pieces of the UX pass that are wiring rather than copy: the
stage-header signal the pipeline emits for phases 1 and 2, and the keyless
detection that decides whether picking a provider asks for an API key.
"""

from __future__ import annotations

from io import StringIO
from typing import Any

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


def test_the_keyless_providers_get_setup_help_instead_of_a_key_prompt() -> None:
    """Ollama used to be registered like a hosted provider, so selecting it
    asked for an API key it does not have and bounced forever on Enter."""
    assert set(provider_selection._LOCAL_PROVIDER_SETUP) == {"codex_cli", "opencode", "ollama"}
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
