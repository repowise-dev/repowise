"""Drive the interactive provider picker without a terminal."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from repowise.cli.ui import provider_selection as ui
from repowise.cli.ui.brand import THEME


def pick_default(monkeypatch: Any, **kwargs: Any) -> tuple[str, str]:
    """The provider the picker offers as its default, and what it printed."""
    seen: dict[str, str] = {}

    def ask(*_a: object, default: str, **_k: object) -> str:
        seen["default"] = default
        return default

    monkeypatch.setattr(ui.Prompt, "ask", ask)
    buf = io.StringIO()
    ui._interactive_provider_name(Console(file=buf, width=160, theme=THEME), None, **kwargs)
    return ui._PROVIDER_CHOICES[int(seen["default"]) - 1], buf.getvalue()
