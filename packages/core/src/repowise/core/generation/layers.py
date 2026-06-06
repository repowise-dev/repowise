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

from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

# ---------------------------------------------------------------------------
# Directory → layer hint table. Each canonical layer maps to the
# directory-name tokens that imply it. A
# file is assigned the layer of the first matching path segment, scanning
# from the deepest segment outward (the closest directory wins).
# ---------------------------------------------------------------------------

_TEST_DIR_TOKENS = frozenset({"__tests__", "test", "tests", "spec", "specs", "e2e"})

_LAYER_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("CLI", frozenset({"cli", "commands", "cmd", "cli_commands"})),
    ("API", frozenset({"routes", "api", "controllers", "endpoints", "handlers", "routers"})),
    ("Service", frozenset({"services", "core", "lib", "domain", "logic", "usecases"})),
    ("Data", frozenset({"models", "db", "data", "persistence", "repository", "repositories", "store", "stores", "entities"})),
    ("UI", frozenset({"components", "views", "pages", "ui", "layouts", "widgets", "screens"})),
    ("Middleware", frozenset({"middleware", "plugins", "interceptors", "guards"})),
    ("Utility", frozenset({"utils", "helpers", "common", "shared", "tools", "util"})),
    ("Config", frozenset({"config", "constants", "env", "settings", "conf"})),
    ("Test", _TEST_DIR_TOKENS),
    ("Types", frozenset({"types", "interfaces", "schemas", "contracts", "dtos", "typings"})),
)

# Layers that observe or support the runtime stack rather than participate in
# it. Tests import production code and are never imported back, so letting
# them compete on import direction would crown them the top "consumer" in
# every codebase that has tests. They are excluded from the dependency race
# and pinned after the runtime layers instead.
ADJACENT_LAYERS: frozenset[str] = frozenset({"Test"})

# Test-dir tokens that also name non-test directories in the wild ("spec(s)" =
# specifications, OpenAPI specs, language specs, …). These assign the Test
# layer only when the *file* corroborates; otherwise the scan continues
# outward to the next matching segment.
_AMBIGUOUS_TEST_DIR_TOKENS: frozenset[str] = frozenset({"spec", "specs"})

# Filename shapes that mark a test on their own (pytest, Go, Jest/Vitest,
# RSpec/minitest fixtures, …). Derived from the language registry — each
# language declares its own conventions on its spec; the union applies
# globally here (parity with the historical hard-coded tuples is pinned by
# tests/unit/ingestion/test_language_capabilities.py).
_TEST_FILE_STEM_PREFIXES = _LANG_REGISTRY.test_stem_prefixes()
_TEST_FILE_STEM_SUFFIXES = _LANG_REGISTRY.test_stem_suffixes()
_TEST_FILE_INFIXES = _LANG_REGISTRY.test_infixes()
_TEST_FIXTURE_STEMS = _LANG_REGISTRY.test_fixture_stems()

# Case-sensitive camel-boundary suffix patterns (FooTest.java, BarSpec.scala),
# keyed by extension so each language's convention applies only to its own
# files (polyglot fairness). The lowercase-boundary lookbehind keeps
# `latest.java`, `contest.cs`, and bare `Test.java` out — conventions match
# with their own case sensitivity.
_TEST_CAMEL_RES = _LANG_REGISTRY.camel_test_res_by_extension()

# Multi-segment test roots (src/it/java) and case-sensitive test-project dir
# suffixes (.NET sibling Foo.Tests/ projects). Both are unambiguous — like
# tests/ and __tests__/, they mark any file beneath them.
# A ``*``-segment form ("src/*Test") matches a Gradle source-set directory:
# the literal segment(s) match lowercased, the ``*<Suffix>`` segment matches
# the original-case dir name by proper suffix (src/jvmTest, src/commonTest,
# src/integrationTest, …).
_TEST_DIR_PATHS: tuple[tuple[str, ...], ...] = tuple(
    tuple(p.split("/")) for p in _LANG_REGISTRY.test_dir_paths() if "*" not in p
)
_TEST_DIR_WILDCARDS: tuple[tuple[str, str], ...] = tuple(
    (p.split("/")[0], p.split("/")[1].lstrip("*"))
    for p in _LANG_REGISTRY.test_dir_paths()
    if "*" in p
)
_TEST_DIR_SUFFIXES = _LANG_REGISTRY.test_dir_suffixes()
# Per-language unambiguous test-dir tokens: ruby's spec/ needs no filename
# corroboration — a Ruby file under spec/ is RSpec material whatever its
# name (support helpers, vendored fixtures).
_LANG_TEST_DIR_TOKENS = _LANG_REGISTRY.test_dir_tokens_by_language()


def _is_test_file_name(filename: str) -> bool:
    """Whether *filename* alone marks a test (test_x.py, x_test.go, x.spec.ts, …)."""
    name = filename.lower()
    stem = PurePosixPath(name).stem
    if (
        stem in _TEST_FIXTURE_STEMS
        or stem.startswith(_TEST_FILE_STEM_PREFIXES)
        or stem.endswith(_TEST_FILE_STEM_SUFFIXES)
        or any(m in name for m in _TEST_FILE_INFIXES)
    ):
        return True
    camel_re = _TEST_CAMEL_RES.get(PurePosixPath(filename).suffix.lower())
    return camel_re is not None and camel_re.search(PurePosixPath(filename).stem) is not None


