"""The walk cache and the token cache fingerprint all the code that shapes them.

Each cache keys on a hash of an explicit module set (``WALK_INPUTS``,
``TOKEN_INPUTS``) instead of ``HEALTH_ANALYZER_VERSION``, so a scoring change
keeps them warm. That is only safe while the set is closed: a module the set
imports at run time is either in the set or listed below with the reason its
code cannot change a cached value. An import from outside the set with no
entry here fails, which is the point: classify it before the cache can serve
values that module would now compute differently.

Ceiling: this reads imports, not calls, so an exemption's reason is a reviewed
claim about which of the module's names the walk reaches. The engine's call
into the walker is not hashed either; the arguments it passes are the key's
terms (grammar, bytes, vocabulary, SQL dialect).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import repowise.core as core
from repowise.core.analysis.health.complexity.walker import walk_file
from repowise.core.analysis.health.duplication.token_cache import TOKEN_INPUTS
from repowise.core.analysis.health.duplication.token_entry import tokenize_entry
from repowise.core.analysis.health.sql_complexity import walk_sql_file
from repowise.core.analysis.health.walk_cache import WALK_INPUTS

_CORE = Path(core.__file__).resolve().parent
_PKG = "repowise.core"

# Shared by both sets: what the grammar loader and source preparation import
# without running it on the way to a tree.
_PARSE_EXEMPT = {
    "ingestion.parser": "package re-export of parser.grammars, which is hashed",
    "ingestion.parser_helpers": (
        "re-exports source_prep's sanitizers (hashed); grammars and sfc_source call "
        "its error-node and enclosing-symbol helpers only while building ingestion facts"
    ),
    "ingestion.languages.registry": "folded in by value through grammar_choices()",
    "ingestion.language_configs": "grammars reads it only for its grammar-gap reports",
    "ingestion.models": "types for ingestion's call sites and synthesized symbols",
    "ingestion.extractors.synthetic_symbols._helpers": (
        "macro symbol synthesis; source_prep reads only cpp_macros' token list"
    ),
}

_WALK_EXEMPT = {
    **_PARSE_EXEMPT,
    "analysis.health.biomarkers.complex_method": (
        "dataflow.gating's refactoring thresholds; the walk never calls the gate"
    ),
    "analysis.health.biomarkers.large_method": (
        "dataflow.gating's refactoring thresholds; the walk never calls the gate"
    ),
    "ingestion.extractors": "package re-export of extractors.helpers.node_text, which is hashed",
    "ingestion.special_handlers": "reads the configured SQL dialect, which keys the walk",
    "test_paths": "class_analysis.cohesion_applies, which the scorer calls, not the walk",
}

_TOKEN_EXEMPT = {
    **_PARSE_EXEMPT,
    "ingestion.language_configs": "folded in by value: the import kinds the tokenizer drops",
}


def _module_name(path: Path) -> str:
    parts = path.relative_to(_CORE).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _module_path(name: str) -> Path | None:
    base = _CORE.joinpath(*name.split(".")) if name else _CORE
    for candidate in (base / "__init__.py", base.with_suffix(".py")):
        if candidate.is_file():
            return candidate
    return None


def _inputs(patterns: tuple[str, ...]) -> dict[str, Path]:
    return {_module_name(p): p for pattern in patterns for p in _CORE.glob(pattern)}


def _is_type_checking(node: ast.If) -> bool:
    test = node.test
    return getattr(test, "id", getattr(test, "attr", None)) == "TYPE_CHECKING"


def _runtime_imports(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    found: list[ast.Import | ast.ImportFrom] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.append(node)
        elif isinstance(node, ast.If) and _is_type_checking(node):
            for child in node.orelse:
                found.extend(_runtime_imports(ast.Module(body=[child], type_ignores=[])))
        else:
            found.extend(_runtime_imports(node))
    return found


def _absolute(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = module.split(".") if is_package else module.split(".")[:-1]
    package = package[: len(package) - (node.level - 1)]
    prefix = ".".join([_PKG, *package])
    return f"{prefix}.{node.module}" if node.module else prefix


def _targets(module: str, path: Path) -> set[str]:
    """``repowise.core`` modules *module* imports at run time, relative to the package.

    ``from pkg import name`` counts as ``pkg.name`` when that is a module and as
    ``pkg`` otherwise.
    """
    out: set[str] = set()
    for node in _runtime_imports(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            base = _absolute(module, path.name == "__init__.py", node)
            names = [
                f"{base}.{alias.name}" if _module_path(_rel(f"{base}.{alias.name}")) else base
                for alias in node.names
            ]
        out.update(_rel(name) for name in names if name.startswith(f"{_PKG}."))
    return {name for name in out if _module_path(name) is not None}


def _rel(name: str) -> str:
    return name[len(_PKG) + 1 :]


def _outside(patterns: tuple[str, ...]) -> set[str]:
    inputs = _inputs(patterns)
    return {t for m, p in inputs.items() for t in _targets(m, p)} - set(inputs)


@pytest.mark.parametrize(
    ("patterns", "exempt"),
    [(WALK_INPUTS, _WALK_EXEMPT), (TOKEN_INPUTS, _TOKEN_EXEMPT)],
    ids=["walk", "token"],
)
def test_every_import_from_outside_the_inputs_is_reviewed(
    patterns: tuple[str, ...], exempt: dict[str, str]
) -> None:
    """A new name here means: add the module to the inputs, or say why it cannot shape a value."""
    outside = _outside(patterns)
    assert sorted(outside - set(exempt)) == [], "unreviewed import from outside the cache inputs"
    assert sorted(set(exempt) - outside) == [], "stale exemption: nothing in the inputs imports it"


@pytest.mark.parametrize("patterns", [WALK_INPUTS, TOKEN_INPUTS], ids=["walk", "token"])
def test_every_input_pattern_matches_a_file(patterns: tuple[str, ...]) -> None:
    assert [p for p in patterns if not list(_CORE.glob(p))] == []


@pytest.mark.parametrize(
    ("entry", "patterns"),
    [(walk_file, WALK_INPUTS), (walk_sql_file, WALK_INPUTS), (tokenize_entry, TOKEN_INPUTS)],
    ids=["walk_file", "walk_sql_file", "tokenize_entry"],
)
def test_the_entry_points_are_inputs(entry: object, patterns: tuple[str, ...]) -> None:
    source = Path(str(inspect.getsourcefile(entry))).resolve()
    assert source in set(_inputs(patterns).values())
