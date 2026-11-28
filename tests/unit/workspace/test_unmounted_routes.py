"""Go routes a never-mounted copy of a register function declares are not providers.

The shape is cut from gin-vue-admin: ``InitSkillsRouter`` is declared in both
``router/system`` and ``plugin/auto/router``; only the plugin copy is called,
yet both copies' routes were stored, so a consumer linked to each.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GraphEdge, Repository
from repowise.core.workspace.extractors.base import iter_source_files
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.repo_index import IndexedSymbol, RepoIndex
from repowise.core.workspace.unmounted_routes import drop_unmounted_providers

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

REPO = "r1"
GIN = '"github.com/gin-gonic/gin"'

MAIN = f"""\
package main

import (
	{GIN}

	"example.com/app/plugin"
	"example.com/app/system"
)

func main() {{
	r := gin.Default()
	g := r.Group("/api")
	system.App.InitBaseRouter(g)
	plugin.Mount(g)
	r.Run()
}}
"""

SYSTEM_BASE = f"""\
package system

import {GIN}

type BaseRouter struct{{}}

func (b *BaseRouter) InitBaseRouter(r *gin.RouterGroup) {{
	r.GET("base", baseHandler)
}}
"""

# The copy nothing mounts; its comment and a string naming it are not callers.
SYSTEM_SKILLS = f"""\
package system

import {GIN}

type SkillsRouter struct{{}}

// InitSkillsRouter is registered by the plugin now.
func (s *SkillsRouter) InitSkillsRouter(r *gin.RouterGroup) {{
	r.GET("getTools", getTools)
	_ = "InitSkillsRouter"
}}
"""

# A test calling the copy does not mount it.
SYSTEM_SKILLS_TEST = """\
package system

import "testing"

func TestSkills(t *testing.T) {
	new(SkillsRouter).InitSkillsRouter(nil)
}
"""

PLUGIN_MOUNT = f"""\
package plugin

import (
	{GIN}

	"example.com/app/plugin/router"
)

func Mount(g *gin.RouterGroup) {{
	router.App.InitSkillsRouter(g)
}}
"""

PLUGIN_SKILLS = f"""\
package router

import {GIN}

type SkillsRouter struct{{}}

