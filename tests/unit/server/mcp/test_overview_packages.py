"""get_overview's package-shaped blocks: key modules, entry points, layer roots, commands."""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.server.mcp_server.tool_overview.graph import _build_commands, _layer_row
from repowise.server.mcp_server.tool_overview.pages import (
    _MODULE_CAP,
    _grouped_by_package,
    _key_module_heads,
)


def _page(path: str, section: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(target_path=path, title=path, section_number=section)


def test_key_modules_are_the_topmost_pages_in_outline_order():
    pages = [
        _page("packages/web", "3"),
        _page("packages/core/src/core", "1"),
        _page("packages/core/src/core/store", "1.1"),
        _page("packages/core/alembic", "2"),
        _page("packages/web/lib", "3.1"),
        _page("scripts", "4"),
    ]
    heads = _key_module_heads(pages)
    assert [h.target_path for h in heads] == [
        "packages/core/src/core",
        "packages/core/alembic",
        "packages/web",
        "scripts",
    ]


def test_too_few_heads_open_the_widest_into_its_top_layer():
    pages = [_page("src", "1"), *(_page(f"src/m{i}", f"1.{i + 1}") for i in range(5))]
    pages.append(_page("src/m0/deep", "1.1.1"))
    heads = _key_module_heads(pages)
    assert [h.target_path for h in heads] == [f"src/m{i}" for i in range(5)]


def test_key_modules_stop_at_the_cap():
    pages = [_page(f"m{i:02d}", str(i + 1)) for i in range(20)]
    assert len(_key_module_heads(pages)) == _MODULE_CAP


def test_entry_points_keep_their_rank_within_each_package():
    roots = ["", "packages/cli", "packages/web"]
    ranked = ["packages/cli/main.py", "packages/web/app.ts", "packages/cli/hook.py", "run.py"]
    assert _grouped_by_package(ranked, roots) == [
        "packages/cli/main.py",
        "packages/cli/hook.py",
        "packages/web/app.ts",
        "run.py",
    ]


def test_a_layer_names_the_directory_holding_most_of_it():
    ids = [f"file:packages/ui/src/c{i}.tsx" for i in range(3)] + ["file:packages/web/x.ts"]
    layer = SimpleNamespace(name="UI", node_ids_json=json.dumps(ids))
    assert _layer_row(layer, ["packages/ui", "packages/web"])["root"] == "packages/ui/src"


def test_a_layer_spread_over_packages_names_no_root():
    ids = ["file:packages/ui/a.ts", "file:packages/web/b.ts"]
    layer = SimpleNamespace(name="Types", node_ids_json=json.dumps(ids))
    assert "root" not in _layer_row(layer, ["packages/ui", "packages/web"])


def test_commands_name_a_package_only_for_tasks_the_root_lacks(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "x", "lint": "y"}}))
    for name, scripts in (("same", {"test": "x"}), ("app", {"build": "b", "test": "x"})):
        (tmp_path / name).mkdir()
        (tmp_path / name / "package.json").write_text(json.dumps({"scripts": scripts}))

    commands = _build_commands(tmp_path, ["", "same", "app"])

    assert commands["."] == {"test": "npm run test", "lint": "npm run lint"}
    assert "same" not in commands
    assert commands["app"]["build"] == "npm run build"
    assert _build_commands(None, []) == {}


def test_twin_module_pages_do_not_stall_the_widening():
    pages = [_page("src", "1"), _page("src", "2"), _page("src/a", "1.1")]
    assert [h.target_path for h in _key_module_heads(pages)] == ["src/a"]


def test_a_repo_summary_is_its_first_sentence_not_its_heading():
    from repowise.server.mcp_server.tool_overview.workspace import _lead_sentence

    essay = "## Project Summary\n\nRepowise indexes code. It also answers questions.\n\n## More"
    assert _lead_sentence(essay) == "Repowise indexes code."
    assert _lead_sentence("# Only a heading") == ""
    long = _lead_sentence("word " * 100)
    assert len(long) <= 140 and long.endswith("…")


def test_a_repo_summary_skips_tables_lists_and_fences():
    from repowise.server.mcp_server.tool_overview.workspace import _lead_sentence

    essay = "## Summary\n| a | b |\n- item\n```\nRepowise indexes code. More."
    assert _lead_sentence(essay) == "Repowise indexes code."
