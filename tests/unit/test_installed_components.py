"""Files a component CLI installed, decided from its components.json, and the
clones between them the health pass drops."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.health.duplication import ClonePair
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.installed_components import is_installed_component


def _config(directory: Path, aliases: dict[str, str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"$schema": "https://ui.shadcn.com/schema.json", "aliases": aliases}
    (directory / "components.json").write_text(json.dumps(payload), encoding="utf-8")


_ALIASES = {"components": "@/components", "utils": "@/lib/utils", "ui": "@/components/ui"}


@pytest.mark.parametrize(
    ("config_dir", "aliases", "path", "expected"),
    [
        # A sub-project's config with the ui alias under src/ (Roo-Code, cline).
        ("webview-ui", _ALIASES, "webview-ui/src/components/ui/dialog.tsx", True),
        ("webview-ui", _ALIASES, "webview-ui/components/ui/dialog.tsx", True),
        ("", _ALIASES, "components/ui/use-toast.ts", True),
        # Configs from before the ui alias put the kit under <components>/ui.
        ("", {"components": "@/components"}, "components/ui/button.tsx", True),
        # Must not change: the team's own files beside the kit, other folders,
        # other sub-projects, aliases that do not resolve to a path.
        ("webview-ui", _ALIASES, "webview-ui/src/components/ui/PathTooltip.tsx", False),
        ("webview-ui", _ALIASES, "webview-ui/src/components/ui/button.stories.tsx", False),
        ("webview-ui", _ALIASES, "webview-ui/src/components/ui/__tests__/button.tsx", False),
        ("webview-ui", _ALIASES, "webview-ui/src/components/chat/dialog.tsx", False),
        ("webview-ui", _ALIASES, "other/src/components/ui/dialog.tsx", False),
        ("", {"ui": "@workspace/ui/components"}, "components/ui/dialog.tsx", False),
    ],
)
def test_ui_dir_from_components_json(
    tmp_path: Path, config_dir: str, aliases: dict[str, str], path: str, expected: bool
) -> None:
    _config(tmp_path / config_dir, aliases)
    assert is_installed_component(tmp_path, path, {}) is expected


def test_no_config_means_no_claim(tmp_path: Path) -> None:
    # A components/ui folder alone is the repository's own code.
    assert is_installed_component(tmp_path, "components/ui/dialog.tsx", {}) is False


def test_malformed_config_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "components.json").write_text("{not json", encoding="utf-8")
    assert is_installed_component(tmp_path, "components/ui/dialog.tsx", {}) is False


def _pair(a: str, b: str, lines: tuple[int, int] = (1, 40)) -> ClonePair:
    start, end = lines
    return ClonePair(
        file_a=a,
        file_b=b,
        a_start_line=start,
        a_end_line=end,
        b_start_line=start,
        b_end_line=end,
        token_count=200,
    )


def test_clones_between_kit_files_are_dropped(tmp_path: Path) -> None:
    _config(tmp_path / "web", _ALIASES)
    analyzer = HealthAnalyzer(graph=None, repo_root=tmp_path)
    kit = "web/src/components/ui/dialog.tsx"
    kit_pair = _pair(kit, "web/src/components/ui/alert-dialog.tsx")
    inside = _pair(kit, kit, (60, 70))
    own_pair = _pair(kit, "web/src/components/Header.tsx", (50, 59))
    kept, pct = analyzer._without_kit_clones(kit, [kit_pair, inside, own_pair], 50.0)
    # The clone with the team's own file still counts; the share shrinks to it.
    assert kept == [own_pair]
    assert pct == 8.2  # 50% scaled by 10 of the 61 cloned lines


def test_clones_of_a_file_outside_the_kit_are_untouched(tmp_path: Path) -> None:
    _config(tmp_path / "web", _ALIASES)
    analyzer = HealthAnalyzer(graph=None, repo_root=tmp_path)
    own = "web/src/components/Header.tsx"
    pair = _pair(own, "web/src/components/ui/dialog.tsx")
    assert analyzer._without_kit_clones(own, [pair], 30.0) == ([pair], 30.0)


def test_no_components_json_keeps_every_clone(tmp_path: Path) -> None:
    analyzer = HealthAnalyzer(graph=None, repo_root=tmp_path)
    pair = _pair("components/ui/dialog.tsx", "components/ui/alert-dialog.tsx")
    assert analyzer._without_kit_clones("components/ui/dialog.tsx", [pair], 80.0) == (
        [pair],
        80.0,
    )
