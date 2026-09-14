"""Base-versus-head comparison: identity, attribution, and honesty."""

from __future__ import annotations

import pytest

from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest

from .conftest import (
    LANGUAGES,
    Repo,
    python_complex,
    python_io_in_loop,
)


def compare(repo: Repo, revspec: str | None = "HEAD", **kwargs):
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    return service.compare(DeltaRequest(str(repo.path), revspec, **kwargs))


def kinds(delta) -> dict[str, int]:
    out: dict[str, int] = {}
    for finding in delta.findings:
        out[finding.change_kind] = out.get(finding.change_kind, 0) + 1
    return out


# -- the core claim ---------------------------------------------------------


def test_a_new_complex_function_is_introduced(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("stable", 1)})
    repo.commit("add", {"app.py": python_complex("stable", 1) + python_complex("tangle", 14)})

    delta = compare(repo)

    assert delta.status == "available"
    assert delta.introduced_total >= 1
    introduced = [f for f in delta.findings if f.change_kind == "introduced"]
    assert any(f.symbol == "tangle" for f in introduced)
    assert all(f.attribution_basis == "added_lines" for f in introduced)
    assert all(f.attribution_confidence == "high" for f in introduced)


def test_pure_line_movement_introduces_nothing(make_repo):
    """The whole point: moving a finding must not create one."""
    repo = make_repo()
    tangled = python_complex("tangle", 14)
    repo.commit("seed", {"app.py": tangled})
    repo.commit("shift", {"app.py": "# a new header comment\n" * 30 + tangled})

    delta = compare(repo)

    assert delta.introduced_total == 0
    assert delta.worsened_total == 0
    assert delta.unchanged_total >= 1
    assert delta.is_clean


def test_a_function_growing_more_complex_is_worsened_not_introduced(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 6)})
    repo.commit("grow", {"app.py": python_complex("tangle", 26)})

    delta = compare(repo)

    assert delta.introduced_total == 0
    worsened = [f for f in delta.findings if f.change_kind == "worsened"]
    assert worsened, kinds(delta)
    assert worsened[0].symbol == "tangle"
    assert worsened[0].severity_before is not None
    assert worsened[0].severity_before != worsened[0].severity


def test_removing_the_problem_resolves_it_and_surfaces_nothing(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 14)})
    repo.commit("simplify", {"app.py": python_complex("tangle", 1)})

    delta = compare(repo)

    assert delta.findings == []
    assert delta.resolved_total >= 1
    assert delta.is_clean


def test_a_rename_carries_the_finding_across(make_repo):
    """A moved file is the same file; its findings are not new."""
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 14)})
    repo.move("app.py", "pkg/app.py")
    repo.commit("move")

    delta = compare(repo)

    assert delta.introduced_total == 0
    assert delta.resolved_total == 0
    assert delta.unchanged_total >= 1


def test_deleting_a_file_surfaces_nothing_and_is_recorded_as_skipped(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 14), "keep.py": "x = 1\n"})
    repo.remove("app.py")
    repo.commit("delete")

    delta = compare(repo)

    assert delta.introduced_total == 0
    assert delta.skipped.get("app.py") == "deleted"


# -- multiplicity -----------------------------------------------------------


def test_a_second_hit_of_the_same_marker_is_one_introduced_finding(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("one", 14)})
    repo.commit("add", {"app.py": python_complex("one", 14) + python_complex("two", 14)})

    delta = compare(repo)

    introduced = [f for f in delta.findings if f.change_kind == "introduced"]
    assert [f.symbol for f in introduced].count("two") == 1
    assert not any(f.symbol == "one" for f in introduced)


def test_replacing_one_marker_hit_with_another_is_not_a_regression(make_repo):
    """Same symbol, same marker, rewritten body: one finding, not two."""
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 14)})
    repo.commit("rewrite", {"app.py": python_complex("tangle", 15)})

    delta = compare(repo)

    assert delta.introduced_total == 0
    assert delta.resolved_total == 0


# -- attribution ------------------------------------------------------------


def test_an_untouched_finding_in_an_edited_file_is_not_charged_to_added_lines(make_repo):
    """Editing elsewhere in a file must not claim its pre-existing problems."""
    repo = make_repo()
    original = python_complex("tangle", 14)
    repo.commit("seed", {"app.py": original})
    repo.commit("append", {"app.py": original + "\n\ndef helper():\n    return 1\n"})

    delta = compare(repo)

    assert not any(f.symbol == "tangle" and f.change_kind == "introduced" for f in delta.findings)