def _is_test_dir_path(segments: list[str], original_segments: list[str]) -> bool:
    """Whether the directory path itself is an unambiguous test root.

    *segments* are lowercased dir names, *original_segments* preserve case
    for the case-sensitive project-dir suffix rule (``Foo.Tests/``).
    """
    for needle in _TEST_DIR_PATHS:
        span = len(needle)
        if span <= len(segments) and any(
            tuple(segments[i : i + span]) == needle
            for i in range(len(segments) - span + 1)
        ):
            return True
    for prefix_seg, camel_sfx in _TEST_DIR_WILDCARDS:
        for i in range(len(segments) - 1):
            nxt = original_segments[i + 1]
            if (
                segments[i] == prefix_seg
                and nxt.endswith(camel_sfx)
                and len(nxt) > len(camel_sfx)
            ):
                return True
    return any(seg.endswith(_TEST_DIR_SUFFIXES) for seg in original_segments)


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


# Example/demo/benchmark directories: documentation-by-code and support
# harnesses, not the system itself. Their files carry entry-style names
# (main.go, index.js, decode.exs) by convention, so without demotion they
# flood entry points and the tour on any repo that ships samples (express,
# chi, …) or benchmarks (cargo's benches/, jason's bench/).
_EXAMPLE_DIR_TOKENS = frozenset(
    {
        "examples", "_examples", "example", "samples", "sample", "demo", "demos",
        "bench", "benches", "benchmarks",
    }
)


# Documentation directories: sphinx/docusaurus/vitepress sites and runnable
# doc snippets (libuv's docs/code/*/main.c, docfx template assets). Like the
# example dirs above, their files carry entry-style names by convention but
# document the system rather than being it.
_DOC_DIR_TOKENS = frozenset({"docs", "doc", "website"})


def is_support_path(path: str) -> bool:
    """Whether *path* is support material (examples/benchmarks/docs sites).

    Support files never seed or anchor a tour and never surface as entry
    points — a reader orienting in the repo must land in the system itself,
    not in its documentation or sample harnesses.
    """
    return any(
        s.lower() in _EXAMPLE_DIR_TOKENS or s.lower() in _DOC_DIR_TOKENS
        for s in PurePosixPath(path).parts[:-1]
    )

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
    "Test": 10,
}


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
    # Original case is preserved for the case-sensitive rules (camel-suffix
    # filenames, .NET ``Foo.Tests/`` project dirs).
    filename = original_parts[-1] if original_parts else ""
    segments = parts[:-1]  # drop filename

    if _is_test_file_name(filename):
        return "Test"

    lang_test_tokens = _LANG_TEST_DIR_TOKENS.get((language or "").lower(), frozenset())
    for seg in segments:
        if seg not in _TEST_DIR_TOKENS:
            continue
        if (
            seg in _AMBIGUOUS_TEST_DIR_TOKENS
            and seg not in lang_test_tokens
            and not _is_test_file_name(filename)
        ):
            continue  # "spec(s)/" without a test-shaped file: not a test root
        return "Test"

    if _is_test_dir_path(segments, original_parts[:-1]):
        return "Test"

    # Repo-root dot-directories (.github, .agents, .claude, .vscode, …) hold
    # tooling, not architecture — their inner dir names (e.g. "plugins") must
    # not mint phantom runtime layers.
    if segments and segments[0].startswith("."):
        return "Config"

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
            if layer_name == "Test":
                continue  # handled above
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
        if src.startswith("external:") or dst.startswith("external:"):
            continue
        ls = file_layers.get(src)
        ld = file_layers.get(dst)
        if not ls or not ld or ls == ld:
            continue
        if ls in ADJACENT_LAYERS or ld in ADJACENT_LAYERS:
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

    :data:`ADJACENT_LAYERS` (tests) sit outside the runtime stack: their edges
    are excluded from the race (a test importing a service says nothing about
    where the service sits) and they are appended after the runtime layers in
    canonical-rank order.
    """
    layers = sorted(set(file_layers.values()))
    if len(layers) <= 1:
        return layers

    runtime = [layer for layer in layers if layer not in ADJACENT_LAYERS]
    adjacent = [layer for layer in layers if layer in ADJACENT_LAYERS]

    out_deg: dict[str, int] = defaultdict(int)  # edges leaving the layer
    in_deg: dict[str, int] = defaultdict(int)  # edges entering the layer
    for src, dst in import_edges:
        if src.startswith("external:") or dst.startswith("external:"):
            continue
        ls = file_layers.get(src)
        ld = file_layers.get(dst)
        if not ls or not ld or ls == ld:
            continue
        if ls in ADJACENT_LAYERS or ld in ADJACENT_LAYERS:
            continue
        out_deg[ls] += 1
        in_deg[ld] += 1

    def sort_key(layer: str) -> tuple[int, int]:
        # Net "imported-ness": more incoming than outgoing → foundational →
        # sorts later (bottom). Negate out so consumers float to the top.
        net = in_deg[layer] - out_deg[layer]
        return (net, _CANONICAL_RANK.get(layer, len(_CANONICAL_RANK)))

    ordered = sorted(runtime, key=sort_key)
    ordered += sorted(adjacent, key=lambda la: _CANONICAL_RANK.get(la, len(_CANONICAL_RANK)))
    return ordered
