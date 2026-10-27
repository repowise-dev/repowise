"""Import-cost budget for ``repowise.server.mcp_server``.

The package used to import all 17 tool modules — and through them
``core.analysis.health``, ``core.generation.onboarding``, FastMCP and
sqlalchemy — at package import. Because a leaf import initialises its parent
package first, importing any ONE tool cost ~2.5s against ~288ms for the entire
CLI. That is the cost a CLI command adapting a single MCP tool would pay before
doing any work, so the laziness is a load-bearing property, not an optimisation.

These tests pin it structurally rather than by wall clock: the thing that
regresses is an eager ``from repowise.server.mcp_server.tool_x import ...`` put
back at the top of ``__init__.py``, and a module-presence assertion catches that
exactly, on any machine, without timing flake. One loose wall-clock ratio guards
the case where the weight arrives through a dependency nobody names here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from repowise.server import mcp_server
from repowise.server.mcp_server import _TOOL_MODULES

_PKG = "repowise.server.mcp_server"

# Heavy third-party chains no consumer of a single tool should pay for at
# package-import time. sqlalchemy is the one _state used to drag in for an
# annotation alone; FastMCP arrives with _server, which only a running server
# needs.
_HEAVY_MODULES = ("sqlalchemy", "mcp.server.fastmcp", "lancedb")


def _probe(body: str) -> dict:
    """Run *body* in a clean interpreter and return the dict it prints as JSON.

    A subprocess is the only honest measurement: pytest has already imported
    most of this package by the time any test runs, so anything reading the
    in-process ``sys.modules`` would pass no matter what ``__init__`` does.
    """
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_bare_package_import_pulls_no_tool_module():
    """Importing the package registers nothing and loads no tool module."""
    seen = _probe(
        f"""
        import json, sys
        import {_PKG}
        from repowise.core.registry import mcp_tool_registry
        print(json.dumps({{
            "entries": len(mcp_tool_registry.entries()),
            "tool_modules": [m for m in sys.modules if m.startswith("{_PKG}.tool_")],
            "heavy": [m for m in {_HEAVY_MODULES!r} if m in sys.modules],
        }}))
        """
    )
    assert seen["entries"] == 0
    assert seen["tool_modules"] == []
    assert seen["heavy"] == []


# Tools with no code-level relationship to get_answer. A genuine shared helper
# may legitimately pull one tool module into another's chain (tool_answer reads
# ``_prose_dominates`` out of tool_search, for instance), so the test names the
# unrelated ones rather than demanding total isolation it should not enforce.
_UNRELATED_TO_ANSWER = (
    "tool_architecture",
    "tool_conformance",
    "tool_dead_code",
    "tool_flows",
    "tool_health",
    "tool_refactoring",
)


def test_one_tool_does_not_drag_in_the_others():
    """A single-tool consumer pays for that tool's chain, not the whole surface."""
    seen = _probe(
        f"""
        import json, sys
        from {_PKG} import get_answer
        print(json.dumps({{
            "loaded": sorted(
                m for m in sys.modules
                if m.startswith("{_PKG}.tool_") and m.count(".") == 3
            ),
        }}))
        """
    )
    loaded = set(seen["loaded"])
    assert f"{_PKG}.tool_answer" in loaded
    unrelated = {f"{_PKG}.{name}" for name in _UNRELATED_TO_ANSWER}
    assert loaded & unrelated == set()
    # The eager version loaded all 17; today's chain is 3. The ceiling is set
    # well clear of both so a new shared helper cross-import does not read as a
    # laziness regression, while anything approaching the whole surface does.
    assert len(loaded) <= 8, f"one tool import loaded {len(loaded)} tool modules: {sorted(loaded)}"


