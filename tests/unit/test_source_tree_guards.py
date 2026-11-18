"""Guard tests that read the whole package source tree, in one module.

Each section below polices one rule over ``packages/`` by reading every
Python file. They share one parse from ``tests/unit/_source_tree.py``, and
living in one module keeps that parse, and the memory it holds, on one
xdist worker under ``--dist loadfile``. A guard that reads the tree belongs
here; its unit tests on small inputs can stay beside the code they test.
"""

from __future__ import annotations

import ast
import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

import repowise.core.ingestion.type_names as type_names
import repowise.core.test_paths as test_paths
from repowise.core.analysis.health.refactoring.split_file import _is_generated_path
from repowise.core.analysis.knowledge_graph import _DOC_EXTENSIONS as KG_DOC_EXTENSIONS
from repowise.core.generation.categories import _DOC_SUFFIXES
from repowise.core.ingestion.git_commit_index import _DEEP_CACHE_GLOB, _deep_cache_name
from repowise.core.ingestion.git_indexer.fix_shape import _DOC_EXT
from repowise.core.ingestion.models import EDGE_TYPE_VALUES
from repowise.core.pipeline.incremental.carried_files import (
    CARRIED_DIRS,
    CARRIED_FILES,
    CARRIED_GLOBS,
    SEALED_FILES,
)
from repowise.core.providers.embedding.registry import _BUILTIN_EMBEDDERS
from repowise.core.providers.llm.specs import PROVIDER_SPECS
from repowise.core.support_paths import DOC_EXTENSIONS
from tests.unit._source_tree import PACKAGES, SourceFile, python_sources, sources_under

# ============================================================================
# Edge types and resolution origins: emitted and consumed values are declared.
# ============================================================================


# Call sites that build the edge type instead of naming it. Each needs the
# reason it cannot be a literal, because "it's computed" is also what a call
# site looks like just before it starts emitting something undeclared.
_COMPUTED: frozenset[str] = frozenset(
    {
        # Prefixes a `DynamicKind` into `dynamic_<kind>`. The input vocabulary
        # is a Literal, so the output is closed even though it is not literal
        # here; `test_every_dynamic_kind_maps_into_the_vocabulary` pins it.
        "packages/core/src/repowise/core/ingestion/graph/_edges.py",
        # Both replay persisted rows back onto a fresh graph. The edge type
        # comes from the database, which this test cannot reach; the rows were
        # written by the producers this test does check.
        "packages/core/src/repowise/core/ingestion/graph/_rehydrate.py",
        "packages/core/src/repowise/core/persistence/coordinator.py",
    }
)


# `add_edge` calls on graphs that are not the symbol/file dependency graph, so
# `EdgeType` does not govern them. Each is a separate model with its own
# vocabulary.
_OTHER_GRAPHS: frozenset[str] = frozenset(
    {
        # Cross-repo SystemGraph: `kind` is a SystemEdge kind ("package",
        # "co_change", contract-derived), a different vocabulary entirely.
        "packages/core/src/repowise/core/workspace/system_graph.py",
        "packages/core/src/repowise/core/workspace/architecture_metrics.py",
        # Scratch networkx graphs built for one algorithm and discarded:
        # community detection, cycle finding, split scoring, page levels.
        # They carry no edge_type at all.
        "packages/core/src/repowise/core/analysis/communities.py",
        "packages/core/src/repowise/core/analysis/health/refactoring/graph_signals.py",
        "packages/core/src/repowise/core/analysis/health/refactoring/split_file.py",
        "packages/core/src/repowise/core/generation/page_generator/levels.py",
        # Interface/passthrough declarations, not producers.
        "packages/core/src/repowise/core/persistence/_interfaces/graph_store.py",
        "packages/core/src/repowise/core/persistence/stores/in_process_graph_store.py",
    }
)


def _parsed() -> list[SourceFile]:
    """The package sources that parse (``node_modules`` is never walked)."""
    return [src for src in python_sources() if src.tree is not None]


def _edge_type_literals() -> dict[str, set[str]]:
    """Map of repo-relative path -> literal `edge_type=` values it passes to add_edge."""
    found: dict[str, set[str]] = {}
    for src in _parsed():
        rel, tree = src.rel, src.tree
        if rel in _OTHER_GRAPHS or rel in _COMPUTED:
            continue
        for node in ast.walk(tree):
            # `graph.add_edge(u, v, edge_type="imports")`
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name != "add_edge":
                    continue
                for kw in node.keywords:
                    if kw.arg == "edge_type" and _is_str_constant(kw.value):
                        found.setdefault(rel, set()).add(kw.value.value)  # type: ignore[attr-defined]
            # `attrs = {"edge_type": "imports", ...}` then `add_edge(u, v, **attrs)`.
            # builder.py builds edges this way, so without this the splat form
            # would be invisible to the check.
            elif isinstance(node, ast.Dict):
                for key, val in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "edge_type"
                        and _is_str_constant(val)
                    ):
                        found.setdefault(rel, set()).add(val.value)  # type: ignore[attr-defined]
    return found


def _is_str_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _edge_type_set_members() -> dict[str, set[str]]:
    """Map of `path::CONSTANT_NAME` -> string members of every *_EDGE_TYPES collection."""
    found: dict[str, set[str]] = {}
    for src in _parsed():
        rel, tree = src.rel, src.tree
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not any(n.endswith("_EDGE_TYPES") for n in names):
                continue
            if node.value is None:
                continue
            members = _string_members(node.value)
            if members:
                found[f"{rel}::{names[0]}"] = members
    return found


def _string_members(value: ast.expr) -> set[str]:
    """Every string literal in a set expression, through calls and `|` unions.

    Recursing through `BinOp` is the point. `SYMBOL_USE_EDGE_TYPES | {"heritage"}`
    is a legal way to write a set, and a version of this that skipped BinOp
    entirely let exactly the dead key it was written to catch survive by moving
    to the right of the pipe.
    """
    # frozenset({...}) / set({...}) unwrap to their single argument.
    if isinstance(value, ast.Call):
        return set().union(*(_string_members(a) for a in value.args)) if value.args else set()
    if isinstance(value, ast.BinOp):
        return _string_members(value.left) | _string_members(value.right)
    if isinstance(value, ast.Set | ast.List | ast.Tuple):
        return {
            el.value for el in value.elts if isinstance(el, ast.Constant) and isinstance(el.value, str)
        }
    return set()  # a bare Name — checked where it is defined


