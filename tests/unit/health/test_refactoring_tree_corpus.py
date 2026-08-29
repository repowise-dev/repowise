"""Composition over the archetype corpus: per-language sanity, off this repo.

The golden pins the composed output for each archetype tree so a composition
change has to explain every delta. The named tests state the contract the golden
is only evidence for.
"""

from __future__ import annotations

import pytest

from .refactoring_tree_fixture import (
    MANIFEST,
    TREES_DIR,
    UNCOVERED,
    archetype_roots,
    compose_tree,
    load_golden,
    plans_for_tree,
    rewrite_requested,
    write_golden,
)

GOLDEN = "golden_opportunities.json"


def test_corpus_composition_matches_the_golden() -> None:
    payload = {name: compose_tree(root) for name, root in archetype_roots()}
    if rewrite_requested():
        write_golden(GOLDEN, payload)
        pytest.skip("golden rewritten")
    assert payload == load_golden(GOLDEN)


def test_every_manifest_archetype_has_a_tree() -> None:
    missing = [name for name in MANIFEST if not (TREES_DIR / name).is_dir()]
    assert not missing, f"manifest names archetypes with no tree: {missing}"


def test_the_uncovered_archetypes_stay_declared() -> None:
    assert UNCOVERED, "an empty gap list reads as full coverage; it is not"


# --------------------------------------------------------------------------
# per-archetype contracts
# --------------------------------------------------------------------------


def test_declarative_orm_columns_never_instruct_a_shared_helper() -> None:
    for opportunity in compose_tree(TREES_DIR / "orm"):
        kinds = [step["refactoring_type"] for step in opportunity["steps"]]
        assert "extract_helper" not in kinds, (
            "a declarative attribute is bound in the class body; no function can hold it"
        )


def test_a_reexport_barrel_never_instructs_a_shared_helper() -> None:
    for opportunity in compose_tree(TREES_DIR / "tsapp"):
        kinds = [step["refactoring_type"] for step in opportunity["steps"]]
        assert "extract_helper" not in kinds


def test_the_cross_file_clone_with_no_history_is_demoted() -> None:
    """account.ts and workspace.ts are near-duplicates with no shared commits.

    Cross-file alone is not enough: without co-change the composer must keep the
    clone as evidence rather than instruct a rewrite of both call sites.
    """
    clones = [
        row for row in plans_for_tree(TREES_DIR / "tsapp") if row["refactoring_type"] == "extract_helper"
    ]
    if not clones:
        pytest.skip("no clone pair survived the detector's gates on this build")
    assert all((row["evidence"].get("co_change_count") or 0) == 0 for row in clones)
    for opportunity in compose_tree(TREES_DIR / "tsapp"):
        assert "extract_helper" not in [step["refactoring_type"] for step in opportunity["steps"]]


def test_nothing_that_moves_a_registered_handler_is_ever_mechanical() -> None:
    """A lifted span is the one exception: it moves no symbol the router holds."""
    moved = 0
    for opportunity in compose_tree(TREES_DIR / "web"):
        for step in opportunity["steps"]:
            if step["refactoring_type"] == "extract_method":
                continue
            moved += 1
            assert step["applicability"]["classification"] == "judgment", (
                f"{step['refactoring_type']} moved a registered symbol and claimed to be safe"
            )
    assert moved == 0, "the archetype grew a symbol-moving plan; assert its reasons too"


def test_a_lifted_span_inside_a_registered_handler_is_still_mechanical() -> None:
    kinds = {
        (step["refactoring_type"], step["applicability"]["classification"])
        for opportunity in compose_tree(TREES_DIR / "web")
        for step in opportunity["steps"]
    }
    assert ("extract_method", "mechanical") in kinds


def test_repeated_handler_bodies_stay_evidence_without_co_change() -> None:
    """Three identical read handlers are real duplication with no history.

    The demotion rule is deliberately strict here: without co-change nothing
    proves the sites move together, so the clone evidences the diagnosis instead
    of instructing a rewrite of all three call sites.
    """
    trees = compose_tree(TREES_DIR / "web")
    assert any(opportunity["evidence"] for opportunity in trees)
    for opportunity in trees:
        assert "extract_helper" not in [step["refactoring_type"] for step in opportunity["steps"]]


def test_a_split_offered_on_go_is_classified_not_dropped() -> None:
    """A same-package Go split is offered, and is still a judgment call.

    Two facts refuse the promotion independently: this fixture's groups cannot
    be named, and no split can prove the absence of a file-scoped build
    constraint. See ``UNCOVERED``.
    """
    splits = [
        step
        for opportunity in compose_tree(TREES_DIR / "gopkg")
        for step in opportunity["steps"]
        if step["refactoring_type"] == "split_file"
    ]
    if not splits:
        pytest.skip("no Go grammar on this build")
    for step in splits:
        assert step["applicability"]["facts"]["shim_required"] is False
        assert step["applicability"]["classification"] == "judgment"
        assert step["applicability"]["reasons"] == ["no_named_target"]


def test_composition_is_deterministic_across_runs() -> None:
    for _, root in archetype_roots():
        assert compose_tree(root) == compose_tree(root)
