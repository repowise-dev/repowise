"""Test wiring no import states: runner setup files and helpers named by path."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.ingestion.framework_edges.test_path_strings import (
    PATH_STRING_HINT,
    _add_path_string_edges,
    path_string_targets,
)
from repowise.core.ingestion.framework_edges.test_runner_setup import (
    SETUP_FILE_HINT,
    _add_setup_edges,
    is_runner_config,
    leaves_its_directory,
    resolve_setup_spec,
    setup_specs,
)
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.test_paths import is_test_related_path


def _ctx(sources: dict[str, str]) -> tuple[nx.DiGraph, dict, ResolverContext]:
    graph = nx.DiGraph()
    graph.add_nodes_from(sources)
    parsed = {
        p: SimpleNamespace(file_info=SimpleNamespace(is_test=is_test_related_path(p), abs_path=p))
        for p in sources
    }
    ctx = ResolverContext(
        path_set=set(sources),
        stem_map={},
        graph=graph,
        source_map={p: s.encode() for p, s in sources.items()},
    )
    return graph, parsed, ctx


# -- runner setup files ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "specs"),
    [
        ('export default { test: { setupFiles: ["./setup.ts"] } }', ["./setup.ts"]),
        ("setupFiles: './a.ts', include: ['src/**']", ["./a.ts"]),
        ('setupFiles: [resolveRoot("test/setup.ts")],', ["test/setup.ts"]),
        (
            'setupFiles: [\n  ...new Set([...(base.setupFiles ?? []), "test/rt.ts"].map(f)),\n],',
            ["test/rt.ts"],
        ),
        ('const setupFiles = [\n  ...(o ? [] : ["test/rt.ts"]),\n];', ["test/rt.ts"]),
        ('"setupFilesAfterEnv": ["<rootDir>/jest.setup.js"]', ["<rootDir>/jest.setup.js"]),
        ("globalSetup:\n  './global.ts',", ["./global.ts"]),
        ("setupFiles: base.setupFiles,\ninclude: ['x.ts']", []),
        ("// setupFiles: ['./old.ts']\n", []),
        ("setupFiles: [`${root}/setup.ts`]", []),
        ("coverage: { exclude: ['./setup.ts'] }", []),
    ],
)
def test_setup_specs_reads_the_strings_in_a_setup_value(text: str, specs: list[str]) -> None:
    assert setup_specs(text.encode()) == specs


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("vitest.config.ts", True),
        ("test/vitest/vitest.scoped-config.ts", True),
        ("packages/a/jest.config.cjs", True),
        ("packages/a/package.json", True),
        ("src/setup.ts", False),
        ("eslint.config.js", False),
    ],
)
def test_runner_configs_are_named_for_their_runner(path: str, expected: bool) -> None:
    assert is_runner_config(path) is expected


def test_a_setup_path_resolves_against_the_config_or_any_directory_above() -> None:
    _, _, ctx = _ctx(
        {
            "test/setup.ts": "",
            "test/vitest/vitest.unit.config.ts": "",
            "web/package.json": "{}",
            "web/jest.setup.js": "",
            "web/config/jest.config.js": "",
        }
    )
    assert resolve_setup_spec("test/setup.ts", "test/vitest/vitest.unit.config.ts", ctx) == (
        "test/setup.ts"
    )
    assert resolve_setup_spec("../setup", "test/vitest/vitest.unit.config.ts", ctx) == (
        "test/setup.ts"
    )
    assert resolve_setup_spec("<rootDir>/jest.setup.js", "web/config/jest.config.js", ctx) == (
        "web/jest.setup.js"
    )
    assert resolve_setup_spec("@testing-library/jest-dom", "web/config/jest.config.js", ctx) is None


def test_tests_a_config_runs_get_an_edge_to_its_setup_files() -> None:
    graph, parsed, ctx = _ctx(
        {
            "package.json": "{}",
            "vitest.config.ts": 'export default { test: { setupFiles: ["./test/setup.ts"] } }',
            "test/setup.ts": "",
            "src/a.test.ts": "",
            "ui/package.json": "{}",
            "ui/vitest.config.ts": "export default { test: { globalSetup: './g.ts' } }",
            "ui/g.ts": "",
            "ui/b.spec.ts": "",
            "src/a.ts": "",
        }
    )
    added = _add_setup_edges(graph, parsed, ctx, ctx.path_set)

    assert added == 3
    assert graph["src/a.test.ts"]["test/setup.ts"]["hint_source"] == SETUP_FILE_HINT
    assert graph["src/a.test.ts"]["test/setup.ts"]["edge_type"] == "framework"
    assert graph.has_edge("ui/b.spec.ts", "test/setup.ts")
    assert graph.has_edge("ui/b.spec.ts", "ui/g.ts")
    # The ui config runs only the tests of its own package.
    assert not graph.has_edge("src/a.test.ts", "ui/g.ts")
    assert not graph.has_edge("src/a.ts", "test/setup.ts")


def test_a_config_whose_test_root_climbs_out_links_every_test() -> None:
    assert leaves_its_directory(b'export default { test: { dir: "../tests" } }')
    assert not leaves_its_directory(b'export default { test: { dir: "extensions" } }')
    graph, parsed, ctx = _ctx(
        {
            "web/package.json": "{}",
            "web/vitest.config.ts": "export default { root: '..', test: { setupFiles: ['./s.ts'] } }",
            "web/s.ts": "",
            "tests/a.test.ts": "",
        }
    )
    assert _add_setup_edges(graph, parsed, ctx, ctx.path_set) == 1
    assert graph.has_edge("tests/a.test.ts", "web/s.ts")


# -- helpers named by path -------------------------------------------------------


def test_a_helper_named_relative_to_the_test_is_linked() -> None:
    path_set = {
        "tests/lsp/test_client.py",
        "tests/lsp/_mock_server.py",
        "tests/lsp/__init__.py",
        "tests/fixture-server.ts",
        "tests/data/sample.json",
        "src/server.py",
    }
    py = b'MOCK = str(Path(__file__).parent / "_mock_server.py")\nopen("../data/sample.json")\n'
    assert path_string_targets("tests/lsp/test_client.py", py, path_set) == [
        "tests/lsp/_mock_server.py"
    ]
    js = b'spawn(process.execPath, [path.join(__dirname, "../fixture-server.ts")])'
    assert path_string_targets("tests/lsp/x.test.ts", js, path_set) == ["tests/fixture-server.ts"]
    # A name that only matches elsewhere, a scratch file named like a package
    # marker, a data file and another test are not routes.
    path_set.add("tests/lsp/test_other.py")
    other = b'"server.py" "__init__.py" "../data/sample.json" "test_other.py"'
    assert path_string_targets("tests/lsp/test_client.py", other, path_set) == []


def test_path_string_edges_leave_from_test_files_only() -> None:
    graph, parsed, ctx = _ctx(
        {
            "tests/test_client.py": 'SERVER = Path(__file__).parent / "_mock_server.py"',
            "tests/_mock_server.py": "",
            "src/run.py": '"helper.py"',
            "src/helper.py": "",
        }
    )
    assert _add_path_string_edges(graph, parsed, ctx, ctx.path_set) == 1
    assert graph["tests/test_client.py"]["tests/_mock_server.py"]["hint_source"] == PATH_STRING_HINT


def test_path_string_edges_link_repo_root_target() -> None:
    graph, parsed, ctx = _ctx(
        {
            "tests/unit/dead_code/test_confidence_parity.py": """