def _alias_targets() -> dict[str, tuple[str, set[str]]]:
    """Map `path::CONSTANT` -> (referenced name, names that file imports from models).

    Only for `*_EDGE_TYPES = <bare name>` and the `sorted(<bare name>)` /
    `frozenset(<bare name>)` wrappers around one. `_string_members` returns an
    empty set for those shapes, so without this they are invisible to both
    directions of the check.
    """
    found: dict[str, tuple[str, set[str]]] = {}
    for src in _parsed():
        rel, tree = src.rel, src.tree
        from_models = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("ingestion.models")
            for alias in node.names
        }
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not any(n.endswith("_EDGE_TYPES") for n in names) or node.value is None:
                continue
            value = node.value
            if isinstance(value, ast.Call) and len(value.args) == 1:
                value = value.args[0]
            if isinstance(value, ast.Name):
                found[f"{rel}::{names[0]}"] = (value.id, from_models)
    return found


def _resolved_call_origins() -> dict[str, set[float]]:
    """Map origin literal -> confidences it is constructed with, from the AST.

    Reads the ``ResolvedCall(caller, callee, <confidence>, line, <origin>)``
    construction sites rather than the ``add_edge`` call, because the edge
    passes a variable and the literal only ever exists at the resolver.
    """
    found: dict[str, set[float]] = {}
    for src in _parsed():
        tree = src.tree
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "ResolvedCall":
                continue
            origin = next(
                (kw.value for kw in node.keywords if kw.arg == "origin"),
                node.args[4] if len(node.args) > 4 else None,
            )
            if origin is None or not _is_str_constant(origin):
                continue
            confidence = node.args[2] if len(node.args) > 2 else None
            confs = found.setdefault(origin.value, set())  # type: ignore[attr-defined]
            if isinstance(confidence, ast.Constant) and isinstance(
                confidence.value, int | float
            ):
                confs.add(float(confidence.value))
    return found


class TestEdgeTypeVocabulary:
    """The declared edge-type vocabulary must stay true in both directions.

    `EdgeType` in :mod:`repowise.core.ingestion.models` was decorative for most of
    this repo's life: nothing checked it, so it drifted both ways at once. It
    declared `has_property`, `method_overrides` and a bare `dynamic` that no
    producer has ever emitted, and it omitted `reads`, `dynamic_uses`,
    `dynamic_imports` and `dynamic_url_route`, which producers emit constantly
    (6,750 rows across 42 local indexes).

    That is not a cosmetic defect. Thirteen modules each wrote a private set of
    "which edges are dependencies" *against the declaration*, so three of them
    tested for the bare `"dynamic"` and matched none of the 6,153 real `dynamic_*`
    edges, and `reads` reached exactly one of the thirteen. A vocabulary nothing
    enforces produces consumers that are all subtly, invisibly wrong.

    Two directions, two tests:

    * **Nothing emits an undeclared type** — every `add_edge` call site in the
      packages passes an `edge_type=` the Literal knows.
    * **Nothing consumes a type no one emits** — every module-level `*_EDGE_TYPES`
      set holds only real members, so a dead key like `"heritage"` cannot sit in a
      dependency set pretending to do work.

    Ceiling: this is an AST check on *literal* edge types, in both the
    `add_edge(..., edge_type="x")` and the `{"edge_type": "x"}`-then-splat forms.
    What it cannot see is a computed value — `add_dynamic_edges` builds one by
    prefixing — so those files are listed in ``_COMPUTED`` and covered instead by
    the `DynamicKind` Literal on the field they read. A type assembled at runtime
    from a variable would still slip through, so this is not a proof that no
    undeclared type can be emitted.

    mypy cannot do this job instead: every `add_edge` in the tree ultimately lands
    on ``networkx.DiGraph.add_edge(**attrs)`` or ``GraphStore.add_edge(**attrs:
    Any)``, so there is no parameter to annotate and no error for it to raise.
    """

    def test_nothing_emits_an_undeclared_edge_type(self) -> None:
        offenders = {
            path: sorted(values - EDGE_TYPE_VALUES)
            for path, values in _edge_type_literals().items()
            if values - EDGE_TYPE_VALUES
        }
        assert not offenders, (
            "add_edge call site(s) emitting an edge type the EdgeType Literal does not declare:\n"
            + "\n".join(f"  {p}: {', '.join(v)}" for p, v in sorted(offenders.items()))
            + "\n\nAdd it to EdgeType in repowise.core.ingestion.models, or emit a declared type."
            " An undeclared type is invisible to every consumer that filters on the vocabulary."
        )

    def test_no_consumer_set_holds_a_type_nothing_emits(self) -> None:
        offenders = {
            name: sorted(members - EDGE_TYPE_VALUES)
            for name, members in _edge_type_set_members().items()
            if members - EDGE_TYPE_VALUES
        }
        assert not offenders, (
            "Edge-type set(s) containing a type no producer emits:\n"
            + "\n".join(f"  {n}: {', '.join(v)}" for n, v in sorted(offenders.items()))
            + "\n\nA dead key does no work and hides that the set is incomplete —"
            " `_DEPENDENCY_EDGE_TYPES` held 'heritage' while missing every real dynamic_* type."
            " Remove it, or add the type to EdgeType if something really does emit it."
        )

    @pytest.mark.parametrize("exempt", sorted(_COMPUTED | _OTHER_GRAPHS))
    def test_exemptions_still_call_add_edge(self, exempt: str) -> None:
        """Every exemption must still be a real add_edge site, so neither list can rot.

        Both lists, not just ``_COMPUTED``: an entry left in ``_OTHER_GRAPHS`` after
        its file starts writing real ``EdgeType`` values would be exempt forever
        and silently.
        """
        path = PACKAGES.parents[0] / exempt
        assert path.exists(), f"{exempt} no longer exists — remove it from the exemption list"
        assert "add_edge" in path.read_text(encoding="utf-8"), (
            f"{exempt} no longer calls add_edge — remove it from the exemption list"
        )

    def test_an_edge_type_alias_points_at_the_shared_vocabulary(self) -> None:
        """`_X_EDGE_TYPES = SOME_NAME` must name something this test can still see.

        The AST checks read literals, so an alias is a blind spot: `_TYPES =
        {"heritage"}` followed by `_CALL_EDGE_TYPES = _TYPES` would smuggle a dead
        key past both directions. Aliasing a shared view is the intended shape and
        stays legal; aliasing an arbitrary local name does not.
        """
        offenders = {
            name: referenced
            for name, (referenced, from_models) in _alias_targets().items()
            if referenced not in from_models and not referenced.endswith("_EDGE_TYPES")
        }
        assert not offenders, (
            "edge-type set(s) aliasing a name this check cannot follow:\n"
            + "\n".join(f"  {n} = {v}" for n, v in sorted(offenders.items()))
            + "\n\nAlias a view imported from repowise.core.ingestion.models, or name the"
            " referent `*_EDGE_TYPES` so it is checked where it is defined."
        )

    def test_nothing_stamps_an_undeclared_resolution_origin(self) -> None:
        from repowise.core.ingestion.models import RESOLUTION_ORIGIN_VALUES

        emitted = set(_resolved_call_origins())
        assert emitted, "found no ResolvedCall construction sites — the AST scan has rotted"
        assert not (emitted - RESOLUTION_ORIGIN_VALUES), (
            "resolution origin(s) stamped but not declared: "
            f"{sorted(emitted - RESOLUTION_ORIGIN_VALUES)}\n"
            "Add them to ResolutionOrigin in repowise.core.ingestion.models. An"
            " undeclared origin reaches consumers as an unrecognised string."
        )

    def test_every_declared_resolution_origin_has_a_producer(self) -> None:
        """The other direction — the failure `EdgeType` shipped for years.

        A declared origin nothing stamps is a word consumers can filter on and
        never match, which is indistinguishable from a filter that works.
        """
        from repowise.core.ingestion.models import RESOLUTION_ORIGIN_VALUES

        orphans = RESOLUTION_ORIGIN_VALUES - set(_resolved_call_origins())
        assert not orphans, (
            f"resolution origin(s) declared with no producer: {sorted(orphans)}"
        )

    def test_each_resolution_origin_carries_one_confidence(self) -> None:
        """One origin, one confidence — what makes the stamping auditable.

        The origin distribution and the confidence histogram are then two views of
        the same data, so an origin that starts disagreeing with the number it is
        supposed to explain fails here instead of quietly reshaping both.
        """
        split = {o: sorted(c) for o, c in _resolved_call_origins().items() if len(c) > 1}
        assert not split, (
            "resolution origin(s) constructed at more than one confidence:\n"
            + "\n".join(f"  {o}: {c}" for o, c in sorted(split.items()))
            + "\n\nSplit the origin, or give the strategies one confidence."
        )

    def test_reads_edge_producer_is_strictly_file_level(self) -> None:
        """`reads` is produced only at the file level: C# member access and Salesforce schema."""
        producers = [
            path for path, types in _edge_type_literals().items() if "reads" in types
        ]
        assert sorted(producers) == [
            "packages/core/src/repowise/core/ingestion/languages/csharp_member_reads.py",
            "packages/core/src/repowise/core/ingestion/salesforce_schema.py",
        ], (
            f"`reads` edge emitted by unexpected producers: {producers}"
        )