def test_tool_modules_map_covers_every_tool_module_on_disk():
    """``_TOOL_MODULES`` is hand-maintained; pin it to the package directory.

    A new ``tool_*.py`` that registers a tool but is never added to the map is
    silently never registered — ``ensure_full_surface`` iterates the map, not
    the directory, so the tool exists and no server ever advertises it. Nothing
    else catches that: ``test_tool_table_drift`` asserts table ⊆ registry, so
    omitting the tool from both the map and the CLAUDE.md table passes.

    Not a laziness test. It guards the duplicate that laziness introduced a
    reason to care about (the old eager import block had the same trap).
    """
    pkg_dir = Path(mcp_server.__file__).parent
    on_disk = {
        p.stem if p.suffix == ".py" else p.name
        for p in pkg_dir.iterdir()
        if p.name.startswith("tool_") and (p.suffix == ".py" or (p / "__init__.py").is_file())
    }
    mapped = set(_TOOL_MODULES.values())
    # Modules that hold shared helpers rather than a registered tool.
    helpers_only = {"tool_search_symbols"}
    unmapped = on_disk - mapped - helpers_only
    assert not unmapped, (
        f"tool modules missing from _TOOL_MODULES (they would never register): {sorted(unmapped)}"
    )
    assert mapped <= on_disk, f"_TOOL_MODULES names modules that do not exist: {mapped - on_disk}"


def test_ensure_full_surface_registers_everything():
    """The eager work still happens, just later — nothing was dropped.

    An equivalence guard, not a laziness guard: it passes under an eager
    ``__init__`` too. It is here because moving ``apply()`` out of the import
    path is exactly the kind of change that drops a tool silently.
    """
    seen = _probe(
        f"""
        import json
        import {_PKG} as m
        m.ensure_full_surface()
        from repowise.core.registry import mcp_tool_registry
        print(json.dumps({{"entries": sorted(e.name for e in mcp_tool_registry.entries())}}))
        """
    )
    assert set(seen["entries"]) == set(_TOOL_MODULES)


def test_create_mcp_server_advertises_the_default_surface():
    """``create_mcp_server`` forces the surface, so it still advertises tools.

    Registration moved out of the import path; this is the assertion that the
    move did not leave a server that boots with an empty tool list. Like the
    test above it is an equivalence guard rather than a laziness guard, and it
    additionally pins the advertised ORDER, which is no longer implied by the
    import order of the tool modules.
    """
    seen = _probe(
        f"""
        import asyncio, json
        import {_PKG} as m
        srv = m.create_mcp_server(repo_path=None)
        # NOT sorted here — the advertised order is part of what is asserted.
        names = [t.name for t in asyncio.run(srv.list_tools())]
        print(json.dumps({{"names": names}}))
        """
    )
    # The curated single-repo default (see the package docstring).
    assert "get_answer" in seen["names"]
    assert "search_codebase" in seen["names"]
    # Workspace-only and opt-in tools stay hidden, as before.
    assert "get_blast_radius" not in seen["names"]
    assert "get_dependency_path" not in seen["names"]
    # Deterministic order regardless of which consumer forced the surface (an
    # HTTP app has tool_risk imported by then, a stdio server has not).
    assert seen["names"] == sorted(seen["names"])


def _timed(args: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(args, capture_output=True, text=True, timeout=180, check=True)
    return time.perf_counter() - start


# Absolute, not a ratio. The package's own cost is a roughly fixed ~80ms that
# does not shrink when the interpreter starts faster, so a multiple-of-baseline
# budget is not scale-invariant: 2x on a Windows dev box (bare ~80ms) is 5x on
# Linux CI (bare ~20ms) for identical work, and the test would fail there with
# nothing wrong. The eager version cost ~2,700ms over baseline; this is ~80ms.
_IMPORT_BUDGET_S = 0.6


def test_package_import_stays_near_bare_interpreter_startup():
    """Wall-clock backstop for weight arriving through an unnamed dependency.

    The structural tests above catch an eager ``tool_*`` import by name. This
    catches the case where the cost returns through something they do not name,
    and is measured against this interpreter's own startup in the same run so a
    slow or contended machine cancels out of the subtraction.
    """
    baseline = min(_timed([sys.executable, "-c", "pass"]) for _ in range(3))
    package = min(_timed([sys.executable, "-c", f"import {_PKG}"]) for _ in range(3))
    overhead = package - baseline
    assert overhead < _IMPORT_BUDGET_S, (
        f"importing {_PKG} costs {overhead:.3f}s over a bare interpreter "
        f"(budget {_IMPORT_BUDGET_S}s) — something is being imported eagerly again"
    )
