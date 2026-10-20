"""Per-file before/after scores on a change, structure only."""

from __future__ import annotations

from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest
from repowise.server.mcp_server._change_health import health_delta_block

from .conftest import python_complex


def _delta(repo):
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    return service.compare(DeltaRequest(str(repo.path), "HEAD"))


def test_a_simplified_file_reports_its_score_on_both_sides(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 26), "ok.py": "x = 1\n"})
    repo.commit("simplify", {"app.py": python_complex("tangle", 1), "ok.py": "x = 2\n"})

    block = health_delta_block(_delta(repo), revspec="HEAD", full=True)

    assert block["files_total"] == 2
    assert block["score_basis"] == "structure_only"
    row = next(r for r in block["files"] if r["path"] == "app.py")
    assert row["head_score"] > row["base_score"]
    assert row["head_structure"] < row["base_structure"]
    assert row["nloc_base"] > row["nloc_head"] > 0
    # Largest move first.
    assert block["files"][0]["path"] == "app.py"
    change = block["score_change"]
    assert change["weighting"] == "base_nloc"
    assert change["head"] > change["base"]
    assert change["structure_delta"] < 0
    assert change["delta"] == round(change["head"] - change["base"], 2)


def test_by_default_every_scored_file_is_listed_without_its_deductions(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 26), "ok.py": "x = 1\n"})
    repo.commit("simplify", {"app.py": python_complex("tangle", 1), "ok.py": "x = 2\n"})

    block = health_delta_block(_delta(repo), revspec="HEAD")

    assert [r["path"] for r in block["files"]] == ["app.py", "ok.py"]
    assert not any("base_structure" in r or "head_structure" in r for r in block["files"])
    assert block["files_total"] == 2
    assert block["score_change"]["files_compared"] == 2


def test_a_new_file_has_no_base_score_and_stays_out_of_the_average(make_repo):
    repo = make_repo()
    repo.commit("seed", {"ok.py": "x = 1\n"})
    repo.commit("add", {"ok.py": "x = 1\n", "app.py": python_complex("tangle", 14)})

    block = health_delta_block(_delta(repo), revspec="HEAD")

    row = next(r for r in block["files"] if r["path"] == "app.py")
    assert row["base_score"] is None and row["head_score"] is not None
    assert "score_change" not in block or block["score_change"]["files_compared"] == 0


def test_rows_are_capped_with_the_total_kept(make_repo):
    repo = make_repo()
    seed = {f"m{i}.py": python_complex(f"f{i}", 8) for i in range(12)}
    repo.commit("seed", seed)
    repo.commit("touch", {p: python_complex(p[:-3], 2) for p in seed})

    block = health_delta_block(_delta(repo), revspec="HEAD")

    assert block["files_total"] == 12
    assert len(block["files"]) == 10


def test_a_split_source_has_a_base_only_row_naming_where_its_code_went(make_repo):
    from .test_split_source import split

    block = health_delta_block(_delta(split(make_repo)), revspec="HEAD")

    source = next(r for r in block["files"] if r["path"] == "tool.py")
    assert source["head_score"] is None and source["base_score"] is not None
    assert source["moved_to"] == ["tool/a.py", "tool/b.py", "tool/c.py"]