# ============================================================================
# Health snapshot writers: every save_health_snapshot call records the same maps.
# ============================================================================


# Only the shipped Python, not the UI packages' ``node_modules``.
_SNAPSHOT_SOURCE_ROOTS = sorted(PACKAGES.glob("*/src"))


# The store wrapper forwards whatever it is handed; it builds no maps of its
# own, so it is a pass-through rather than a writer with an opinion.
_FORWARDERS = {"_sql_analysis.py"}


def _snapshot_calls() -> list[tuple[Path, ast.Call]]:
    out: list[tuple[Path, ast.Call]] = []
    for src in sources_under(*_SNAPSHOT_SOURCE_ROOTS):
        if src.path.name in _FORWARDERS or src.tree is None:
            continue
        for node in ast.walk(src.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "save_health_snapshot":
                out.append((src.path, node))
    return out


class TestSnapshotWritersAgree:
    """Every snapshot writer has to record the same thing.

    ``save_health_snapshot`` is called from four places: the ``repowise health``
    persister, the upgrade flow, the full-index pipeline, and the store wrapper
    that forwards to crud. A repo's history is whichever of those wrote it last.

    This has already gone wrong once here, with a different argument: four
    ``HealthAnalyzer`` call sites of which only one passed ``community_label_map``, so a
    repo's module namespace flipped depending on which command last ran. The same
    shape applied to ``per_file_deductions`` would make a floored file's trend
    depth appear and disappear as the user alternated between ``repowise health``,
    ``repowise upgrade`` and a full index — a bug that reproduces only for people
    who use more than one of them.

    So this walks the source rather than any one code path. A fifth writer added
    later gets caught the day it lands, which is the only time the fix is cheap.
    """

    def test_the_writers_this_test_guards_still_exist(self) -> None:
        """Without this the sweep below passes vacuously if the call is renamed."""
        calls = _snapshot_calls()
        assert len(calls) >= 3, [str(p) for p, _ in calls]

    def test_every_writer_records_the_depth_as_well_as_the_score(self) -> None:
        missing = []
        for path, call in _snapshot_calls():
            kwargs = {kw.arg for kw in call.keywords}
            # ``**fields`` is ``trends.snapshot_fields``, which carries both maps.
            if None in kwargs:
                continue
            if not {"per_file_scores", "per_file_deductions"} <= kwargs:
                missing.append(f"{path.name}:{call.lineno} passes {sorted(kwargs)}")
        assert not missing, "snapshot writers that would record a different history: " + "; ".join(
            missing
        )

    def test_no_writer_builds_the_maps_by_hand(self) -> None:
        """Both maps come from ``snapshot_file_maps``.

        The score map used to be an inline dict comprehension repeated at each
        site. Two comprehensions that agree today drift the moment one of them
        learns about rounding, exclusions or test files and the others do not.
        """
        inline = []
        for path, call in _snapshot_calls():
            for kw in call.keywords:
                if kw.arg in {"per_file_scores", "per_file_deductions"} and not isinstance(
                    kw.value, ast.Name
                ):
                    inline.append(f"{path.name}:{call.lineno} builds {kw.arg} inline")
        assert not inline, "; ".join(inline)


# ============================================================================
# Carried files: every cache file the source names survives an update.
# ============================================================================


_CARRIED_SOURCE_ROOTS = [PACKAGES / name / "src" / "repowise" for name in ("core", "cli", "server")]


# Any pickle, or a data file named like a cache or a scan record.
_CACHE_NAME = re.compile(
    r"^[\w.-]+\.(pkl|pickle)$|^[\w.-]*(cache|scan)[\w.-]*\.(json|jsonl|pkl|pickle|db|bin|msgpack)$",
    re.IGNORECASE,
)


def _literal(node: ast.AST) -> str | None:
    """A string constant, or an f-string with each placeholder read as ``1``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "1" for part in node.values
        )
    return None


def _modules() -> list[tuple[Path, ast.Module]]:
    files = sources_under(*_CARRIED_SOURCE_ROOTS)
    assert files, f"no sources under {_CARRIED_SOURCE_ROOTS}"
    unparsed = [src.rel for src in files if src.tree is None]
    assert not unparsed, f"sources that do not parse: {unparsed}"
    return [(src.path, src.tree) for src in files]


def _cache_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        value = _literal(node)
        if value is not None and _CACHE_NAME.match(value):
            names.add(value)
    return names


def _carried(name: str) -> bool:
    return name in CARRIED_FILES or any(fnmatchcase(name, glob) for glob in CARRIED_GLOBS)


class TestCarriedFiles:
    """The carried-files list names every cache the source writes under ``.repowise/``.

    Scans the source text rather than a hand list, so a new cache module fails here
    until it is carried. The strong guard is any pickle literal; JSON caches are
    caught when named like a cache or a scan record.
    """

    def test_every_cache_filename_in_the_source_is_carried(self) -> None:
        missing = {
            f"{path.relative_to(PACKAGES).as_posix()}: {name}"
            for path, tree in _modules()
            for name in _cache_names(tree)
            if not _carried(name)
        }
        assert not missing, (
            "cache files not in repowise.core.pipeline.incremental.carried_files: "
            f"{sorted(missing)}"
        )

    def test_sealed_files_are_the_pickles_of_cache_seal_users(self) -> None:
        sealed = set()
        for _path, tree in _modules():
            uses_seal = any(
                isinstance(node, ast.ImportFrom) and node.module == "repowise.core.cache_seal"
                for node in ast.walk(tree)
            )
            if uses_seal:
                sealed |= {n for n in _cache_names(tree) if n.endswith((".pkl", ".pickle"))}
        assert sealed == set(SEALED_FILES)

    def test_deep_cache_glob_matches_every_region(self) -> None:
        assert _DEEP_CACHE_GLOB in CARRIED_GLOBS
        for region in (1, 2, 7, 12):
            assert fnmatchcase(_deep_cache_name(region), _DEEP_CACHE_GLOB)

    def test_lists_have_no_duplicates_or_paths(self) -> None:
        names = [*CARRIED_FILES, *CARRIED_GLOBS, *CARRIED_DIRS]
        assert len(names) == len(set(names))
        assert set(SEALED_FILES) <= set(CARRIED_FILES)
        assert all("/" not in n and "\\" not in n for n in names)


# ============================================================================
# Test-path predicates live only in core.test_paths.
# ============================================================================


# Function-name shapes that answer this question. Matched on the definition, so
# a helper named for something else that happens to take a path is unaffected.
_PREDICATE_NAMES = (
    "_is_test_file",
    "_is_test_path",
    "_looks_like_test_path",
    "_is_test_file_name",
    "_is_test_dir_path",
    # Public spellings too. These shadow the shared module's own exported name,
    # which is the worst case for a wrong import, and listing only the private
    # forms is what kept the two in `_TEST_PATH_KNOWN` below invisible until review.
    "is_test_file",
    "is_test_path",
)


# Module-level constants holding test-path patterns. The `_TEST_PATH_TOKENS`
# tuple existed verbatim in three server modules at once, so a fix to one
# search path silently left the other two behind.
_PREDICATE_CONSTANTS = (
    "_TEST_PATH_TOKENS",
    "_TEST_PATH_RE",
    "_TEST_PATH_FRAGMENTS",
    "_TEST_FILE_SUFFIXES",
    "_TEST_FILE_PREFIXES",
    "_TEST_DIR_FRAGMENTS",
    "_TEST_SUFFIXES",
    "_TEST_DIRS",
    "_TEST_DIR_SEGMENTS",
    "_TEST_FILE_RE",
    "_TEST_FILE_PATTERNS",
    # Both held unanchored substrings and both were measurably wrong:
    # `protest/`, `latest/` and `contest/` all counted as tests, while
    # `myapp/tests.py` and `Foo.Tests/` did not. Neither name was on this list,
    # which is why they stayed green through the whole consolidation.
    "_TEST_PATTERNS",
    "_TEST_ROOTS",
)


# Copies that still exist, with the reason each is still here. Shrink, never
# grow — an entry needs the reason it is exempt written in the code it exempts.
#
# Both entries are deliberate divergences rather than unconverted copies: each
# answers a narrower question than the shared rules, with the reason in a
# comment above it. They became visible only when the public name shapes were
# added to `_PREDICATE_NAMES`, which is the point of recording them.
#
# Note what is deliberately *not* listed: `communities._is_test_node` and
# `move_method._file_is_test` read the flag off a graph node and fall back to the
# shared module. That is the pattern to copy, not a copy to remove.
_TEST_PATH_KNOWN: frozenset[str] = frozenset(
    {
        # Excludes bare `test/` on purpose: `django/test/` and `flask/testing.py`
        # are shipped test-*framework* code, and counting fixes to them would
        # inflate every downstream repo's defect count. Also exempts
        # config-shaped names, because fixing `vitest.config.ts` is a toolchain
        # fix rather than a defect.
        "packages/core/src/repowise/core/ingestion/git_indexer/fix_shape.py",
        # pytest's own `python_files` rule, not ours: a fixture is injected
        # only into a file pytest collects, so this asks "does pytest run
        # this?" rather than "is this test-related code". The shared rules also
        # claim `conftest.py` and `tests/helpers.py`, and giving those fixture
        # parameters would be wrong.
        "packages/core/src/repowise/core/ingestion/framework_edges/pytest_edges.py",
    }
)


# The one place these are allowed to be defined.
_TEST_PATH_HOME = "packages/core/src/repowise/core/test_paths.py"


# The shared answers. A function that calls one of these is a wrapper around
# the single home, not a second implementation of it.
_SHARED = frozenset({"is_test_path", "is_test_support_path", "is_test_related_path"})


def _delegates(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether *fn* answers by calling the shared module rather than re-deriving.

    ``coverage.detector.is_test_file`` adds a source-content branch on top of
    the shared rules, and ``communities._is_test_node`` falls back to them. Both
    are the pattern to copy. Only a body that never reaches the shared module is
    a copy.
    """
    return any(
        (isinstance(node, ast.Name) and node.id in _SHARED)
        or (isinstance(node, ast.Attribute) and node.attr in _SHARED)
        for node in ast.walk(fn)
    )


def _test_path_offenders() -> dict[str, list[str]]:
    """Map of repo-relative path -> the predicate names/constants it defines."""
    found: dict[str, list[str]] = {}
    for src in python_sources():
        rel, tree = src.rel, src.tree
        if rel == _TEST_PATH_HOME or tree is None:  # unparseable: not ours to police
            continue
        hits: list[str] = []
        # Whole tree, not just module level: a copy tucked inside a class body or
        # nested in a function is still a copy.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in _PREDICATE_NAMES
                and not _delegates(node)
            ):
                hits.append(node.name)
            elif isinstance(node, ast.Assign):
                hits.extend(
                    t.id
                    for t in node.targets
                    if isinstance(t, ast.Name) and t.id in _PREDICATE_CONSTANTS
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in _PREDICATE_CONSTANTS
            ):
                hits.append(node.target.id)
        if hits:
            found[rel] = sorted(hits)
    return found


