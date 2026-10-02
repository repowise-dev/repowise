"""Files a component CLI installed, decided from its components.json."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_health_origin_reads_components_json(tmp_path: Path) -> None:
    _config(tmp_path / "web", _ALIASES)

    def origin(path: str) -> str:
        pf = SimpleNamespace(file_info=SimpleNamespace(path=path, is_test=False))
        return HealthAnalyzer(graph=None, repo_root=tmp_path)._origin(pf)

    assert origin("web/src/components/ui/dialog.tsx") == "generated"
    assert origin("web/src/components/Header.tsx") == "production"