func (s *SkillsRouter) InitSkillsRouter(r *gin.RouterGroup) {{
	r.GET("getTools", getTools)
}}
"""

FILES = {
    "go.mod": "module example.com/app\n",
    "main.go": MAIN,
    "system/base.go": SYSTEM_BASE,
    "system/skills.go": SYSTEM_SKILLS,
    "system/skills_test.go": SYSTEM_SKILLS_TEST,
    "plugin/mount.go": PLUGIN_MOUNT,
    "plugin/router/skills.go": PLUGIN_SKILLS,
}

# Go import edges fan out to every file of the imported package, as ingestion stores them.
IMPORTS = [
    ("main.go", "plugin/mount.go"),
    ("main.go", "system/base.go"),
    ("main.go", "system/skills.go"),
    ("plugin/mount.go", "plugin/router/skills.go"),
]

_FUNC_RE = re.compile(r"^func\s*(?:\((?P<recv>\w+\s+\*?(?P<type>\w+))\)\s*)?(?P<name>\w+)", re.M)
_TYPE_RE = re.compile(r"^type\s+(?P<name>\w+)\s+(?P<kind>struct|interface)\b", re.M)


def _symbols(files: dict[str, str]) -> list[IndexedSymbol]:
    """The functions, methods and types of *files*, spans as the parser gives them."""
    out: list[IndexedSymbol] = []
    for path, text in files.items():
        if not path.endswith(".go"):
            continue
        lines = text.splitlines()
        for pattern, kind_of in ((_FUNC_RE, None), (_TYPE_RE, "kind")):
            for m in pattern.finditer(text):
                start = text.count("\n", 0, m.start()) + 1
                end = next(
                    (i for i in range(start, len(lines) + 1) if lines[i - 1].startswith("}")),
                    start,
                )
                if kind_of:
                    kind, local = m.group("kind"), m.group("name")
                else:
                    kind = "method" if m.group("recv") else "function"
                    local = (
                        f"{m.group('type')}::{m.group('name')}"
                        if m.group("recv")
                        else m.group("name")
                    )
                out.append(
                    IndexedSymbol(
                        symbol_id=f"{path}::{local}",
                        name=m.group("name"),
                        qualified_name=local.replace("::", "."),
                        kind=kind,
                        signature="",
                        file_path=path,
                        start_line=start,
                        end_line=end,
                        visibility="public",
                        language="go",
                    )
                )
    return out


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    async with factory() as s:
        s.add(
            Repository(
                id=REPO, name="app", local_path="/app", settings_json="{}",
                created_at=now, updated_at=now,
            )
        )  # fmt: skip
        await s.commit()
        yield s
    await engine.dispose()


async def _add_edges(session: AsyncSession, edges: list[tuple[str, str]], kind: str) -> None:
    session.add_all(
        GraphEdge(
            repository_id=REPO,
            source_node_id=src,
            target_node_id=dst,
            imported_names_json="[]",
            edge_type=kind,
        )
        for src, dst in edges
    )
    await session.commit()


async def _providers(
    tmp_path: Path, session: AsyncSession | None, files: dict[str, str] = FILES
) -> tuple[list[tuple[str, str]], int]:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    walked = list(iter_source_files(tmp_path, frozenset({".go"})))
    contracts = [c for c in HttpExtractor().extract(tmp_path, "app") if c.role == "provider"]
    index = (
        RepoIndex.from_symbols("app", _symbols(files), repo_id=REPO, session=session)
        if session is not None
        else None
    )
    kept, dropped = await drop_unmounted_providers(contracts, index, walked)
    return sorted((c.file_path, c.contract_id) for c in kept), dropped


async def test_a_copy_only_another_copys_callers_name_is_dropped(tmp_path, session) -> None:
    await _add_edges(session, IMPORTS, "imports")
    kept, dropped = await _providers(tmp_path, session)
    assert kept == [
        ("plugin/router/skills.go", "http::GET::/gettools"),
        ("system/base.go", "http::GET::/base"),
    ]
    assert dropped == 1


async def test_without_an_index_or_a_graph_every_route_stays(tmp_path, session) -> None:
    every = [
        ("plugin/router/skills.go", "http::GET::/gettools"),
        ("system/base.go", "http::GET::/base"),
        ("system/skills.go", "http::GET::/gettools"),
    ]
    assert await _providers(tmp_path, None) == (every, 0)
    # An index whose graph holds no import edge cannot place a caller.
    assert await _providers(tmp_path, session) == (every, 0)


async def test_a_call_edge_from_production_keeps_the_copy(tmp_path, session) -> None:
    await _add_edges(session, IMPORTS, "imports")
    await _add_edges(
        session, [("main.go::main", "system/skills.go::SkillsRouter::InitSkillsRouter")], "calls"
    )
    _kept, dropped = await _providers(tmp_path, session)
    assert dropped == 0


async def test_a_call_edge_from_a_test_does_not(tmp_path, session) -> None:
    await _add_edges(session, IMPORTS, "imports")
    await _add_edges(
        session,
        [("system/skills_test.go::TestSkills", "system/skills.go::SkillsRouter::InitSkillsRouter")],
        "calls",
    )
    _kept, dropped = await _providers(tmp_path, session)
    assert dropped == 1


async def test_a_name_an_interface_declares_is_kept(tmp_path, session) -> None:
    """A call through an interface reaches every implementation and names none."""
    await _add_edges(session, IMPORTS, "imports")
    registrar = (
        f"package plugin\n\nimport {GIN}\n\ntype Registrar interface {{\n"
        "\tInitSkillsRouter(r *gin.RouterGroup)\n}\n"
    )
    files = {**FILES, "plugin/registrar.go": registrar}
    symbols = _symbols(files)
    assert any(s.symbol_id == "plugin/registrar.go::Registrar" for s in symbols)
    # The interface's method, as the parser stores it.
    symbols.append(
        IndexedSymbol(
            symbol_id="plugin/registrar.go::Registrar::InitSkillsRouter",
            name="InitSkillsRouter", qualified_name="Registrar.InitSkillsRouter",
            kind="method", signature="", file_path="plugin/registrar.go",
            start_line=6, end_line=6, visibility="public", language="go",
        )
    )  # fmt: skip
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    walked = list(iter_source_files(tmp_path, frozenset({".go"})))
    contracts = [c for c in HttpExtractor().extract(tmp_path, "app") if c.role == "provider"]
    index = RepoIndex.from_symbols("app", symbols, repo_id=REPO, session=session)
    _kept, dropped = await drop_unmounted_providers(contracts, index, walked)
    assert dropped == 0


async def test_a_function_no_one_calls_by_name_is_kept(tmp_path, session) -> None:
    """Nothing in the repo names it, so a framework or another repo may call it."""
    await _add_edges(session, IMPORTS, "imports")
    files = {**FILES, "plugin/mount.go": PLUGIN_MOUNT.replace("router.App.InitSkillsRouter(g)", "")}
    _kept, dropped = await _providers(tmp_path, session, files)
    assert dropped == 0


async def test_a_caller_reaching_the_copy_keeps_it(tmp_path, session) -> None:
    """``main.go`` reaches both packages, so its call may be either copy's."""
    await _add_edges(session, IMPORTS, "imports")
    files = {**FILES, "main.go": MAIN.replace("plugin.Mount(g)", "router.App.InitSkillsRouter(g)")}
    _kept, dropped = await _providers(tmp_path, session, files)
    assert dropped == 0


