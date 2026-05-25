from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console

from repowise.cli import ui
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

    monkeypatch.setattr(ui.click, "confirm", fake_confirm)
    monkeypatch.setattr(ui.click, "prompt", fake_prompt)

    result = ui.interactive_advanced_config(_silent_console())

    assert captured["reasoning_choices"] == REASONING_MODES
    assert result["reasoning"] == "xhigh"