class TestNoTestPathCopies:
    """No fifteenth copy of "is this path a test?" (#1103).

    An architecture check rather than a behaviour one: it fails when a new
    test-path predicate appears anywhere outside :mod:`repowise.core.test_paths`.
    Fourteen of these existed and disagreed on five real layouts, and two of them
    carried docstrings claiming to mirror implementations they had already drifted
    from, so a reviewer noticing the fifteenth is not a plan.

    ``_KNOWN`` holds the call sites that answer a *narrower* question on purpose,
    each with its reason. The list only ever shrinks; adding a plain copy means this
    test tells you to use the shared module instead.

    Ceiling: this matches on names, so a predicate called something the lists below
    do not know stays invisible — which is exactly how the two entries in ``_KNOWN``
    went unnoticed until someone read for them. It catches the shapes that actually
    recurred here, and a wrapper that calls the shared module is not one of them.
    The corpus in ``test_test_paths.py`` is what catches a *wrong* answer; this only
    catches a second implementation of the question.
    """

    def test_no_new_test_path_predicates(self) -> None:
        offenders = {p: names for p, names in _test_path_offenders().items() if p not in _TEST_PATH_KNOWN}
        assert not offenders, (
            "New test-path predicate(s) outside repowise.core.test_paths:\n"
            + "\n".join(f"  {p}: {', '.join(names)}" for p, names in sorted(offenders.items()))
            + "\n\nImport is_test_path / is_test_support_path / is_test_related_path from"
            " repowise.core.test_paths instead. If a call site genuinely needs to differ,"
            " say why in the code and add it to _TEST_PATH_KNOWN in this file."
        )

    def test_known_copies_still_exist(self) -> None:
        """Every allowlist entry must still be a real copy, so the list cannot rot.

        Without this, a converted call site leaves a stale exemption behind and the
        next copy added to that same file goes unnoticed.
        """
        found = _test_path_offenders()
        stale = sorted(p for p in _TEST_PATH_KNOWN if p not in found)
        assert not stale, (
            "These no longer define a test-path predicate — remove them from _TEST_PATH_KNOWN:\n"
            + "\n".join(f"  {p}" for p in stale)
        )

    def test_a_wrapper_is_not_a_copy(self) -> None:
        """The check must not push call sites into re-deriving to dodge it."""
        wrapper = ast.parse(
            "def _is_test_path(p):\n"
            "    return is_test_related_path(p) or p.endswith('.fixture')\n"
        ).body[0]
        copy = ast.parse("def _is_test_path(p):\n    return '/tests/' in p\n").body[0]
        assert _delegates(wrapper)
        assert not _delegates(copy)

    @pytest.mark.parametrize("name", ["is_test_path", "is_test_support_path", "is_test_related_path"])
    def test_shared_module_exports_the_replacements(self, name: str) -> None:
        assert callable(getattr(test_paths, name))


