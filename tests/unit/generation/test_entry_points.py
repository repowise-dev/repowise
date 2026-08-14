"""Tests for the pure entry-point ranking/candidacy rules (generation.entry_points)."""

from __future__ import annotations

from repowise.core.generation.entry_points import (
    GLUE_STEMS,
    entry_point_depth,
    entry_point_rank_key,
    is_glue_leaf,
    not_an_execution_start,
    rank_entry_points,
)

# A representative conventional-entry stem set (registry-derived in production).
_CONV = frozenset({"main", "app", "server", "cli", "run", "manage", "wsgi", "asgi"})


def test_entry_point_depth_counts_directories():
    assert entry_point_depth("main.py") == 0
    assert entry_point_depth("src/main.py") == 1
    assert entry_point_depth("a/b/c/d/main.py") == 4


def test_glue_stems_are_index_and_mod():
    assert set(GLUE_STEMS) == {"index", "mod"}


def test_is_glue_leaf_only_for_deep_generic_stems():
    # A deeply-nested resolver index.py is a dispatch leaf.
    assert is_glue_leaf("packages/core/ingestion/resolvers/dotnet/index.py")
    assert is_glue_leaf("a/b/mod.rs")
    # Shallow generic stems may still be a real package entry.
    assert not is_glue_leaf("index.ts")
    assert not is_glue_leaf("src/index.ts")
    # Non-generic stems are never glue leaves, however deep.
    assert not is_glue_leaf("a/b/c/d/main.py")


def test_not_an_execution_start_is_language_or_glue_leaf():
    # Config/data and infra languages describe or wire the system.
    assert not_an_execution_start("api/server.json", "json")
    assert not_an_execution_start("deploy/Dockerfile", "dockerfile")
    # Deep generic-glue leaves dispatch within it.
    assert not_an_execution_start("core/ingestion/resolvers/dotnet/index.py", "python")
    # A real code entry is neither, at any depth.
    assert not not_an_execution_start("src/main.py", "python")
    assert not not_an_execution_start("a/b/c/d/main.py", "python")
    # Only a *shallow* glue stem survives candidacy. A monorepo package barrel
    # is dropped here even though ``orientation_entry_points`` keeps it by
    # ranking it last — candidacy and ordering answer different questions.
    assert not not_an_execution_start("src/index.ts", "typescript")
    assert not_an_execution_start("packages/cli/src/index.ts", "typescript")


def test_the_curator_calls_the_shared_rule_not_a_copy():
    # The curator's output is unchanged *by construction* only while it holds
    # this exact object; a re-inlined copy reopens the divergence the lift
    # closed. Scope, honestly: this pins the module binding, not the two call
    # sites. A copy that left the import in place still passes here — ruff's
    # unused-import rule is what catches that half.
    from repowise.core.analysis import kg_curation

    assert kg_curation.not_an_execution_start is not_an_execution_start


def test_glue_leaf_never_outranks_a_real_entry():
    # The .NET resolver index.py is highly central (high pagerank+betweenness)
    # but must rank below a shallow, conventionally-named main.py.
    candidates = [
        ("packages/core/ingestion/resolvers/dotnet/index.py", 0.9, 0.9),
        ("packages/cli/src/main.py", 0.1, 0.0),
    ]
    ranked = rank_entry_points(candidates, _CONV)
    assert ranked[0] == "packages/cli/src/main.py"
    assert ranked[-1].endswith("dotnet/index.py")


def test_conventional_name_outranks_neutral_at_same_depth():
    candidates = [
        ("pkg/sub/helper.py", 0.9, 0.9),  # neutral name, very central
        ("pkg/sub/app.py", 0.0, 0.0),  # conventional entry name
    ]
    ranked = rank_entry_points(candidates, _CONV)
    assert ranked[0] == "pkg/sub/app.py"


def test_shallower_entry_wins_within_a_bucket():
    candidates = [
        ("a/b/c/d/main.py", 0.5, 0.5),
        ("a/main.py", 0.0, 0.0),
    ]
    ranked = rank_entry_points(candidates, _CONV)
    assert ranked[0] == "a/main.py"


def test_centrality_only_breaks_ties():
    # Same name bucket and depth: the more central file wins.
    candidates = [
        ("pkg/main.py", 0.2, 0.1),
        ("lib/main.py", 0.9, 0.5),
    ]
    ranked = rank_entry_points(candidates, _CONV)
    assert ranked[0] == "lib/main.py"


def test_rank_is_deterministic_on_full_ties():
    candidates = [
        ("z/main.py", 0.0, 0.0),
        ("a/main.py", 0.0, 0.0),
    ]
    ranked = rank_entry_points(candidates, _CONV)
    assert ranked == ["a/main.py", "z/main.py"]  # path tiebreak


def test_rank_key_orders_bucket_then_depth_then_centrality():
    conv = entry_point_rank_key("a/app.py", pagerank=0.0, conventional_stems=_CONV)
    glue = entry_point_rank_key("a/index.py", pagerank=1.0, conventional_stems=_CONV)
    assert conv < glue  # conventional name beats central glue regardless
