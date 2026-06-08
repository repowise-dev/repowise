"""Surface taxonomy helpers."""

from __future__ import annotations

import pytest

from repowise.core.distill.surfaces import is_mcp, surface_of


@pytest.mark.parametrize(
    "source, mcp",
    [
        ("mcp:get_risk", True),
        ("mcp:get_context", True),
        ("cli", False),
        ("hook-bash", False),
        ("hook-powershell", False),
        ("hook-codex", False),
        ("", False),
    ],
)
def test_is_mcp(source: str, mcp: bool) -> None:
    assert is_mcp(source) is mcp


def test_surface_of() -> None:
    assert surface_of("mcp:get_dead_code") == "mcp"
    assert surface_of("cli") == "distill"
    assert surface_of("hook-bash") == "distill"