def test_a_new_file_is_attributed_as_a_new_file(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("add", {"new.py": python_complex("tangle", 14)})

    delta = compare(repo)

    introduced = [f for f in delta.findings if f.change_kind == "introduced"]
    assert introduced
    assert all(f.attribution_basis == "new_file" for f in introduced)


def test_every_surfaced_finding_names_a_basis_and_confidence(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("add", {"app.py": python_complex("tangle", 20)})

    delta = compare(repo)

    for finding in delta.findings:
        assert finding.attribution_basis
        assert finding.attribution_confidence in {"high", "medium", "low"}
        assert finding.attribution_detail


# -- cross-language ---------------------------------------------------------


@pytest.mark.parametrize("language", sorted(LANGUAGES))
def test_the_same_change_shape_works_in_every_language(make_repo, language):
    """No language-specific code in the comparison or the response."""
    filename, build = LANGUAGES[language]
    repo = make_repo()
    repo.commit("seed", {filename: build("stable", 1)})
    repo.commit("add", {filename: build("stable", 1) + "\n" + build("tangle", 16)})

    delta = compare(repo)

    assert delta.status == "available"
    assert delta.scope.analyzed == 1
    assert any(f.path == filename for f in delta.findings), (
        f"{language}: {[(f.path, f.biomarker_type) for f in delta.findings]}"
    )


# -- dimensions -------------------------------------------------------------


def test_a_performance_finding_is_ranked_without_defect_impact(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_io_in_loop(in_loop=False)})
    repo.commit("loop", {"app.py": python_io_in_loop(in_loop=True)})

    delta = compare(repo)
    perf = [f for f in delta.findings if f.dimension == "performance"]

    assert perf, [(f.dimension, f.biomarker_type) for f in delta.findings]
    assert perf[0].health_impact == 0.0
    assert perf[0].opportunity_id
    assert perf[0].opportunity_rank is not None


def test_findings_are_ordered_by_severity_not_by_dimension(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("add", {"app.py": python_complex("tangle", 26)})

    delta = compare(repo)

    severities = [f.severity for f in delta.findings]
    ranks = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    assert severities == sorted(severities, key=lambda s: -ranks[s])


# -- honesty ----------------------------------------------------------------


def test_an_unsupported_file_is_skipped_and_never_looks_clean(make_repo):
    repo = make_repo()
    repo.commit("seed", {"README.md": "# hi\n"})
    repo.commit("edit", {"README.md": "# hi there\n"})

    delta = compare(repo)

    assert delta.findings == []
    assert delta.status == "unavailable"
    assert delta.skipped == {"README.md": "not_health_analyzable"}
    assert not delta.is_clean


def test_a_partial_run_is_reported_as_partial(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit(
        "mixed",
        {"app.py": python_complex("tangle", 16), "notes.md": "text\n"},
    )

    delta = compare(repo)

    assert delta.status == "partial"
    assert delta.skipped
    assert not delta.is_clean


def test_a_generated_file_is_excluded_by_reason(make_repo):
    repo = make_repo()
    repo.commit("seed", {"gen.py": "# GENERATED CODE\nx = 1\n"})
    repo.commit("edit", {"gen.py": "# GENERATED CODE\n" + python_complex("tangle", 16)})

    delta = compare(repo)

    assert delta.skipped.get("gen.py") == "generated"
    assert delta.findings == []
    # Nothing was compared, so this is not a partial pass and not a clean one.
    assert delta.status == "unavailable"
    assert not delta.is_clean


def test_a_bad_revspec_is_an_explicit_unsupported_range(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})

    delta = compare(repo, "no-such-ref")

    assert delta.status == "unsupported_range"
    assert delta.findings == []
    assert not delta.is_clean


def test_clean_only_claims_the_analyzed_scope(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("edit", {"app.py": "x = 2\n"})

    delta = compare(repo)

    assert delta.is_clean
    assert "analyzed scope" in delta.explanation


# -- revspec shapes ---------------------------------------------------------


def test_working_tree_changes_are_compared_against_head(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.write("app.py", python_complex("tangle", 16))

    delta = compare(repo, None)

    assert delta.head is not None and delta.head.kind == "working_tree"
    assert delta.introduced_total >= 1


def test_two_dot_and_three_dot_ranges_resolve(make_repo):
    repo = make_repo()
    base = repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("add", {"app.py": python_complex("tangle", 16)})

    two_dot = compare(repo, f"{base}..HEAD")
    three_dot = compare(repo, f"{base}...HEAD")

    assert two_dot.introduced_total == three_dot.introduced_total >= 1
    assert two_dot.base is not None and two_dot.base.sha == base


def test_a_historical_commit_is_compared_against_its_own_parent(make_repo):
    """Not against HEAD, and not against whatever the index last saw."""
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    target = repo.commit("add", {"app.py": python_complex("tangle", 16)})
    repo.commit("later", {"other.py": "y = 2\n"})

    delta = compare(repo, target)

    assert delta.head is not None and delta.head.sha == target
    assert delta.introduced_total >= 1
    assert all(f.path == "app.py" for f in delta.findings)


def test_a_root_commit_compares_against_the_empty_tree(make_repo):
    repo = make_repo()
    root = repo.commit("seed", {"app.py": python_complex("tangle", 16)})

    delta = compare(repo, root)

    assert delta.introduced_total >= 1
    assert all(f.attribution_basis == "new_file" for f in delta.findings)


# -- filters and identity ---------------------------------------------------


def test_exclusions_and_extensions_narrow_the_compared_scope(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit(
        "add",
        {"app.py": python_complex("a", 16), "vendor/lib.py": python_complex("b", 16)},
    )

    excluded = compare(repo, "HEAD", exclude_patterns=("vendor/",))

    assert all(not f.path.startswith("vendor/") for f in excluded.findings)
    assert excluded.scope.changed == 1


def test_change_finding_ids_are_deterministic_across_runs(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("add", {"app.py": python_complex("tangle", 16)})

    first = compare(repo)
    second = ChangeHealthDeltaService(repo_path=str(repo.path)).compare(
        DeltaRequest(str(repo.path), "HEAD")
    )

    assert [f.change_finding_id for f in first.findings] == [
        f.change_finding_id for f in second.findings
    ]
    assert all(f.change_finding_id.startswith("chf_") for f in first.findings)


def test_the_analyzer_fingerprint_is_reported_on_both_sides(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("add", {"app.py": python_complex("tangle", 16)})

    delta = compare(repo)

    assert delta.fingerprint is not None
    assert delta.fingerprint.analyzer_version > 0
    assert delta.fingerprint.performance_model_version > 0
    assert delta.comparison_basis == "both_sides_analyzed"
