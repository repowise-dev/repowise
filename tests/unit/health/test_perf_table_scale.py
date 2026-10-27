"""Whether the tables a loop reads grow with the repository (``perf.table_scale``).

Models are declared in one file, the loop is walked in another, and the
post-walk pass judges the loop with the repo's whole schema, as on an index.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.perf.actionability import expected_reason
from repowise.core.analysis.health.perf.orm_models import build_model_index
from repowise.core.analysis.health.perf.table_scale import (
    settle_small_table_loops,
    table_scales,
)

_CODE_SCHEMA = """
from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    local_path: Mapped[str] = mapped_column(String)
    head_commit: Mapped[str] = mapped_column(String)

class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    repository_id: Mapped[str] = mapped_column(String, ForeignKey("repositories.id"))

class GraphNode(Base):
    __tablename__ = "graph_nodes"
    node_id: Mapped[str] = mapped_column(String)
    file_path: Mapped[str] = mapped_column(String)

class GitCommit(Base):
    __tablename__ = "git_commits"
    sha = Column(String)

class Page(Base):
    __tablename__ = "wiki_pages"
    target_path: Mapped[str] = mapped_column(String)

class PageVersion(Base):
    __tablename__ = "wiki_page_versions"
    page_id: Mapped[str] = mapped_column(String, ForeignKey("wiki_pages.id"))
"""

# One key family only (a workflow ``node_id``): the column rule does not apply.
_APP_SCHEMA = """
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[str] = mapped_column(String, primary_key=True)

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)

class NodeRun(Base):
    __tablename__ = "node_runs"
    node_id: Mapped[str] = mapped_column(String)
"""

_LOOP = """
from sqlalchemy import select
async def check(session):
    result = await session.execute(select(MODEL))
    rows = result.scalars().all()
    for row in rows:
        await session.execute(select(Page).where(Page.repository_id == row.id))
"""


@dataclass
class _FileInfo:
    path: str
    abs_path: str
    language: str


@dataclass
class _ParsedFile:
    file_info: _FileInfo


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


def _index(tmp_path: Path, schema: str):
    path = tmp_path / "models.py"
    path.write_text(textwrap.dedent(schema), encoding="utf-8")
    pf = _ParsedFile(_FileInfo(path="models.py", abs_path=str(path), language="python"))
    return build_model_index([pf], lambda p: Path(p).read_bytes())


def _settled_loop(tmp_path: Path, schema: str, src: str):
    _require_python()
    fcx = walk_file("loop.py", "python", textwrap.dedent(src).encode())
    pf = _ParsedFile(_FileInfo(path="loop.py", abs_path="loop.py", language="python"))
    settle_small_table_loops([(pf, fcx)], _index(tmp_path, schema))
    return next(h for h in fcx.perf_hits if h.kind == "io_in_loop").loop


def test_tables_with_a_code_artefact_key_or_a_parent_that_has_one_grow(tmp_path: Path) -> None:
    _require_python()
    scales = table_scales(_index(tmp_path, _CODE_SCHEMA))
    for name in ("GraphNode", "graph_nodes", "GitCommit", "Page", "PageVersion"):
        assert scales.classify(name) == "repo_scale", name
    for name in ("Repository", "repositories", "Decision"):
        assert scales.classify(name) == "small", name


def test_a_schema_with_one_key_family_falls_back_to_registry_names(tmp_path: Path) -> None:
    _require_python()
    scales = table_scales(_index(tmp_path, _APP_SCHEMA))
    assert scales.classify("Order") is None
    assert scales.classify("NodeRun") is None
    assert scales.classify("Repository") == "registry"


_UNSURE_TABLES = """
class Upload(Base):
    __tablename__ = "uploads"
    path: Mapped[str] = mapped_column(String)

class Bare(Base):
    __tablename__ = "bare"

class Child(Base):
    __tablename__ = "children"
    other_id: Mapped[str] = mapped_column(String, ForeignKey("elsewhere.id"))

class Grandchild(Base):
    __tablename__ = "grandchildren"
    child_id: Mapped[str] = mapped_column(String, ForeignKey("children.id"))
