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


def test_keyed_read_reduced_per_its_own_key_column(tmp_path: Path):
    # The keys bound which repos, not how many builds each has: keeping the
    # first per repo still reads every build of every repo.
    hits = _hits(
        tmp_path,
        "keyed_same.py",
        """
        from sqlalchemy import select

        async def latest(session, repo_ids):
            res = await session.execute(select(Build).where(Build.repo_id.in_(repo_ids)))
            out = {}
            for build in res.scalars().all():
                out.setdefault(build.repo_id, build)
            return out
        """,
    )
    assert len(hits) == 1


def test_keys_a_subquery_yields_do_not_bound_the_read(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "keyed_subquery.py",
        """
        from sqlalchemy import select

        async def f(session):
            live = select(Repo.id).where(Repo.active).subquery()
            res = await session.execute(select(Build).where(Build.repo_id.in_(live)))
            seen = set()
            for build in res.scalars().all():
                if build.branch in seen:
                    continue
                seen.add(build.branch)
            return seen
        """,
    )
    assert len(hits) == 1


def test_a_fixed_set_of_states_does_not_bound_the_read(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "states.py",
        """
        from sqlalchemy import select

        def f(session):
            rows = session.execute(select(Build).where(Build.status.in_(("ready", "done"))))
            out = {}
            for build in rows.scalars().all():
                out.setdefault(build.repo_id, build)
            return out
        """,
    )
    assert len(hits) == 1


def test_a_value_the_query_compares_against_does_not_bound_it(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "compared.py",
        """
        from sqlalchemy import select

        async def f(session):
            repo = (await session.execute(select(Repo).limit(1))).scalar()
            res = await session.execute(select(Build).where(Build.repo_id == repo.id))
            out = {}
            for build in res.scalars().all():
                out.setdefault(build.branch, build)
            return out
        """,
    )
    assert len(hits) == 1


# -- negative ------------------------------------------------------------


def test_keyed_read_reduced_per_another_column(tmp_path: Path):
    # Edges into a few files, kept once per importer: the rows are the keys'.
    hits = _hits(
        tmp_path,
        "keyed_other.py",
        """
        from sqlalchemy import select

        async def files_importing(session, symbols):
            by_file: dict[str, list] = {}
            for node in symbols:
                by_file.setdefault(node.file_path, []).append(node)
            res = await session.execute(
                select(GraphEdge).where(GraphEdge.target_node_id.in_(list(by_file)))
            )
            seen = set()
            out = []
            for edge in sorted(res.scalars().all(), key=lambda e: e.source_node_id):
                importer = edge.source_node_id
                if importer in seen:
                    continue
                seen.add(importer)
                out.append(importer)
            return out
        """,
    )
    assert hits == []


def test_keyed_statement_built_before_the_read(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "keyed_tuple.py",
        """
        from sqlalchemy import select

        async def attach(session, paths):
            stmt = select(Sym.symbol_id, Sym.file_path, Sym.name).where(Sym.file_path.in_(paths))
            rows = (await session.execute(stmt)).all()
            by_name = {}
            for symbol_id, file_path, name in rows:
                by_name.setdefault((file_path, name), symbol_id)
            return by_name
        """,
    )
    assert hits == []


@pytest.mark.parametrize(
    "read",
    [
        'supabase.table("builds").select("*").in_("id", ids).execute()',
        "session.query(Build).filter_by(id=build_id).all()",
        "session.execute(select(Build).limit(10)).scalars().all()",
    ],
)
def test_reads_bounded_by_primary_keys_or_a_cap(tmp_path: Path, read: str):
    hits = _hits(
        tmp_path,
        "keyed_id.py",
        f"""
        from sqlalchemy import select

        def f(session, supabase, ids, build_id):
            rows = {read}
            out = {{}}
            for row in rows:
                out.setdefault(row.id, row)
            return out
        """,
    )
    assert hits == []



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


def test_filter_makes_keep_all_append_a_reduction(tmp_path: Path):
    hits = _hits(
        tmp_path,
        "mixed.py",
        """
        def filtered(supabase):
            for build in supabase.table("builds").select("*").execute().data:
                if not build.active: continue
                by_name.setdefault(build.name, build)
                by_file.setdefault(build.path, []).append(build)
        def unfiltered(supabase):
            for build in supabase.table("builds").select("*").execute().data:
                for item in build.items: continue
                by_name.setdefault(build.name, build)
                by_file.setdefault(build.path, []).append(build)
        """,
    )
    assert {hit.function for hit in hits} == {"filtered"}


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
