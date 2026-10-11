from __future__ import annotations

import ast
import pathlib

import pytest

from repowise.core.analysis.health.refactoring.split_file import _is_generated_path

_KNOWN: frozenset[str] = frozenset(
    {
        "packages/core/src/repowise/core/entry_candidacy.py",
        "packages/core/src/repowise/core/ingestion/tsconfig_resolver.py",
        "packages/server/src/repowise/server/services/c4_builder/architecture.py",
    }
)

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"
_HOME = "packages/core/src/repowise/core/analysis/dead_code/file_reachability.py"
_BARREL_MARKERS = {
    "index.ts",
    "index.tsx",
    "index.js",
    "index.jsx",
    "index.mts",
    "index.cts",
    "index.mjs",
    "index.cjs",
    "__init__.py",
    "mod.rs",
}


def _check_assignment(node: ast.AST) -> list[str]:
    if (
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "BARREL_FILENAMES" for target in node.targets
        )
    ) or (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "BARREL_FILENAMES"
    ):
        return ["BARREL_FILENAMES"]
    return []


def _check_literals(node: ast.AST) -> list[str]:
    if not isinstance(node, ast.Tuple | ast.Set | ast.List):
        return []
    str_elts = {
        elt.value
        for elt in node.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    }
    matched = str_elts & _BARREL_MARKERS
    if len(matched) >= 2:
        return [f"literal {sorted(matched)}"]
    return []


def _analyze_file(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        hits.extend(_check_assignment(node))
        hits.extend(_check_literals(node))
    return sorted(set(hits))


def _offenders() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _PACKAGES.rglob("*.py"):
        rel = path.relative_to(_PACKAGES.parents[0]).as_posix()
        if rel == _HOME:
            continue
        hits = _analyze_file(path)
        if hits:
            found[rel] = hits
    return found


def test_no_new_barrel_filename_copies() -> None:
    offenders = {p: hits for p, hits in _offenders().items() if p not in _KNOWN}
    assert not offenders, (
        "New inline barrel-filename definition(s) outside file_reachability.py:\n"
        + "\n".join(f"  {p}: {', '.join(hits)}" for p, hits in sorted(offenders.items()))
    )


@pytest.mark.parametrize(
    "path, expected",
    [
        ("src/index.ts", True),
        ("src/index.tsx", True),
        ("src/index.js", True),
        ("src/index.jsx", True),
        ("src/index.mts", True),
        ("src/index.cts", True),
        ("src/index.mjs", True),
        ("src/index.cjs", True),
        ("pkg/__init__.py", True),
        ("src/mod.rs", True),
        ("src/utils.ts", False),
        ("src/component.tsx", False),
    ],
)
def test_split_file_is_generated_path_covers_all_barrels(path: str, expected: bool) -> None:
    assert _is_generated_path(path) is expected
