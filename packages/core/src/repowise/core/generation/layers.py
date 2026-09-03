"""Architectural layer inference — the grouping spine for the wiki.

Two responsibilities, both pure and deterministic:

1. :func:`infer_layer` — assign every file to exactly one architectural
   layer from its path, using a directory→layer hint table. This is the
   *fallback* used when the knowledge graph has no layer for a file, so the
   wiki can guarantee that **every** ``file_page`` carries a
   ``metadata.layer_name``.

2. :func:`compute_layer_order` — order the layers top→bottom by inter-layer
   **dependency direction** (a layer that imports others sits above the layers
   it imports). This turns the Architecture section from a flat list into a
   hierarchy that teaches how the system is stacked. We reuse the import graph
   already built during ingestion rather than re-deriving fan-in/fan-out.

Neither function does any I/O or depends on graph libraries — they take plain
strings and edge tuples, which keeps them trivially unit-testable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

from repowise.core.ids import is_external
from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.support_paths import DOC_DIR_TOKENS, EXAMPLE_DIR_TOKENS
from repowise.core.test_paths import is_test_related_path

# ---------------------------------------------------------------------------
# Directory → layer hint table. Each canonical layer maps to the
# directory-name tokens that imply it. A
# file is assigned the layer of the first matching path segment, scanning
# from the deepest segment outward (the closest directory wins).
# ---------------------------------------------------------------------------

_LAYER_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("CLI", frozenset({"cli", "commands", "cmd", "cli_commands"})),
    ("API", frozenset({"routes", "api", "controllers", "endpoints", "handlers", "routers"})),
    ("Service", frozenset({"services", "core", "lib", "domain", "logic", "usecases"})),
    ("Data", frozenset({"models", "db", "data", "persistence", "repository", "repositories", "store", "stores", "entities"})),
    ("UI", frozenset({"components", "views", "pages", "ui", "layouts", "widgets", "screens"})),
    ("Middleware", frozenset({"middleware", "plugins", "interceptors", "guards"})),
    ("Utility", frozenset({"utils", "helpers", "common", "shared", "tools", "util"})),
    ("Config", frozenset({"config", "constants", "env", "settings", "conf"})),
    ("Types", frozenset({"types", "interfaces", "schemas", "contracts", "dtos", "typings"})),
)

# Layers that observe or support the runtime stack rather than participate in
# it. Tests import production code and are never imported back, so letting
# them compete on import direction would crown them the top "consumer" in
# every codebase that has tests. They are excluded from the dependency race
# and pinned after the runtime layers instead.
ADJACENT_LAYERS: frozenset[str] = frozenset({"Test"})

# Every test convention this module used to carry - registry-derived filename
# shapes, camel-boundary suffixes, multi-segment roots, the ambiguous `spec/`
# rule - now lives in ``core.test_paths``, which answers the same question for
# ingestion and for the MCP tools (#1103). The table above no longer carries a
# Test row: the check runs ahead of it, so the row was unreachable.


# Per-language layer hints (they fire only for files of the
# declaring language, never others'). Partitioned by hint shape at import time — exact
# lowercase tokens, multi-segment paths ("src/bin"), and case-sensitive
# dir-name suffixes (".Api", "-cli"). The generic table above wins at any
# given depth; a deeper segment beats a shallower one across both tables.
_LANG_TOKEN_HINTS: dict[str, dict[str, str]] = {}
_LANG_PATH_HINTS: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {}
_LANG_SUFFIX_HINTS: dict[str, tuple[tuple[str, str], ...]] = {}
_LANG_ROOT_HINTS: dict[str, dict[str, str]] = {}
for _tag, _hints in _LANG_REGISTRY.layer_dir_hints_by_language().items():
    _tokens: dict[str, str] = {}
    _paths: list[tuple[tuple[str, ...], str]] = []
    _suffixes: list[tuple[str, str]] = []
    _roots: dict[str, str] = {}
    for _key, _layer in _hints:
        if _key.startswith("/"):
            # Root-anchored token ("/include"): the convention is a
            # top-level dir — a vendored include/ buried deep in another
            # language's tree must not mint the layer.
            _roots[_key[1:]] = _layer
        elif "/" in _key:
            _paths.append((tuple(_key.split("/")), _layer))
        elif _key.startswith((".", "-")):
            _suffixes.append((_key, _layer))
        else:
            _tokens[_key] = _layer
    if _tokens:
        _LANG_TOKEN_HINTS[_tag] = _tokens
    if _paths:
        _LANG_PATH_HINTS[_tag] = tuple(_paths)
    if _suffixes:
        _LANG_SUFFIX_HINTS[_tag] = tuple(_suffixes)
    if _roots:
        _LANG_ROOT_HINTS[_tag] = _roots


# The directory vocabularies and `is_support_path` live in `core.support_paths`
# so `analysis` can ask the same question without importing `generation`. Only
# the layer-inference use of the token sets stays here.
_EXAMPLE_DIR_TOKENS = EXAMPLE_DIR_TOKENS
_DOC_DIR_TOKENS = DOC_DIR_TOKENS


# Build / CI / extension tooling directories: scripts, container definitions,
# and agent/editor plugin trees. Like the doc and example dirs above, these
# support the project without being part of its runtime architecture, so they
# must not swell the application catch-all or borrow a runtime category. A
# top-level ``plugins/`` tree holds editor/agent extension manifests far more
# often than request-pipeline middleware (genuine middleware lives under
# ``middleware/``), so it is routed here rather than to the Middleware layer.
_TOOLING_DIR_TOKENS = frozenset({"scripts", "script", "docker", "plugins"})

# The single non-architectural support bucket. Every doc/example/benchmark and
# build/CI/extension-tooling file lands here instead of the runtime layers, so
# the architectural layers describe the system and only the system.
DOCS_TOOLING_LAYER = "Docs & Tooling"

# Directory-name tokens that route a file to :data:`DOCS_TOOLING_LAYER`.
_NON_ARCH_DIR_TOKENS = _EXAMPLE_DIR_TOKENS | _DOC_DIR_TOKENS | _TOOLING_DIR_TOKENS

# Fallback layer for files whose path matches no hint (root scripts, etc.).
DEFAULT_LAYER = "Application"

# Canonical top→bottom dependency rank. Used to seed the ordering and to
# break ties when the import graph is too sparse to imply a direction. Lower
# index = closer to the top (consumers); higher = closer to the bottom
# (foundational): top imports middle imports bottom.
_CANONICAL_RANK: dict[str, int] = {
    "UI": 0,
    "CLI": 1,
    "API": 2,
    "Middleware": 3,
    "Service": 4,
    DEFAULT_LAYER: 5,
    "Data": 6,
    "Types": 7,
    "Config": 8,
    "Utility": 9,
    DOCS_TOOLING_LAYER: 10,
    "Test": 11,
}

# Layers pinned after the runtime stack when ordering. Tests (see
# ADJACENT_LAYERS) and the support bucket both sit outside the runtime
# dependency race: a script or doc importing core code says nothing about where
# core sits, and letting them compete would crown the tooling bucket the top
# "consumer". Distinct from ADJACENT_LAYERS, which also governs entry-point
# candidacy — a tooling file (scripts/validate.py) may still be a real entry
# point, so DOCS_TOOLING_LAYER is pinned for ordering only, never excluded from
# entry points.
_PINNED_AFTER_RUNTIME: frozenset[str] = ADJACENT_LAYERS | {DOCS_TOOLING_LAYER}


def layer_key(layer: str) -> str:
    """Normalise a layer to its stable slug, from either spelling.

    Callers hand us one of two things: a canonical heuristic name from
    :func:`infer_layer` ("UI", "Docs & Tooling") or a curated layer id
    ("layer:ui"). Both must rank identically, because the curated id *is*
    ``layer:`` plus the slug of the heuristic name. See ``kg_curation`` where the
    id is minted. Normalising at lookup lets ordering key on the stable id
    without a second rank table, and keeps older callers passing names working.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", layer.lower().strip()).strip("-")
    return slug.removeprefix("layer-") or "unknown"