repo_root = Path(__file__).parents[3]
ts_file = repo_root / "packages" / "types" / "src" / "dead-code.ts"
""",
            "packages/types/src/dead-code.ts": "export const MEDIUM = 0.4;",
        }
    )
    assert _add_path_string_edges(graph, parsed, ctx, ctx.path_set) == 1
    assert (
        graph["tests/unit/dead_code/test_confidence_parity.py"][
            "packages/types/src/dead-code.ts"
        ]["hint_source"]
        == PATH_STRING_HINT
    )



def test_ancestor_and_repo_root_paths_are_linked() -> None:
    path_set = {
        "tests/unit/dead_code/test_confidence_parity.py",
        "packages/types/src/dead-code.ts",
        "packages/types/src/doc-drift.ts",
        "packages/ui/src/docs/reader-persona.ts",
        "plugins/claude-code/hooks/lens/lens.js",
        "packages/data/sample.json",
    }
    # 1. Direct parents[n] chain inside a function
    src_local = b"""
def test_something():
    repo_root = Path(__file__).parents[3]
    ts_file = repo_root / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_local, path_set
    ) == ["packages/types/src/dead-code.ts"]

    # 2. Direct .resolve().parents[n] chained assignment at module level
    src_contract = b"""
_CONTRACT = (
    Path(__file__).resolve().parents[3] / "packages" / "types" / "src" / "doc-drift.ts"
)
"""
    assert path_string_targets(
        "tests/unit/doc_drift/test_ts_contract_parity.py", src_contract, path_set
    ) == ["packages/types/src/doc-drift.ts"]

    # 3. REPO_ROOT constant at module level used by another constant
    src_persona = b"""
REPO_ROOT = Path(__file__).resolve().parents[3]
READER_PERSONA_TS = REPO_ROOT / "packages" / "ui" / "src" / "docs" / "reader-persona.ts"
"""
    assert path_string_targets(
        "tests/unit/generation/test_structural_pages_read_like_a_wiki.py", src_persona, path_set
    ) == ["packages/ui/src/docs/reader-persona.ts"]

    # 4. Constant built from another constant (ROOT -> PLUGIN_ROOT -> bundle)
    src_plugin = b"""
ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "claude-code"
def test_plugin():
    bundle = PLUGIN_ROOT / "hooks" / "lens" / "lens.js"
"""
    assert path_string_targets(
        "tests/unit/cli/test_claude_plugin.py", src_plugin, path_set
    ) == ["plugins/claude-code/hooks/lens/lens.js"]

    # 5. .parent.parent.parent.parent chain
    src_parent_chain = b"""
root = Path(__file__).parent.parent.parent.parent
target = root / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_parent_chain, path_set
    ) == ["packages/types/src/dead-code.ts"]

    # 6. Single string literal with slashes
    src_single = b"""