"""


def test_a_table_whose_size_cannot_be_told_is_never_small(tmp_path: Path) -> None:
    # A key under another spelling, no column read, or a parent the repo does not declare.
    _require_python()
    scales = table_scales(_index(tmp_path, _CODE_SCHEMA + _UNSURE_TABLES))
    for name in ("Upload", "Bare", "Child", "Grandchild"):
        assert scales.classify(name) is None, name


def test_a_typescript_loop_is_judged_by_registry_names_alone(tmp_path: Path) -> None:
    _require_python()
    src = """
    async function f(prisma) {
      const rows = await prisma.repository.findMany();
      for (const r of rows) { await prisma.page.findMany({ where: { repoId: r.id } }); }
    }
    """
    fcx = walk_file("loop.ts", "typescript", textwrap.dedent(src).encode())
    pf = _ParsedFile(_FileInfo(path="loop.ts", abs_path="loop.ts", language="typescript"))
    settle_small_table_loops([(pf, fcx)], _index(tmp_path, _CODE_SCHEMA))
    facts = next(h for h in fcx.perf_hits if h.kind == "io_in_loop").loop
    assert facts.magnitude == "unknown"
    assert "loop_small_tables" not in facts.as_details()


def test_a_comma_join_in_raw_sql_names_no_tables_one_by_one(tmp_path: Path) -> None:
    src = """
    from sqlalchemy import text
    async def check(session):
        result = await session.execute(text("SELECT r.id FROM repositories r, graph_nodes n"))
        rows = result.all()
        for row in rows:
            await session.execute(select(Page).where(Page.repository_id == row.id))
    """
    assert _settled_loop(tmp_path, _CODE_SCHEMA, src).magnitude == "grows_with_data"


def test_a_filter_on_a_growing_model_keeps_the_loop_growing(tmp_path: Path) -> None:
    src = _LOOP.replace("select(MODEL)", "select(Repository).where(Repository.name == GraphNode.repo_name)")
    assert _settled_loop(tmp_path, _CODE_SCHEMA, src).magnitude == "grows_with_data"


def test_a_loop_over_a_small_table_is_bounded_and_names_it(tmp_path: Path) -> None:
    facts = _settled_loop(tmp_path, _CODE_SCHEMA, _LOOP.replace("MODEL", "Repository"))
    assert facts.magnitude == "bounded"
    assert facts.as_details()["loop_small_tables"] == ["Repository"]


def test_a_loop_over_a_table_that_grows_with_the_repo_still_grows(tmp_path: Path) -> None:
    facts = _settled_loop(tmp_path, _CODE_SCHEMA, _LOOP.replace("MODEL", "PageVersion"))
    assert facts.magnitude == "grows_with_data"
    assert "loop_small_tables" not in facts.as_details()


def test_a_join_to_a_growing_table_keeps_the_loop_growing(tmp_path: Path) -> None:
    src = _LOOP.replace("select(MODEL)", "select(Repository).join(GraphNode)")
    assert _settled_loop(tmp_path, _CODE_SCHEMA, src).magnitude == "grows_with_data"


def test_outside_a_code_schema_only_a_registry_name_speaks_and_it_proves_no_size(
    tmp_path: Path,
) -> None:
    assert (
        _settled_loop(tmp_path, _APP_SCHEMA, _LOOP.replace("MODEL", "Order")).magnitude
        == "grows_with_data"
    )
    registry = _settled_loop(tmp_path, _APP_SCHEMA, _LOOP.replace("MODEL", "Repository"))
    assert registry.magnitude == "unknown"
    assert "loop_small_tables" not in registry.as_details()


def test_an_outer_loop_that_grows_by_itself_keeps_the_hit_growing(tmp_path: Path) -> None:
    src = """
    import os
    from sqlalchemy import select
    async def check(session, root):
        for path in os.walk(root):
            result = await session.execute(select(Repository))
            rows = result.scalars().all()
            for row in rows:
                await session.execute(select(Page).where(Page.repository_id == row.id))
    """
    assert _settled_loop(tmp_path, _CODE_SCHEMA, src).magnitude != "bounded"


def test_a_registry_named_loop_over_a_read_that_names_no_table_is_unmeasured() -> None:
    _require_python()
    src = """
    from sqlalchemy import select
    async def check(session, stmt):
        result = await session.execute(stmt)
        repos = result.scalars().all()
        for repo in repos:
            await session.execute(select(Page).where(Page.repository_id == repo.id))
    """
    fcx = walk_file("loop.py", "python", textwrap.dedent(src).encode())
    facts = next(h for h in fcx.perf_hits if h.kind == "io_in_loop").loop
    assert facts.iterates == "config_table"
    assert facts.magnitude == "unknown"


def test_column_only_model_files_do_not_widen_the_lazy_load_index(tmp_path: Path) -> None:
    _require_python()
    index = _index(tmp_path, _APP_SCHEMA.replace("ForeignKey, ", ""))
    assert index.models == frozenset()
    assert "Order" in index.columns


@dataclass
class _Member:
    details: dict
    file_path: str = "app/store.py"
    function_name: str = "check"


def test_a_bounded_group_over_small_tables_says_so() -> None:
    small = _Member({"loop_magnitude": "bounded", "loop_small_tables": ["Repository"]})
    plain = _Member({"loop_magnitude": "bounded"})
    assert expected_reason([small, plain], "bounded") == "small_table"
    assert expected_reason([plain], "bounded") == "bounded_loop"