_PINNED_KEYS: frozenset[str] = frozenset(layer_key(la) for la in _PINNED_AFTER_RUNTIME)
_CANONICAL_RANK_BY_KEY: dict[str, int] = {
    layer_key(name): rank for name, rank in _CANONICAL_RANK.items()
}


def _is_pinned(layer: str) -> bool:
    return layer_key(layer) in _PINNED_KEYS


def _canonical_rank(layer: str) -> int:
    return _CANONICAL_RANK_BY_KEY.get(layer_key(layer), len(_CANONICAL_RANK))


def infer_layer(path: str, language: str | None = None) -> str:
    """Return the architectural layer name for *path*.

    A test-shaped filename wins outright — Go and Jest colocate tests beside
    sources (``mux_test.go``, ``Button.test.tsx``), so without this check
    repos with no ``tests/`` dir get no Test layer at all. A test root
    anywhere on the path wins next: ``tests/models/x.py`` is a test fixture,
    not Data, so unambiguous test dirs (``tests``/``__tests__``/…) mark the
    file from any depth. Ambiguous test-dir tokens (``spec``/``specs``) count
    only when the filename itself looks like a test — a ``specs/`` directory
    full of ordinary modules is a specification folder, not a test suite.
    Otherwise scans path segments from the deepest directory outward and
    returns the first layer whose hint set contains a segment. When
    *language* is given, that language's registry-declared hints (Go
    ``internal/``, Rust ``src/bin/``, .NET ``Foo.Api/``…) are consulted at
    each depth after the generic table — they never fire for other
    languages' files. Falls back to :data:`DEFAULT_LAYER` when nothing
    matches.
    """
    original_parts = list(PurePosixPath(path).parts)
    parts = [s.lower() for s in original_parts]
    segments = parts[:-1]  # drop filename

    # Checked before the hint table below, and deliberately: that table scans
    # deepest-segment-outward, so ``tests/utils/foo.py`` would answer "Utility".
    # A test is a test wherever in the path the marker sits.
    if is_test_related_path(path, language):
        return "Test"

    # Repo-root dot-directories (.github, .agents, .claude, .vscode, …) hold
    # tooling, not architecture — their inner dir names (e.g. "plugins") must
    # not mint phantom runtime layers.
    if segments and segments[0].startswith("."):
        return "Config"

    # Documentation sites, sample harnesses, and build/CI/extension tooling are
    # support material, not the runtime architecture. Route the whole subtree
    # to a single explicit support bucket so docs/scripts/website/plugins never
    # swell the application catch-all or borrow a runtime category. Checked
    # before the hint scan: a deeper architectural token inside a tooling tree
    # (scripts/build/services/…) describes the tooling, not a runtime service.
    if any(seg in _NON_ARCH_DIR_TOKENS for seg in segments):
        return DOCS_TOOLING_LAYER

    lang = (language or "").lower()
    token_hints = _LANG_TOKEN_HINTS.get(lang)
    path_hints = _LANG_PATH_HINTS.get(lang)
    suffix_hints = _LANG_SUFFIX_HINTS.get(lang)
    root_hints = _LANG_ROOT_HINTS.get(lang)
    original_segments = original_parts[:-1]

    # Deepest directory first — the closest folder describes the file best.
    for i in range(len(segments) - 1, -1, -1):
        seg = segments[i]
        for layer_name, tokens in _LAYER_HINTS:
            if seg in tokens:
                return layer_name
        if token_hints and seg in token_hints:
            return token_hints[seg]
        if path_hints:
            for needle, layer_name in path_hints:
                span = len(needle)
                if span <= i + 1 and tuple(segments[i - span + 1 : i + 1]) == needle:
                    return layer_name
        if suffix_hints:
            orig = original_segments[i]
            for sfx, layer_name in suffix_hints:
                # Proper suffix only — a dir literally named ".Api" is not
                # the convention.
                if orig.endswith(sfx) and len(orig) > len(sfx):
                    return layer_name
        if i == 0 and root_hints and seg in root_hints:
            return root_hints[seg]
    return DEFAULT_LAYER


