"""Workspace init keeps the repos and primary an existing config already lists.

Picking one repo on a re-run used to rewrite ``.repowise-workspace.yaml`` with
only that repo, dropping the rest without a question.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from rich.console import Console

from repowise.cli.commands.init_cmd import _workspace_repos
from repowise.cli.commands.init_cmd._workspace_config import (
    WorkspacePick,
    build_workspace_config,
    kept_primary,
)
from repowise.cli.commands.init_cmd._workspace_repos import select_workspace_repos
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.workspace_selection import interactive_repo_select
from repowise.core.workspace import WorkspaceConfig

CONFIG = ".repowise-workspace.yaml"


def _repo(root: Path, name: str) -> SimpleNamespace:
    path = root / name
    path.mkdir(exist_ok=True)
    return SimpleNamespace(path=path.resolve(), alias=name, name=name)


def _write(root: Path, aliases: list[str], default: str, **extra: object) -> None:
    repos = []
    for a in aliases:
        (root / a).mkdir(exist_ok=True)
        repos.append({"path": a, "alias": a, "indexed_at": "2026-01-01T00:00:00"})
    data = {"version": 1, "repos": repos, "default_repo": default, **extra}
    (root / CONFIG).write_text(yaml.safe_dump(data), encoding="utf-8")


def _build(root: Path, picked: list[str], primary: str, **kw: bool) -> WorkspaceConfig:
    console = Console(record=True, width=200, theme=THEME)
    repos = [_repo(root, n) for n in picked]
    pick = WorkspacePick(repos, root.resolve(), primary, repos[0], kw.get("primary_picked", False))
    cfg = build_workspace_config(pick, console=console, ask=kw.get("ask", False))
    cfg._console_text = console.export_text()  # type: ignore[attr-defined]
    return cfg


def test_fresh_workspace_lists_the_picked_repos(tmp_path: Path) -> None:
    cfg = _build(tmp_path, ["a", "b"], "b")
    assert [e.alias for e in cfg.repos] == ["a", "b"]
    assert cfg.default_repo == "b"
    assert [e.is_primary for e in cfg.repos] == [False, True]


def test_non_interactive_keeps_repos_it_did_not_pick(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b", "c"], "b")
    with patch("click.confirm", side_effect=AssertionError("must not prompt")):
        cfg = _build(tmp_path, ["a"], "a", ask=False)

    assert [e.alias for e in cfg.repos] == ["a", "b", "c"]
    # Kept repos keep their stamps; the existing primary stays.
    assert cfg.get_repo("c").indexed_at == "2026-01-01T00:00:00"
    assert cfg.default_repo == "b"
    assert "Kept 2 existing repos" in cfg._console_text
    assert "repowise workspace remove <alias>" in cfg._console_text


def test_interactive_default_keeps_and_adds(tmp_path: Path) -> None:
    _write(tmp_path, ["b", "c"], "c")
    with patch("click.confirm", return_value=False) as confirm:
        cfg = _build(tmp_path, ["a"], "a", ask=True)

    assert confirm.call_args.kwargs["default"] is False
    assert [e.alias for e in cfg.repos] == ["b", "c", "a"]
    assert cfg.default_repo == "c"


def test_interactive_replace_drops_the_rest(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b", "c"], "b")
    with patch("click.confirm", return_value=True):
        cfg = _build(tmp_path, ["a"], "a", ask=True)

    assert [e.alias for e in cfg.repos] == ["a"]
    assert cfg.default_repo == "a"


def test_no_prompt_when_nothing_would_be_dropped(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b"], "a")
    with patch("click.confirm", side_effect=AssertionError("must not prompt")):
        cfg = _build(tmp_path, ["a", "b", "c"], "a", ask=True)
    assert [e.alias for e in cfg.repos] == ["a", "b", "c"]


def test_a_primary_picked_in_the_prompt_wins(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b"], "a")
    cfg = _build(tmp_path, ["a", "b"], "b", ask=True, primary_picked=True)
    assert cfg.default_repo == "b"
    assert cfg.get_repo("b").is_primary and not cfg.get_repo("a").is_primary


def test_existing_tags_display_name_and_contracts_survive(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    data = {
        "version": 1,
        "repos": [{"path": "a", "alias": "a", "tags": ["frontend"], "display_name": "Web app"}],
        "default_repo": "a",
        "contracts": {"detect_http": False},
    }
    (tmp_path / CONFIG).write_text(yaml.safe_dump(data), encoding="utf-8")

    cfg = _build(tmp_path, ["a"], "a")
    assert cfg.get_repo("a").tags == ["frontend"]
    assert cfg.get_repo("a").display_name == "Web app"
    assert cfg.contracts.detect_http is False


def test_kept_repo_whose_alias_was_taken_is_named(tmp_path: Path) -> None:
    (tmp_path / "old").mkdir()
    data = {"version": 1, "repos": [{"path": "old", "alias": "a"}], "default_repo": "a"}
    (tmp_path / CONFIG).write_text(yaml.safe_dump(data), encoding="utf-8")

    cfg = _build(tmp_path, ["a"], "a")
    assert [(e.alias, e.path) for e in cfg.repos] == [("a", "a")]
    assert "Dropped a" in cfg._console_text


def test_picker_number_column_fits_three_digits() -> None:
    repos = [
        SimpleNamespace(name=f"r{i}", path=Path(f"/w/r{i}"), has_repowise=False, is_submodule=False)
        for i in range(226)
    ]
    console = Console(record=True, width=120, theme=THEME)
    with patch("repowise.cli.ui.workspace_selection.Prompt.ask", return_value="none"):
        interactive_repo_select(console, repos)
    text = console.export_text()
    assert "[226]" in text and "…" not in text


def test_primary_moves_when_its_alias_was_taken(tmp_path: Path) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "b").mkdir()
    data = {
        "version": 1,
        "repos": [{"path": "old", "alias": "a"}, {"path": "b", "alias": "b"}],
        "default_repo": "a",
    }
    (tmp_path / CONFIG).write_text(yaml.safe_dump(data), encoding="utf-8")

    cfg = _build(tmp_path, ["a"], "a")
    assert cfg.default_repo == "a" and cfg.get_repo("a").path == "a"


def test_repo_whose_folder_is_gone_is_dropped_and_named(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b", "c"], "b")
    (tmp_path / "c").rmdir()
    with patch("click.confirm", side_effect=AssertionError("must not prompt")):
        cfg = _build(tmp_path, ["a", "b"], "a", ask=True)

    assert [e.alias for e in cfg.repos] == ["a", "b"]
    assert "Dropped c: the folder no longer exists" in cfg._console_text


def test_existing_primary_kept_on_single_pick_via_selector(tmp_path: Path) -> None:
    scan = SimpleNamespace(root=tmp_path, repos=[_repo(tmp_path, "a"), _repo(tmp_path, "b")])
    with (
        patch.object(_workspace_repos, "interactive_repo_select", return_value=scan.repos[:1]),
        patch("sys.stdin.isatty", return_value=True),
    ):
        pick = select_workspace_repos(scan, repo_names=[], select_all=False)
        assert pick.primary_picked is False
    assert select_workspace_repos(scan, repo_names=[], select_all=True).primary_picked is False


def test_a_malformed_config_is_replaced_with_a_note(tmp_path: Path) -> None:
    (tmp_path / CONFIG).write_text("repos: [unclosed", encoding="utf-8")
    cfg = _build(tmp_path, ["a"], "a")
    assert [e.alias for e in cfg.repos] == ["a"]
    assert "Could not read the existing workspace config" in cfg._console_text


def _pick(root: Path, picked: list[str], primary: str, primary_picked: bool = False):
    repos = [_repo(root, n) for n in picked]
    first = next(r for r in repos if r.alias == primary)
    return WorkspacePick(repos, root.resolve(), primary, first, primary_picked)


def test_kept_primary_is_the_repo_provider_and_editor_setup_read(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b"], "a")
    pick = kept_primary(_pick(tmp_path, ["a", "b"], "b"), ask=False)
    assert pick.primary_alias == "a"
    assert pick.primary_repo.path == (tmp_path / "a").resolve()
    # The config build ends on the same primary.
    assert build_workspace_config(pick, console=Console(), ask=False).default_repo == "a"


def test_a_primary_picked_in_the_prompt_is_not_replaced(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b"], "a")
    pick = kept_primary(_pick(tmp_path, ["a", "b"], "b", primary_picked=True), ask=False)
    assert pick.primary_alias == "b"


def test_a_kept_primary_left_out_of_the_pick_still_leads(tmp_path: Path) -> None:
    _write(tmp_path, ["a", "b"], "a")
    pick = kept_primary(_pick(tmp_path, ["b"], "b"), ask=False)
    assert pick.primary_alias == "a"
    assert pick.primary_repo.path == (tmp_path / "a").resolve()
    assert build_workspace_config(pick, console=Console(), ask=False).default_repo == "a"
    # An interactive run may drop the left-out repo, so the pick stands.
    assert kept_primary(_pick(tmp_path, ["b"], "b"), ask=True).primary_alias == "b"


def test_no_config_leaves_the_pick_alone(tmp_path: Path) -> None:
    pick = _pick(tmp_path, ["a", "b"], "b")
    assert kept_primary(pick, ask=False) is pick