# ============================================================================
# Qualifier splitting lives only in ingestion.type_names.
# ============================================================================


# Splitting on one of these is how a qualifier is taken off a type name.
_SEPARATORS = frozenset({".", "::", "\\"})


# ...and these open a type-argument group or a constructor call, so cutting at
# one is how the other half of the question was answered.
_OPENERS = frozenset({"<", "[", "("})


_SPLIT_METHODS = frozenset({"split", "rsplit", "partition", "rpartition"})


_INGESTION = PACKAGES / "core" / "src" / "repowise" / "core" / "ingestion"


# Where the question gets asked. Everything here reduces a type name written in
# source; nothing else in the tree is scanned.
_SCOPE = (
    _INGESTION / "extractors" / "heritage",
    _INGESTION / "dynamic_hints",
    _INGESTION / "framework_edges",
    _INGESTION / "languages",
    _INGESTION / "parser_helpers.py",
    _INGESTION / "lang_helpers",
    _INGESTION / "type_ref_resolution.py",
    _INGESTION / "call_resolver.py",
    _INGESTION / "call_overload_selection.py",
    _INGESTION / "call_barrel_forwarding.py",
    _INGESTION / "call_inheritance.py",
    _INGESTION / "call_receiver_typing",
    _INGESTION / "heritage_resolver.py",
    # Holds the shared symbol-ID splitter both resolvers used to keep a copy
    # of; in scope so moving it did not move it out of reach.
    _INGESTION / "models.py",
)


_PREFIX = "packages/core/src/repowise/core/ingestion/"