root = Path(__file__).parents[3]
target = root / "packages/types/src/dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_single, path_set
    ) == ["packages/types/src/dead-code.ts"]

    # 7. Negative: variable in chain yields nothing
    src_var = b"""
root = Path(__file__).parents[3]
target = root / "packages" / var / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_var, path_set
    ) == []

    # 8. Negative: n climbing past repo root yields nothing
    src_past_root = b"""
root = Path(__file__).parents[4]
target = root / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_past_root, path_set
    ) == []

    # 9. Negative: missing file yields nothing
    src_missing = b"""
root = Path(__file__).parents[3]
target = root / "packages" / "types" / "src" / "missing-file.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_missing, path_set
    ) == []

    # 10. Negative: data file (.json) yields nothing
    src_data = b"""
root = Path(__file__).parents[3]
target = root / "packages" / "data" / "sample.json"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_data, path_set
    ) == []

    # 11. Negative: reassignment to dynamic expression invalidates tracked binding
    src_reassigned = b"""
root = Path(__file__).parents[3]
root = get_root()
target = root / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_reassigned, path_set
    ) == []

    # 12. Negative: annotated reassignment to dynamic expression invalidates tracked binding
    src_ann_reassigned = b"""
root = Path(__file__).parents[3]
root: Path = get_root()
target = root / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_ann_reassigned, path_set
    ) == []

    # 13. Negative: bare type annotation without assignment invalidates tracked binding
    src_ann_bare = b"""
root = Path(__file__).parents[3]
root: Path
target = root / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_ann_bare, path_set
    ) == []

    # 14. Negative: loop variable rebounds and invalidates tracked binding
    src_loop_rebound = b"""
root = Path(__file__).parents[3]
for root in get_roots():
    pass
target = root / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_loop_rebound, path_set
    ) == []

    # 15. Negative: function parameter shadows module-level ancestor binding
    src_param_shadowed = b"""
ROOT = Path(__file__).parents[3]
def f(ROOT):
    target = ROOT / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_param_shadowed, path_set
    ) == []

    # 16. Local function reassignment invalidates inside function but preserves outer binding
    src_local_preserve = b"""
ROOT = Path(__file__).parents[3]
def helper():
    ROOT = get_root()
    target1 = ROOT / "packages" / "types" / "src" / "dead-code.ts"

def test_fn():
    target2 = ROOT / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_local_preserve, path_set
    ) == ["packages/types/src/dead-code.ts"]

    # 17. Negative: comprehension loop variable shadows module-level ancestor binding
    src_comp_shadowed = b"""
ROOT = Path(__file__).parents[3]
targets = [ROOT / "packages" / "types" / "src" / "dead-code.ts" for ROOT in get_roots()]
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_comp_shadowed, path_set
    ) == []

    # 18. Negative: lambda parameter shadows module-level ancestor binding
    src_lambda_shadowed = b"""
ROOT = Path(__file__).parents[3]
fn = lambda ROOT: ROOT / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_lambda_shadowed, path_set
    ) == []

    # 19. Negative: context manager target invalidates tracked binding
    src_with_invalidated = b"""
ROOT = Path(__file__).parents[3]
with open("some_file") as ROOT:
    target = ROOT / "packages" / "types" / "src" / "dead-code.ts"
"""
    assert path_string_targets(
        "tests/unit/dead_code/test_confidence_parity.py", src_with_invalidated, path_set
    ) == []