async def test_a_span_the_file_has_moved_past_binds_nothing(tmp_path, session) -> None:
    """The index's span names a line the edited file no longer declares the function on."""
    await _add_edges(session, IMPORTS, "imports")
    edited = SYSTEM_SKILLS.replace("package system\n", "package system\n\n\n")
    indexed = _symbols(FILES)
    for rel, text in {**FILES, "system/skills.go": edited}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    walked = list(iter_source_files(tmp_path, frozenset({".go"})))
    contracts = [c for c in HttpExtractor().extract(tmp_path, "app") if c.role == "provider"]
    index = RepoIndex.from_symbols("app", indexed, repo_id=REPO, session=session)
    _kept, dropped = await drop_unmounted_providers(contracts, index, walked)
    assert dropped == 0


async def test_another_copy_in_test_code_does_not_make_this_one_dead(tmp_path, session) -> None:
    files = {k: v for k, v in FILES.items() if k != "plugin/router/skills.go"}
    files["plugin/router/skills_test.go"] = PLUGIN_SKILLS
    edges = [e for e in IMPORTS if e[1] != "plugin/router/skills.go"]
    await _add_edges(
        session, [*edges, ("plugin/mount.go", "plugin/router/skills_test.go")], "imports"
    )
    _kept, dropped = await _providers(tmp_path, session, files)
    assert dropped == 0


async def test_a_sibling_of_an_importer_reaches_the_package(tmp_path, session) -> None:
    """``wire.go`` imports system; ``mount.go`` beside it calls system's copy through it."""
    wire = 'package plugin\n\nimport "example.com/app/system"\n\nvar Sys = system.App\n'
    mount = PLUGIN_MOUNT.replace("router.App.InitSkillsRouter(g)", "Sys.InitSkillsRouter(g)")
    files = {**FILES, "plugin/wire.go": wire, "plugin/mount.go": mount}
    await _add_edges(
        session,
        [*IMPORTS, ("plugin/wire.go", "system/base.go"), ("plugin/wire.go", "system/skills.go")],
        "imports",
    )
    _kept, dropped = await _providers(tmp_path, session, files)
    assert dropped == 0
