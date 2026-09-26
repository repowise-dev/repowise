"""Unit tests for ``unbounded_read_reduced_in_memory``.

Positive: same-function supabase chain + setdefault, SQLAlchemy
``scalars().all()`` + seen-set, and the one-hop same-file-helper case (the
motivating shape: a query in one function reduced by its helper). Negative:
bounded reads, 1:1 projection, query-in-a-loop, unconsumed result, and a
helper defined in another file.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import FileComplexity
from repowise.core.analysis.health.perf.unbounded_reduction import collect_unbounded_reductions

_KIND = "unbounded_read_reduced_in_memory"


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


@dataclass
class _FileInfo:
    path: str
    abs_path: str
    language: str


@dataclass
class _ParsedFile:
    file_info: _FileInfo


def _hits(tmp_path: Path, name: str, src: str) -> list:
    _require_python()
    p = tmp_path / name
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    pf = _ParsedFile(_FileInfo(path=name, abs_path=str(p), language="python"))
    fcx = FileComplexity(functions=[], classes=[])
    collect_unbounded_reductions([(pf, fcx)])
    return [h for h in fcx.perf_hits if h.kind == _KIND]


# -- positive ----------------------------------------------------------------


def test_same_function_supabase_setdefault(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "a.py",
        """
        def get_repos(supabase, repo_ids):
            builds = (
                supabase.table("builds")
                .select("*")
                .in_("repo_id", repo_ids)
                .eq("status", "ready")
                .order("completed_at", desc=True)
                .execute()
            )
            latest_by_repo = {}
            for build in builds.data:
                rid = build["repo_id"]
                latest_by_repo.setdefault(rid, build)
            return latest_by_repo
        """,
    )
    assert len(hits) == 1
    assert hits[0].function == "get_repos"
    assert hits[0].path == ()


def test_sqlalchemy_scalars_all_seen_set(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "b.py",
        """
        def latest_per_repo(session):
            rows = session.execute(select(Build)).scalars().all()
            seen = set()
            out = []
            for row in rows:
                rid = row.repo_id
                if rid in seen:
                    continue
                seen.add(rid)
                out.append(row)
            return out
        """,
    )
    assert len(hits) == 1
    assert hits[0].function == "latest_per_repo"


def test_one_hop_same_file_helper(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "repos.py",
        """
        def list_latest_builds(supabase, repo_ids):
            builds = (
                supabase.table("builds")
                .select("*")
                .in_("repo_id", repo_ids)
                .eq("status", "ready")
                .execute()
            )
            return _latest_per_branch(builds.data)


        def _latest_per_branch(builds):
            latest_by_repo = {}
            for build in builds:
                rid = build["repo_id"]
                latest_by_repo.setdefault(rid, build)
            return latest_by_repo
        """,
    )
    assert len(hits) == 1
    assert hits[0].function == "list_latest_builds"
    assert hits[0].path == ("_latest_per_branch",)


# -- negative ------------------------------------------------------------


def test_limit_bounds_the_read(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "c.py",
        """
        def f(supabase):
            builds = supabase.table("builds").select("*").eq("status", "ready").limit(50).execute()
            out = {}
            for row in builds.data:
                out.setdefault(row["id"], row)
            return out
        """,
    )
    assert hits == []


def test_bare_one_to_one_projection_does_not_fire(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "d.py",
        """
        def f(supabase):
            builds = supabase.table("builds").select("*").execute()
            out = {}
            for row in builds.data:
                out[row.id] = row
            return out
        """,
    )
    assert hits == []


def test_query_inside_a_loop_does_not_fire(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "e.py",
        """
        def f(supabase, ids):
            out = {}
            for rid in ids:
                builds = supabase.table("builds").select("*").eq("repo_id", rid).execute()
                out[rid] = builds.data
            return out
        """,
    )
    assert hits == []


def test_result_only_returned_does_not_fire(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "f.py",
        """
        def f(supabase):
            builds = supabase.table("builds").select("*").execute()
            return builds.data
        """,
    )
    assert hits == []


def test_different_file_helper_does_not_fire(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "g.py",
        """
        from other_module import process_builds

        def f(supabase):
            builds = supabase.table("builds").select("*").execute()
            return process_builds(builds.data)
        """,
    )
    assert hits == []