# Sites that split on a separator but are not answering this question, or that
# are and cannot be converted yet, with the reason each is exempt and how many
# matches it is allowed. Shrink, never grow.
_BARE_TYPE_KNOWN: dict[str, int] = {
    # Splits our own `path::Class::method` symbol IDs, which we mint. The
    # separator is ours rather than the language's, so the shared helper would
    # be answering about a type where these ask about an ID. `models.py` holds
    # the one both resolvers used to duplicate. In each call resolver module
    # one site is an import's module path, which is a module name and not a
    # type: `call_receiver_typing/__init__.py` takes its tail and `call_resolver.py`
    # its head, to ask whether the package it names is one of ours. The rest
    # are symbol IDs, except one `call_receiver_typing/__init__.py` site that walks a
    # dotted receiver (`this.a.b`) hop by hop: an instance path, where every
    # segment is a variable or a field and none is discarded, and the `go.py` one that
    # reads the head of a go return type (`pkg.T`) to find the package the
    # callee's file imports as `pkg`; its tail comes from the shared helper.
    _PREFIX + "call_resolver.py": 3,
    # Symbol IDs: a type node's file and name, and a method's enclosing class.
    _PREFIX + "call_inheritance.py": 3,
    _PREFIX + "call_receiver_typing/__init__.py": 3,
    _PREFIX + "call_receiver_typing/go.py": 1,
    _PREFIX + "models.py": 1,
    # Reads the head to decide whether taking a bare name is safe at all: a
    # qualifier that is a type rather than a package must not be discarded.
    # It guards the shared helper and then calls it, so it asks the opposite
    # question. The second, in `_builtin_type_name`, splits a C# array's rank
    # brackets off to keep them (`Item[,]` is not `Item[]`), not to drop them.
    _PREFIX + "languages/receiver_types.py": 2,
    # Normalises the method name out of `#selector(Type.method)`. The last
    # segment here is a member, not a type.
    _PREFIX + "dynamic_hints/swift.py": 1,
    # Splits an import statement's package path, not a type reference.
    _PREFIX + "languages/jvm_same_package.py": 1,
    # Splits a module-path string (`"pkg.mod.Name"`) into the module it loads
    # and the attribute it reads; the head is a module path, not a qualifier.
    _PREFIX + "dynamic_hints/python_imports.py": 1,
    # Qualifies a PHP name against its `use` alias (the HEAD segment) and
    # splits an FQN into the namespace it indexes by and the class: it asks
    # which namespace, the opposite end from the shared helper.
    _PREFIX + "languages/php_same_namespace.py": 2,
    # Takes the head of a URLconf's `views.detail` to reach the Python module
    # declaring the view. The trailing segment is the view function and the
    # head is a module path, so neither end is a type.
    _PREFIX + "framework_edges/django.py": 1,
    # Takes the head of a blueprint variable such as `views.bp` to reach the
    # module that bound it, which is the opposite end from the shared helper.
    _PREFIX + "framework_edges/flask.py": 1,
    # Split a route handler's path to reach a function name, and keep the
    # prefix to resolve the package it came from. Neither wants a type.
    _PREFIX + "framework_edges/rust.py": 1,
    _PREFIX + "framework_edges/go.py": 1,
    _PREFIX + "framework_edges/flask.py": 1,
    # Takes the head of an `import a.b` path (twice: the module's bound names and
    # a test body rebinding a parameter): the name the import binds, a module
    # name, not a type.
    _PREFIX + "framework_edges/pytest_edges.py": 2,
    # Takes the qualifier HEAD of `OrderHandlers.GetOrder` to reach the
    # declaring type. It does want a type, but the shared helper returns the
    # trailing segment, which is the member — the opposite end, the same reason
    # `receiver_types.py` is exempt.
    _PREFIX + "framework_edges/aspnet.py": 1,
    # Real copies, each held by a consumer no gate here measures: converting
    # them moves framework edges, C++ symbol parent names and Go
    # `method_implements` edges respectively. Removable once those are covered.
    _PREFIX + "framework_edges/jakarta.py": 2,
    _PREFIX + "framework_edges/micronaut.py": 2,
    _PREFIX + "framework_edges/spring.py": 4,
    _PREFIX + "parser_helpers.py": 1,
}


# Builtin-type lists are language identity data and belong on the spec beside
# `builtin_calls` and `builtin_parents`, where every language can be compared
# against every other. Matches on the name, so a set spelled differently is out
# of reach.
_SPECS = _INGESTION / "languages" / "specs"


def _splits_on_a_qualifier(node: ast.AST) -> str | None:
    """Describe *node* if it is a call that cuts a name at a qualifier."""
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in _SPLIT_METHODS:
        return None
    if not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    if first.value not in _SEPARATORS | _OPENERS:
        return None
    return f"{node.func.attr}({first.value!r})"


def _bare_type_offenders() -> dict[str, list[str]]:
    """Map of repo-relative path -> the splitting expressions it holds."""
    found: dict[str, list[str]] = {}
    missing = [entry for entry in _SCOPE if not entry.exists()]
    assert not missing, f"_SCOPE names paths that are gone: {missing}"
    for src in sources_under(*_SCOPE):
        rel, tree = src.rel, src.tree
        assert tree is not None, f"{rel} does not parse"
        hits = [
            f"line {node.lineno}: {described}"
            for node in ast.walk(tree)
            if (described := _splits_on_a_qualifier(node))
        ]
        if hits:
            found[rel] = hits
    return found


class TestNoBareTypeNameCopies:
    """No second answer to "what is this type's bare name".

    An architecture check rather than a behaviour one: it fails when a new
    qualifier-splitting expression appears in the modules that resolve type names,
    anywhere outside :mod:`repowise.core.ingestion.type_names`. The
    characterisation tables catch a *wrong* answer from an extractor that already
    exists; only this catches a second implementation appearing.

    These are expressions rather than named functions, so the match is on shape: a
    call to ``split`` / ``rsplit`` / ``partition`` / ``rpartition`` whose first
    argument is a separator a qualifier is spelled with, or a bracket that opens a
    type-argument group. That shape is also how a file extension or a URL is taken
    apart, which is why the scan is confined to the modules that resolve types —
    over the whole tree it would match hundreds of legitimate uses and mean nothing.

    Ceiling: string shape only. A copy that reaches for ``re``, splits on a
    separator held in a variable, or navigates the syntax tree instead of the text
    stays invisible — the per-language head extractors in ``lang_helpers/type_heads.py`` are
    node walks and are deliberately out of reach. It catches the shape that
    actually recurred here.

    ``_KNOWN`` holds the sites that stay, each with the reason it is exempt. It
    records a count rather than a bare path so a new copy cannot hide in a file
    that is already exempt, and a second test holds the counts exact so a converted
    site cannot leave a stale exemption behind.
    """

    def test_no_new_bare_type_name_expressions(self) -> None:
        offenders = {p: hits for p, hits in _bare_type_offenders().items() if len(hits) > _BARE_TYPE_KNOWN.get(p, 0)}
        assert not offenders, (
            "New qualifier-splitting expression(s) outside"
            " repowise.core.ingestion.type_names:\n"
            + "\n".join(f"  {p}\n    " + "\n    ".join(hits) for p, hits in sorted(offenders.items()))
            + "\n\nCall bare_type_name / strip_type_arguments / strip_call_arguments from"
            " repowise.core.ingestion.type_names instead. If a site genuinely asks a"
            " different question, say why in the code and add it to _BARE_TYPE_KNOWN in this file."
        )

    def test_known_copies_still_exist(self) -> None:
        """Every allowlist count must be exact, so the list cannot rot."""
        found = _bare_type_offenders()
        stale = {
            path: (expected, len(found.get(path, [])))
            for path, expected in _BARE_TYPE_KNOWN.items()
            if len(found.get(path, [])) != expected
        }
        assert not stale, (
            "These no longer hold the number of matches _BARE_TYPE_KNOWN allows"
            " (path: allowed -> actual):\n"
            + "\n".join(f"  {p}: {exp} -> {act}" for p, (exp, act) in sorted(stale.items()))
        )

    def test_the_shape_is_what_gets_caught(self) -> None:
        """A copy is caught; taking a path or an extension apart is not."""

        def found(source: str) -> list[str]:
            return [
                described
                for node in ast.walk(ast.parse(source))
                if (described := _splits_on_a_qualifier(node))
            ]

        assert found('bare = raw.rsplit("::", 1)[-1]') == ["rsplit('::')"]
        assert found('head = raw.split("<")[0]') == ["split('<')"]
        assert found('ext = name.rsplit("/", 1)[-1]') == []
        assert found("part = raw.split(sep)[0]") == []

    def test_builtin_type_lists_live_on_the_language_specs(self) -> None:
        strays: dict[str, list[str]] = {}
        for src in python_sources():
            path, tree = src.path, src.tree
            if _SPECS in path.parents or tree is None:  # unparseable: not ours to police
                continue
            names: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    names.extend(
                        t.id
                        for t in node.targets
                        if isinstance(t, ast.Name) and t.id.endswith("BUILTIN_TYPES")
                    )
                elif (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id.endswith("BUILTIN_TYPES")
                ):
                    names.append(node.target.id)
            if names:
                strays[path.relative_to(PACKAGES.parent).as_posix()] = sorted(names)
        assert not strays, (
            "Builtin-type list(s) outside ingestion/languages/specs/:\n"
            + "\n".join(f"  {p}: {', '.join(n)}" for p, n in sorted(strays.items()))
            + "\n\nPut them on that language's LanguageSpec.builtin_types and read them"
            " back with get_builtin_types()."
        )

    @pytest.mark.parametrize(
        "name", ["bare_type_name", "strip_type_arguments", "strip_call_arguments"]
    )
    def test_shared_module_exports_the_replacements(self, name: str) -> None:
        assert callable(getattr(type_names, name))


