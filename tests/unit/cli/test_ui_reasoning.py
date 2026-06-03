from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console

from repowise.cli import ui
from repowise.cli.ui import mode_selection, provider_selection
from repowise.core.providers.llm.base import ProviderModelOption
from repowise.core.reasoning import REASONING_MODES


def _silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False)


def test_interactive_advanced_config_uses_shared_reasoning_modes(
    monkeypatch: Any,
) -> None:
    captured: dict[str, tuple[str, ...]] = {}

    def fake_confirm(*_args: object, **_kwargs: object) -> bool:
        return False

    def fake_prompt(
        text: str,
        *,
        default: Any = None,
        type: Any = None,
        **_kwargs: object,
    ) -> Any:
        label = text.strip()
        if label == "Pattern":
            return ""
        if label == "Reasoning mode":
            captured["reasoning_choices"] = tuple(type.choices)
            return "xhigh"
        return default

    monkeypatch.setattr(mode_selection.click, "confirm", fake_confirm)
    monkeypatch.setattr(mode_selection.click, "prompt", fake_prompt)

    result = ui.interactive_advanced_config(_silent_console())

    assert captured["reasoning_choices"] == REASONING_MODES
    assert result["reasoning"] == "xhigh"


def test_interactive_advanced_config_can_skip_reasoning_prompt(
    monkeypatch: Any,
) -> None:
    def fake_confirm(*_args: object, **_kwargs: object) -> bool:
        return False

    def fake_prompt(
        text: str,
        *,
        default: Any = None,
        **_kwargs: object,
    ) -> Any:
        if text.strip() == "Reasoning mode":
            raise AssertionError("reasoning prompt should be skipped")
        if text.strip() == "Pattern":
            return ""
        return default

    monkeypatch.setattr(mode_selection.click, "confirm", fake_confirm)
    monkeypatch.setattr(mode_selection.click, "prompt", fake_prompt)

    result = ui.interactive_advanced_config(
        _silent_console(),
        prompt_reasoning=False,
    )

    assert result["reasoning"] is None


def test_interactive_provider_config_select_uses_model_reasoning_options(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(provider_selection, "_detect_provider_status", lambda: {"gemini": "GEMINI_API_KEY"})
    monkeypatch.setattr(provider_selection, "_detect_codex_cli_status", lambda: (False, False))
    monkeypatch.setattr(
        provider_selection,
        "_provider_model_options",
        lambda *_args, **_kwargs: (
            ProviderModelOption(
                model="gemini-fast",
                reasoning_modes=("auto",),
                recommended=True,
                source="api",
            ),
            ProviderModelOption(
                model="gemini-reasoner",
                reasoning_modes=("auto", "low", "high"),
                source="api",
            ),
        ),
    )

    prompt_answers = iter(["1", "2"])

    def fake_ask(*_args: object, **_kwargs: object) -> str:
        return next(prompt_answers)

    def fake_prompt(
        text: str,
        *,
        default: Any = None,
        type: Any = None,
        **_kwargs: object,
    ) -> Any:
        if text.strip() == "Reasoning":
            assert tuple(type.choices) == ("auto", "low", "high")
            return "high"
        return default

    monkeypatch.setattr(provider_selection.Prompt, "ask", fake_ask)
    monkeypatch.setattr(provider_selection.click, "prompt", fake_prompt)

    result = ui.interactive_provider_config_select(_silent_console(), None)

    assert result.provider_name == "gemini"
    assert result.model == "gemini-reasoner"
    assert result.reasoning == "high"
