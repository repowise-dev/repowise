"""Single home for "is this path test material?".

Fourteen implementations of this question used to live across ingestion,
analysis, generation and the server, and they disagreed on real layouts
(#1103). This module is the one answer. It sits beside :mod:`exclusion` for the
same reason that one does: the question is asked both during ingestion and at
query time, so it cannot live under ``ingestion/`` without the server importing
across a layer to reach it.

**The decision is made once, at ingestion.** :func:`is_test_related_path` is
what stamps ``FileInfo.is_test`` (``ingestion/traverser.py``), which is carried
onto graph nodes and persisted as ``GraphNode.is_test``. Any caller holding a
``FileInfo`` or a graph node should read that flag rather than call back in
here. The functions below are for callers that genuinely only have a path
string, chiefly the MCP tools ranking wiki-page rows: they exist so that the
fallback and the stored flag can never disagree, because they are the same code.

**Test versus test support.** ``conftest.py`` is not a test — pytest collects no
tests from it, it is a per-directory fixture plugin. But a refactoring detector
that skips tests wants to skip it too, while search should still surface it when
someone asks where the fixtures are. So the two are separate questions:
:func:`is_test_path` for tests, :func:`is_test_support_path` for the
infrastructure around them, and :func:`is_test_related_path` for the callers
that mean "either". Pick deliberately at the call site; do not reach for
``is_test_related_path`` by default.

**Conventions are data, not code.** Every pattern comes from the language
registry, where each language already declares its own conventions on its spec.
Adding an ecosystem is a row on a spec, not an edit here. That is also why
``.test.mts``/``.spec.cts`` need no mention anywhere: ``.test.`` and ``.spec.``
are registry *infixes*, so every extension that will ever exist is covered by
construction. The suffix-list copies that had to be edited per extension are
what let #288 regress twice.

**Matching is anchored.** Directory rules compare whole path segments and
filename rules compare stems, never substrings. An unanchored ``test[s_/]``
substring is what made ``src/latest/api.py`` and ``protest/main.py`` read as
tests to community assignment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath

# Directory segments that mark every file beneath them as test material,
# whatever the filename. ``__test__`` is the Jest variant of ``__tests__``.
_TEST_DIR_TOKENS: frozenset[str] = frozenset({"test", "tests", "__tests__", "__test__", "e2e"})

# Tokens that also name non-test directories in the wild: "spec(s)" is as often
# OpenAPI/language specifications as it is RSpec. These count only when the
# filename corroborates, or when the file's own language declares the token
# (Ruby's spec/ needs no corroboration - a Ruby file under spec/ is RSpec
# material whatever its name).
_AMBIGUOUS_TEST_DIR_TOKENS: frozenset[str] = frozenset({"spec", "specs"})

# A whole module named for testing and nothing else: Django's per-app
# ``myapp/tests.py``, which no prefix/suffix/infix rule catches. Matched against
# the filename's own case, deliberately: a lowercase ``tests.py`` is the
# snake_case-module convention, while a capitalised ``Test.java`` is a class
# named Test and may be production code - which is why the registry's camel rule
# requires a lowercase boundary and excludes it. Ceiling: if one language ever
# needs to disagree, this moves onto the language specs like everything else here.
_TEST_EXACT_STEMS: frozenset[str] = frozenset({"test", "tests"})

# Directories that hold the scaffolding rather than the tests. These only count
# inside a test tree: ``src/helpers/`` is production code, ``tests/helpers/`` is
# not. A test-shaped filename still wins over them, so
# ``tests/helpers/test_builders.py`` stays a test.
_SUPPORT_DIR_TOKENS: frozenset[str] = frozenset(
    {"fixtures", "factories", "support", "helpers", "mocks", "__mocks__"}
)

# Scaffolding directories whose names mean test material *wherever* they sit,
# because nothing else is ever called this. Bare ``fixtures`` deliberately stays
# out of this set and above: it is an ordinary English word that names real
# product directories (a sports app's fixtures list), and on this repo plus its
# two siblings 230 of 231 ``fixtures/`` files already sit inside a test tree, so
# the tree requirement costs nothing and the widening would buy nothing.
# ``__fixtures__`` is the same convention with the JS dunder wrapper that
# ``__tests__``/``__mocks__`` use, and carries no other meaning. ``testdata`` is
# the Go convention the toolchain itself reserves - ``go build`` ignores any
# directory of that name - which is why it needs no Go file to corroborate it:
# the golden files inside are JSON and YAML, and asking the file's own language
# would never fire on them.
#
# Support rather than test, deliberately: golden data is what a test reads, not
# a test. So the union counts it (#1103's reporter asked for exactly that) while
# search, which uses ``is_test_path``, still surfaces the golden file by name.
_SUPPORT_DIR_TOKENS_ANYWHERE: frozenset[str] = frozenset({"__fixtures__", "testdata"})


@dataclass(frozen=True, slots=True)
class _Conventions:
    """The registry-declared halves of the rules, resolved once."""

    stem_prefixes: tuple[str, ...]
    stem_suffixes: tuple[str, ...]
    infixes: tuple[str, ...]
    camel_res: dict[str, re.Pattern[str]]
    camel_prefix_res: dict[str, re.Pattern[str]]
    support_stems: frozenset[str]
    support_camel_res: dict[str, re.Pattern[str]]
    dir_paths: tuple[tuple[str, ...], ...]
    dir_wildcards: tuple[tuple[str, str], ...]
    dir_suffixes: tuple[str, ...]
    lang_dir_tokens: dict[str, frozenset[str]]


@cache
def _conventions() -> _Conventions:
    """Resolve the language registry on first use, not at import.

    The registry lives under ``ingestion/``, and importing anything from there
    runs ``ingestion/__init__``, which imports the traverser, which imports this
    module. Deferring the import breaks that cycle and costs one branch per
    call. ``spec_`` is carried over from the rule the traverser used to hold:
    the registry declares the ``_spec`` suffix and the ``spec_helper`` stem but
    no prefix, and dropping it would unclassify RSpec-style ``spec_foo.rb``.
    """
    from .ingestion.languages.registry import REGISTRY

    return _Conventions(
        stem_prefixes=tuple(sorted({*REGISTRY.test_stem_prefixes(), "spec_"})),
        stem_suffixes=REGISTRY.test_stem_suffixes(),
        infixes=REGISTRY.test_infixes(),
        camel_res=REGISTRY.camel_test_res_by_extension(),
        camel_prefix_res=REGISTRY.camel_test_prefix_res_by_extension(),
        support_stems=REGISTRY.test_fixture_stems(),
        support_camel_res=REGISTRY.camel_fixture_res_by_extension(),
        # Multi-segment test roots (src/test/java, src/it/scala) and the
        # ``*``-segment Gradle source-set form (src/*Test matches src/jvmTest).
        dir_paths=tuple(tuple(p.split("/")) for p in REGISTRY.test_dir_paths() if "*" not in p),
        dir_wildcards=tuple(
            (p.split("/")[0], p.split("/")[1].lstrip("*"))
            for p in REGISTRY.test_dir_paths()
            if "*" in p
        ),
        # Case-sensitive .NET sibling project dirs (Foo.Tests/, Foo.Specs/).
        dir_suffixes=REGISTRY.test_dir_suffixes(),
        lang_dir_tokens=REGISTRY.test_dir_tokens_by_language(),
    )


def _parts(path: str) -> tuple[list[str], list[str]]:
    """Original-case and lowercased path segments, filename last.

    Original case is preserved because two rules are deliberately
    case-sensitive: camel-boundary filenames (``FooTest.java``) and .NET
    project dirs (``Foo.Tests/``).
    """
    original = list(PurePosixPath(path.replace("\\", "/")).parts)
    return original, [seg.lower() for seg in original]


def _is_test_name(filename: str) -> bool:
    """Whether the filename alone marks a test (test_x.py, x_test.go, x.spec.ts)."""
    if not filename:
        return False
    rules = _conventions()
    if PurePosixPath(filename).stem in _TEST_EXACT_STEMS:
        return True
    lowered = filename.lower()
    stem = PurePosixPath(lowered).stem
    if (
        stem.startswith(rules.stem_prefixes)
        or stem.endswith(rules.stem_suffixes)
        or any(infix in lowered for infix in rules.infixes)
    ):
        return True
    ext = PurePosixPath(lowered).suffix
    original_stem = PurePosixPath(filename).stem
    camel_re = rules.camel_res.get(ext)
    if camel_re is not None and camel_re.search(original_stem) is not None:
        return True
    camel_prefix_re = rules.camel_prefix_res.get(ext)
    return camel_prefix_re is not None and camel_prefix_re.search(original_stem) is not None


def _is_support_name(filename: str) -> bool:
    """Whether the filename marks test support (conftest.py, FooFixtures.java)."""
    if not filename:
        return False
    rules = _conventions()
    lowered = filename.lower()
    if PurePosixPath(lowered).stem in rules.support_stems:
        return True
    camel_re = rules.support_camel_res.get(PurePosixPath(lowered).suffix)
    return camel_re is not None and camel_re.search(PurePosixPath(filename).stem) is not None


def _is_test_dir(
    segments: list[str], original: list[str], filename: str, language: str | None
) -> bool:
    """Whether any directory segment marks this path as sitting in a test tree."""
    rules = _conventions()
    lang_tokens = rules.lang_dir_tokens.get((language or "").lower(), frozenset())
    # ``spec/`` needs corroboration from somewhere: the language declaring the
    # token, a test- or support-shaped filename, or a scaffolding dir beneath it
    # (``spec/support/helper.rb`` is RSpec whatever the filename says, and
    # path-only callers have no language to go on).
    corroborated = (
        _is_test_name(filename)
        or _is_support_name(filename)
        or any(seg in _SUPPORT_DIR_TOKENS for seg in segments)
    )
    for seg in segments:
        if seg in _TEST_DIR_TOKENS:
            return True
        if seg in _AMBIGUOUS_TEST_DIR_TOKENS and (seg in lang_tokens or corroborated):
            return True

    for needle in rules.dir_paths:
        span = len(needle)
        if span <= len(segments) and any(
            tuple(segments[i : i + span]) == needle for i in range(len(segments) - span + 1)
        ):
            return True

    for prefix_seg, camel_suffix in rules.dir_wildcards:
        for i in range(len(segments) - 1):
            nxt = original[i + 1]
            if (
                segments[i] == prefix_seg
                and nxt.endswith(camel_suffix)
                and len(nxt) > len(camel_suffix)
            ):
                return True

    return any(seg.endswith(rules.dir_suffixes) for seg in original)


def _classify(path: str, language: str | None) -> str:
    """``"test"``, ``"support"``, or ``""`` for production code.

    One traversal, so the two public predicates cannot disagree with each other
    the way the copies they replace disagreed.
    """
    original, lowered = _parts(path)
    if not original:
        return ""
    filename = original[-1]
    segments, original_segments = lowered[:-1], original[:-1]

    # A name that says "support" settles it wherever the file sits: conftest.py
    # at the repo root is still a fixture plugin.
    if _is_support_name(filename):
        return "support"

    named_test = _is_test_name(filename)

    # A scaffolding directory that needs no test tree around it settles the
    # question before the tree rules run. A test-shaped filename still wins, so
    # ``testdata/build_test.go`` stays a test.
    if not named_test and any(seg in _SUPPORT_DIR_TOKENS_ANYWHERE for seg in segments):
        return "support"

    if not named_test and not _is_test_dir(segments, original_segments, filename, language):
        return ""

    # Inside test material. A test-shaped filename wins; otherwise a
    # scaffolding directory demotes it to support.
    if not named_test and any(seg in _SUPPORT_DIR_TOKENS for seg in segments):
        return "support"
    return "test"


def is_test_path(path: str, language: str | None = None) -> bool:
    """Whether *path* is a test.

    Test *support* (``conftest.py``, ``tests/factories/user.py``) is
    deliberately not a test here - see :func:`is_test_support_path`. Pass
    *language* when it is known: it decides the ambiguous ``spec/`` case, which
    is RSpec for Ruby and a specification folder for everything else.
    """
    return _classify(path, language) == "test"


def is_test_support_path(path: str, language: str | None = None) -> bool:
    """Whether *path* is test infrastructure rather than a test.

    ``conftest.py``, ``spec_helper.rb``, ``FooFixtures.java``, and the
    scaffolding directories inside a test tree (``tests/factories/user.py``).
    Never true at the same time as :func:`is_test_path`.
    """
    return _classify(path, language) == "support"


def is_test_related_path(path: str, language: str | None = None) -> bool:
    """Whether *path* is a test **or** test support.

    This is what stamps ``FileInfo.is_test`` at ingestion, and what callers
    should use when they mean "not production code" - a refactoring detector
    skipping files, a health biomarker exempting them. Callers that rank or
    search should prefer :func:`is_test_path`, so fixtures stay findable.
    """
    return _classify(path, language) != ""