# ============================================================================
# Barrel filenames live only in ingestion.barrels.
# ============================================================================


_BARREL_KNOWN: frozenset[str] = frozenset(
    {
        "packages/core/src/repowise/core/entry_candidacy.py",
        "packages/core/src/repowise/core/ingestion/tsconfig_resolver.py",
        "packages/server/src/repowise/server/services/c4_builder/architecture.py",
    }
)


_BARREL_HOME = "packages/core/src/repowise/core/ingestion/barrels.py"


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


def _barrel_check_assignment(node: ast.AST) -> list[str]:
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


def _barrel_analyze_file(tree: ast.Module | None) -> list[str]:
    if tree is None:
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        hits.extend(_barrel_check_assignment(node))
        hits.extend(_check_literals(node))
    return sorted(set(hits))


def _barrel_offenders() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for src in python_sources():
        rel = src.rel
        if rel == _BARREL_HOME:
            continue
        hits = _barrel_analyze_file(src.tree)
        if hits:
            found[rel] = hits
    return found


class TestNoBarrelFilenameCopies:

    def test_no_new_barrel_filename_copies(self) -> None:
        offenders = {p: hits for p, hits in _barrel_offenders().items() if p not in _BARREL_KNOWN}
        assert not offenders, (
            "New inline barrel-filename definition(s) outside ingestion/barrels.py:\n"
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
    def test_split_file_is_generated_path_covers_all_barrels(self, path: str, expected: bool) -> None:
        assert _is_generated_path(path) is expected


# ============================================================================
# Doc extensions live only in core.support_paths.
# ============================================================================


# Constant name shapes that define documentation extension sets.
_DOC_CONSTANTS = (
    "DOC_EXTENSIONS",
    "_DOC_EXTENSIONS",
    "_DOC_EXT",
    "_DOC_SUFFIXES",
    "_DOC_EXTS",
)


# Canonical doc extension markers that distinguish general documentation sets
# from language parser specs (e.g. .adoc / .rst / .txt alongside .md).
_DOC_MARKERS = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})


# Call sites that answer a narrower question on purpose, with reasons.
_DOC_KNOWN: frozenset[str] = frozenset()


_DOC_HOME = "packages/core/src/repowise/core/support_paths.py"