def layer_order_basis(
    file_layers: Mapping[str, str],
    import_edges: Iterable[tuple[str, str]],
) -> str:
    """Whether :func:`compute_layer_order`'s result is evidence or convention.

    Returns ``"imports"`` when at least one inter-layer runtime edge
    participated in the ordering race, ``"canonical"`` when the order is
    purely the conventional rank (edgeless/sparse graphs, single-layer
    repos). Consumers must not claim "X sits above Y" for a canonical
    order — no edge supports it.
    """
    for src, dst in import_edges:
        if is_external(src) or is_external(dst):
            continue
        ls = file_layers.get(src)
        ld = file_layers.get(dst)
        if not ls or not ld or ls == ld:
            continue
        if _is_pinned(ls) or _is_pinned(ld):
            continue
        return "imports"
    return "canonical"


def compute_layer_order(
    file_layers: Mapping[str, str],
    import_edges: Iterable[tuple[str, str]],
) -> list[str]:
    """Order the layers present in *file_layers* top→bottom by dependency direction.

    Parameters
    ----------
    file_layers:
        ``{file_path: layer_name}`` for every documented file.
    import_edges:
        ``(src, dst)`` pairs meaning *src imports dst* (file paths). External
        nodes (``external:*``) and intra-layer edges are ignored.

    A layer that does more importing than being-imported sits higher (it
    consumes the layers below it). We rank by ``in - out`` ascending: a layer
    imported by many but importing few is foundational (bottom); a layer that
    imports many but is imported by few is a consumer (top). Ties fall back to
    the canonical rank so the result is stable on graphs with no clear
    direction.

    Layers in :data:`_PINNED_AFTER_RUNTIME` (tests and the Docs & Tooling
    support bucket) sit outside the runtime stack: their edges are excluded
    from the race (a test or build script importing a service says nothing
    about where the service sits) and they are appended after the runtime
    layers in canonical-rank order.
    """
    layers = sorted(set(file_layers.values()))
    if len(layers) <= 1:
        return layers

    runtime = [layer for layer in layers if not _is_pinned(layer)]
    adjacent = [layer for layer in layers if _is_pinned(layer)]

    out_deg: dict[str, int] = defaultdict(int)  # edges leaving the layer
    in_deg: dict[str, int] = defaultdict(int)  # edges entering the layer
    for src, dst in import_edges:
        if is_external(src) or is_external(dst):
            continue
        ls = file_layers.get(src)
        ld = file_layers.get(dst)
        if not ls or not ld or ls == ld:
            continue
        if _is_pinned(ls) or _is_pinned(ld):
            continue
        out_deg[ls] += 1
        in_deg[ld] += 1

    def sort_key(layer: str) -> tuple[int, int]:
        # Net "imported-ness": more incoming than outgoing → foundational →
        # sorts later (bottom). Negate out so consumers float to the top.
        net = in_deg[layer] - out_deg[layer]
        return (net, _canonical_rank(layer))

    ordered = sorted(runtime, key=sort_key)
    ordered += sorted(adjacent, key=_canonical_rank)
    return ordered
