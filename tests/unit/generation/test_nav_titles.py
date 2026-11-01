"""Navigation labels: one casing table, and the rule a sidebar row reads by."""

from __future__ import annotations

import pytest

from repowise.core.generation.concept_tree.grouping import ConceptGroup
from repowise.core.generation.nav_titles import nav_label, recase


@pytest.mark.parametrize(
    ("title", "label"),
    [
        ("Cli Overview", "CLI"),
        ("Mcp Server Overview", "MCP Server"),
        ("Ui Overview (packages/ui)", "UI"),
        ("Ui Health (packages/ui/src/health/performance)", "UI Health"),
        ("Application Flask", "Flask"),
        ("Service Alembic", "Alembic"),
        ("Frontend Components", "Frontend"),
        ("Repository Overview", "Repository Root"),
        ("Overview", "Overview"),
        ("Dead Code Detection", "Dead Code Detection"),
        ("Vscode Webview", "VS Code Webview"),
        ("Config Devcontainer", "Config Dev Container"),
        ("Service Worker", "Service Worker"),
        ("Application State Machine", "Application State Machine"),
    ],
)
def test_a_module_title_reads_as_a_short_cased_label(title: str, label: str) -> None:
    assert nav_label(title) == label


def test_the_overview_is_called_overview() -> None:
    assert nav_label("Repository Overview: flask", "repo_overview") == "Overview"


def test_other_outline_pages_are_only_recased() -> None:
    assert nav_label("How the cli starts", "flow_page") == "How the CLI starts"
    assert nav_label("Getting Started", "onboarding") == "Getting Started"


def test_code_and_english_words_keep_their_spelling() -> None:
    # A path, an identifier and a backticked name are code.
    assert recase("packages/ui and get_api and `cli`") == "packages/ui and get_api and `cli`"
    # An ordinary word that is also an acronym is left alone in lower case.
    assert recase("the rest of the id") == "the rest of the id"
    assert recase("Rest of the Api") == "Rest of the API"


def test_a_tiny_leaf_folds_into_the_group_holding_its_parent() -> None:
    from repowise.core.generation.concept_tree.grouping import (
        GroupingParams,
        _fold_tiny_leaves,
        _Partitioner,
    )

    part = _Partitioner(GroupingParams(min_files=2, max_files=4), {}, {})
    parent = part.make([f"lib/config/c{i}.go" for i in range(4)])
    tiny = part.make(["lib/config/services/s.go"])
    other = part.make([f"lib/other/o{i}.go" for i in range(4)])
    folded = _fold_tiny_leaves([parent, tiny, other], part)
    assert len(folded) == 2
    assert "lib/config/services/s.go" in next(g for g in folded if "lib/config/c0.go" in g.members).members


def test_nested_tiny_groups_fold_up_the_chain() -> None:
    from repowise.core.generation.concept_tree.grouping import (
        GroupingParams,
        _fold_tiny_leaves,
        _Partitioner,
    )

    part = _Partitioner(GroupingParams(min_files=2, max_files=4), {}, {})
    home = part.make([f"lib/h{i}.go" for i in range(4)])
    middle = part.make(["lib/mid/m.go"])
    inner = part.make(["lib/mid/in/i.go"])
    for order in ([home, middle, inner], [inner, middle, home]):
        folded = _fold_tiny_leaves(list(order), part)
        assert len(folded) == 1
        assert {"lib/mid/m.go", "lib/mid/in/i.go"} <= set(folded[0].members)


def test_a_tiny_group_with_no_parent_group_keeps_its_page() -> None:
    from repowise.core.generation.concept_tree.grouping import (
        GroupingParams,
        _fold_tiny_leaves,
        _Partitioner,
    )

    part = _Partitioner(GroupingParams(min_files=2, max_files=4), {}, {})
    lone = part.make(["bufferpool/pool.go"])
    other = part.make([f"hugolib/h{i}.go" for i in range(4)])
    assert len(_fold_tiny_leaves([lone, other], part)) == 2


def test_a_tiny_group_never_crosses_a_package_root() -> None:
    from repowise.core.generation.concept_tree.grouping import (
        GroupingParams,
        _fold_tiny_leaves,
        _Partitioner,
    )

    package_of = {"app/a0.py": "", "app/a1.py": "", "app/plugin/p.py": "app/plugin"}
    part = _Partitioner(GroupingParams(min_files=2, max_files=4), {}, package_of)
    groups = [part.make(["app/a0.py", "app/a1.py"]), part.make(["app/plugin/p.py"])]
    assert len(_fold_tiny_leaves(groups, part)) == 2


def test_concept_group_is_still_constructible() -> None:
    # Guards the fixture shape the tests above rely on.
    assert ConceptGroup(members=["a/b.py"], dirs=["a"], target_path="a").file_count == 1


@pytest.mark.parametrize(
    ("label", "parent", "expected"),
    [
        ("UI Zoom", "UI", "Zoom"),
        ("API Client Types", "API Client", "Types"),
        ("Repowise Pipeline", "Core", "Pipeline"),
        ("Repowise Core", "Core", "Core"),
        ("Web Source", "Web", "Web"),
        ("Source API Client", "API Client", "API Client"),
        ("App Application Web", "Web", "Web"),
        ("CI GitHub", "", "CI"),
        ("Alembic", "Core", "Alembic"),
        ("UI and Stats", "UI", "Stats"),
        ("Lib and Components", "Components", "Components"),
        ("Source", "", "Source"),
    ],
)
def test_a_label_in_place_drops_its_parent_product_and_path_words(label, parent, expected):
    from repowise.core.generation.nav_titles import contextual_label

    assert contextual_label(label, parent, "repowise") == expected


def test_outline_labels_read_each_parent_in_place_first():
    from types import SimpleNamespace

    from repowise.core.generation.nav_titles import apply_outline_labels

    rows = [
        SimpleNamespace(id="c", parent_page_id="b", nav_title="Repowise Server MCP"),
        SimpleNamespace(id="b", parent_page_id="a", nav_title="Repowise Server"),
        SimpleNamespace(id="a", parent_page_id=None, nav_title="Overview"),
        SimpleNamespace(id="f", parent_page_id="b", nav_title=None),
    ]
    apply_outline_labels(rows, "repowise")
    assert [r.nav_title for r in rows] == ["MCP", "Server", "Overview", None]