def _extract_literals(node: ast.AST) -> set[str]:
    """Extract string constant elements from set, tuple, list, or Call literals (e.g. frozenset({...}))."""
    if isinstance(node, ast.Tuple | ast.Set | ast.List):
        return {
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
    if isinstance(node, ast.Call):
        # e.g. frozenset({".md", ...}) or set([".md", ...])
        for arg in node.args:
            literals = _extract_literals(arg)
            if literals:
                return literals
    return set()


def _is_doc_constant_target(target: ast.AST) -> bool:
    return isinstance(target, ast.Name) and target.id in _DOC_CONSTANTS


def _doc_check_assignment(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Check if an assignment creates a hand-written doc-extensions copy."""
    targets: list[ast.AST] = node.targets if isinstance(node, ast.Assign) else [node.target]

    named_constant = any(_is_doc_constant_target(t) for t in targets)
    literals = _extract_literals(node.value) if node.value is not None else set()

    hits: list[str] = []
    # 1. Named constant assigned to a literal collection rather than referencing DOC_EXTENSIONS
    if named_constant and literals:
        hits.append(f"constant assigned literal {sorted(literals)}")

    # 2. Literal collection containing 4+ doc markers or .adoc with doc extensions
    matched = literals & _DOC_MARKERS
    if (len(matched) >= 4 or (len(matched) >= 3 and ".adoc" in matched)) and not hits:
        hits.append(f"literal doc extensions {sorted(matched)}")

    return hits


def _doc_analyze_file(tree: ast.Module | None) -> list[str]:
    if tree is None:
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            hits.extend(_doc_check_assignment(node))
    return sorted(set(hits))


def _doc_offenders() -> dict[str, list[str]]:
    """Map of repo-relative path -> the forbidden doc-extension copies it defines."""
    found: dict[str, list[str]] = {}
    for src in python_sources():
        rel = src.rel
        if rel == _DOC_HOME:
            continue
        hits = _doc_analyze_file(src.tree)
        if hits:
            found[rel] = hits
    return found


class TestNoDocExtensionCopies:
    """No third or fourth copy of "is this file documentation?" (#1489).

    An architecture check rather than a behaviour one: it fails when a new
    hand-written doc-extensions set or constant appears anywhere outside
    :mod:`repowise.core.support_paths`.

    ``_KNOWN`` holds the call sites that answer a narrower question on purpose,
    each with its reason. The list only ever shrinks; adding a plain copy means this
    test tells you to use `repowise.core.support_paths.DOC_EXTENSIONS` instead.

    Ceiling: this matches on constant names and literal set/tuple/list shapes
    containing documentation file extension subsets (e.g. .md, .rst, .adoc, .mdx),
    so a dynamically computed collection or custom parser check that does not match
    these patterns stays invisible. It catches the shapes that actually recurred across
    categories, fix_shape, and knowledge_graph.
    """

    def test_no_new_doc_extension_copies(self) -> None:
        offenders = {p: hits for p, hits in _doc_offenders().items() if p not in _DOC_KNOWN}
        assert not offenders, (
            "New hand-written doc-extensions definition(s) outside support_paths.py:\n"
            + "\n".join(f"  {p}: {', '.join(hits)}" for p, hits in sorted(offenders.items()))
            + "\n\nImport DOC_EXTENSIONS from repowise.core.support_paths instead."
        )

    def test_known_copies_still_exist(self) -> None:
        """Every allowlist entry must still be a real copy, so the list cannot rot."""
        found = _doc_offenders()
        stale = sorted(p for p in _DOC_KNOWN if p not in found)
        assert not stale, (
            "These no longer define a doc-extension copy — remove them from _DOC_KNOWN:\n"
            + "\n".join(f"  {p}" for p in stale)
        )

    def test_shared_constant_exports_expected_extensions(self) -> None:
        """Verify canonical DOC_EXTENSIONS contains the full set including .mdx."""
        assert DOC_EXTENSIONS == _DOC_MARKERS

    def test_subsystem_constants_alias_canonical_constant(self) -> None:
        """Verify that categories, fix_shape, and knowledge_graph all alias the exact same constant."""
        assert _DOC_SUFFIXES is DOC_EXTENSIONS
        assert _DOC_EXT is DOC_EXTENSIONS
        assert KG_DOC_EXTENSIONS is DOC_EXTENSIONS


# ============================================================================
# Provider names: no module outside the registries branches on one.
# ============================================================================


# --- no provider-name branches outside the registries ----------------------

#: Every provider name but ``mock``, which is also the default embedder and the
#: ordinary test-double word that health and dead-code analysis match on.
_CHECKED = frozenset(PROVIDER_SPECS) - {"mock"}


_EMBEDDERS = frozenset(_BUILTIN_EMBEDDERS)


#: Path (relative to ``packages/``) -> names it may spell, and why.
_ALLOWED: dict[str, frozenset[str]] = {
    # ``opencode`` is a ``--target`` id as well as a provider.
    "cli/src/repowise/cli/agent_targets/registry.py": frozenset({"opencode"}),
    # Commit-trailer patterns naming the agent that wrote a commit.
    "core/src/repowise/core/ingestion/git_indexer/agent_provenance.py": frozenset(
        {"gemini", "opencode"}
    ),
    # Price keys are model families (``deepseek-v4``, ``kimi-k2``, ``gemini``),
    # and embedding rates are keyed by embedder.
    "core/src/repowise/core/cost_estimator/pricing.py": frozenset({"deepseek", "gemini", "kimi"})
    | _EMBEDDERS,
    # Third-party SDK names found in a scanned repo's imports.
    "core/src/repowise/core/ingestion/external_systems/classifier.py": frozenset(
        {"anthropic", "openai"}
    ),
    # SDK module names whose error base classes chat matches.
    "server/src/repowise/server/chat_errors.py": frozenset({"anthropic", "openai", "ollama"}),
    # Behaviour only the OpenAI adapter has: the custom-gateway row and base-url
    # prompt, and its reasoning budget. Ollama's readiness is a TCP probe.
    "cli/src/repowise/cli/_helpers/providers.py": frozenset({"openai"}),
    "cli/src/repowise/cli/ui/provider_selection.py": frozenset({"openai", "ollama"}),
    # Embedder surfaces: these literals name embedders, which share spellings.
    "core/src/repowise/core/providers/embedding/registry.py": _EMBEDDERS,
    "core/src/repowise/core/providers/embedding/resolve.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/init_cmd/command.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/reindex_cmd.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/serve_cmd/_embedder.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/update_cmd/deterministic.py": _EMBEDDERS,
    "cli/src/repowise/cli/providers/embedders.py": _EMBEDDERS,
    "cli/src/repowise/cli/ui/mode_selection.py": _EMBEDDERS,
    "server/src/repowise/server/mcp_server/_server.py": _EMBEDDERS,
}


def _is_allowed(path: Path, name: str) -> bool:
    rel = path.relative_to(PACKAGES).as_posix()
    # A provider's own module tags its errors and reports its own name.
    if rel.startswith("core/src/repowise/core/providers/llm/") and path.stem == name:
        return True
    return name in _ALLOWED.get(rel, frozenset())


def _name_literals(tree: ast.AST):
    """Provider-name literals used as a comparison operand, a ``case`` value, a
    collection element, a dict key or a ``.get()`` key. A bare argument
    (``get_provider("x")``) is a use, not a branch or a table, and is not
    flagged."""

    def names(node):
        if isinstance(node, ast.Constant) and node.value in _CHECKED:
            yield node
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for elt in node.elts:
                yield from names(elt)

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                yield from names(operand)
        elif isinstance(node, ast.MatchValue):
            yield from names(node.value)
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            yield from names(node)
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if key is not None:
                    yield from names(key)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            yield from names(node.args[0])


def _sources() -> list[SourceFile]:
    return sources_under(*PACKAGES.glob("*/src"))


class TestNoProviderNameBranches:

    def test_no_module_branches_on_a_provider_name(self):
        sources = _sources()
        unparsed = [src.rel for src in sources if src.tree is None]
        assert not unparsed, f"sources that do not parse: {unparsed}"
        offenders = sorted(
            {
                f"{src.path.relative_to(PACKAGES).as_posix()}:{node.lineno} {node.value!r}"
                for src in sources
                for node in _name_literals(src.tree)
                if not _is_allowed(src.path, node.value)
            }
        )
        assert not offenders, (
            "provider names spelled outside the registry; read a ProviderSpec field "
            f"instead: {offenders}"
        )

    def test_the_allowlist_has_no_stale_entries(self):
        """An entry that no longer matches anything would quietly widen later."""
        for rel, names in _ALLOWED.items():
            path = PACKAGES / rel
            assert path.is_file(), f"{rel} is gone"
            found = {node.value for node in _name_literals(ast.parse(path.read_text(encoding="utf-8")))}
            assert found & names, f"{rel} no longer spells any of {sorted(names)}"

    @pytest.mark.parametrize(
        "snippet",
        [
            'x == "codex_cli"',
            'x in ("claude_cli", "y")',
            '{"opencode": 1}',
            'd.get("litellm")',
            'match x:\n    case "kimi":\n        pass',
        ],
    )
    def test_the_guard_sees_each_branch_shape(self, snippet):
        assert list(_name_literals(ast.parse(snippet)))

    def test_the_guard_is_looking_at_real_sources(self):
        """A glob that matched nothing, or a name set that emptied, passes vacuously."""
        assert len(_sources()) > 500
        assert {"claude_cli", "codex_cli", "opencode", "litellm", "openai"} <= _CHECKED
