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

**Matching is anchored.** Directory rules compare whole words of a path
segment and filename rules compare stems, never substrings. An unanchored
``test[s_/]`` substring is what made ``src/latest/api.py`` and
``protest/main.py`` read as tests to community assignment. A segment's words are
what ``-``, ``_`` and ``.`` separate, so ``e2e-tests/`` and ``integration_test/``
are test trees while ``latest/`` and ``contest/`` stay single words that are not.

**Filename rules are source rules.** ``test_``, ``_test`` and ``.test.`` name a
test only on a source-language file: ``tsconfig.test.json`` and
``workflows/release.test.yml`` are configuration that happens to share the word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import PurePath, PurePosixPath

# Directory segments that mark every file beneath them as test material,
# whatever the filename. ``__test__`` is the Jest variant of ``__tests__``;
# ``integration_test`` is the directory Flutter's integration tests must live in.
_TEST_DIR_TOKENS: frozenset[str] = frozenset(
    {"test", "tests", "__tests__", "__test__", "e2e", "integration_test"}
)

# A compound segment (``e2e-tests``, ``pkg_tests``, ``client-e2e``) is a test
# tree when its last word, the head of the compound, is one of these. Only the
# head counts: ``test-api/`` (reference docs), ``test-tools/`` and
# ``e2e-project/`` (a generator) are *about* testing. Singular ``test`` is not a
# head word in any spelling, because a compound ending in it names a single
# thing - ``unit-test/`` an executor, ``component-test/`` a generator,
# ``assets_smoke_test/`` an example project, ``svg_test/`` a helper package -
# far more often than it names a suite.
_TEST_DIR_HEAD_WORDS: frozenset[str] = frozenset({"tests", "e2e"})

# Two-word heads that name a suite of tests outright: a Gradle module of shared
# test classes (``ktor-server-test-suites/``) or serde's ``test_suite/``.
# ``suite`` alone is not one (``office-suite/``).
_TEST_DIR_HEAD_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {("test", "suite"), ("test", "suites")}
)

# The same head written in PascalCase or camelCase, one word with no separator:
# ``UnitTests/``, ``UITests/``, ``FuzzTests/``, ``AdvancedPaste.UnitTests/``.
# Plural only, for the reason above (``HitTest/`` is a UI feature), and matched
# on the original case so ``contests/`` stays one lowercase word. Ceiling: a
# product folder that names a kind of test it runs for users (``LoadTests/``,
# ``ABTests/``, ``PenTests/``) reads as a test tree too; none seen so far.
_CAMEL_TESTS_HEAD_RE = re.compile(r"[A-Za-z0-9]Tests$")

# Gradle QA builds keep integration suites and the plugins they load in their
# own projects: ``qa/<project>/src/<source set>/<java|resources|...>``. The full
# JVM source-set shape is required, so a ``qa/`` package of tooling
# (``packages/qa/cli/src/main.ts``) stays production.
_TEST_PROJECT_CONTAINER = "qa"
_JVM_SOURCE_ROOTS: frozenset[str] = frozenset({"java", "kotlin", "groovy", "scala", "resources"})

# GitHub's repository-metadata directory; see ``_classify``.
_REPO_METADATA_DIR = ".github"

# The separators a directory segment splits into words on.
_SEGMENT_WORD_SEPARATORS = re.compile(r"[-_.]+")

# Tokens that also name non-test directories in the wild: "spec(s)" is as often
# OpenAPI/language specifications as it is RSpec. These count only when the
# filename corroborates, or when the file's own language declares the token
# (Ruby's spec/ needs no corroboration - a Ruby file under spec/ is RSpec
# material whatever its name).
_AMBIGUOUS_TEST_DIR_TOKENS: frozenset[str] = frozenset({"spec", "specs"})

# A whole module named for testing and nothing else: Django's per-app
# ``myapp/tests.py`` and Rust's ``#[cfg(test)] mod tests;`` in ``tests.rs``,
# which no prefix/suffix/infix rule catches. Those two languages only: elsewhere
# a bare ``test.ts`` or ``test.sh`` is as often an example or a script that
# exercises something by hand as it is a suite. Matched against
# the filename's own case, deliberately: a lowercase ``tests.py`` is the
# snake_case-module convention, while a capitalised ``Test.java`` is a class
# named Test and may be production code - which is why the registry's camel rule
# requires a lowercase boundary and excludes it. Ceiling: if a third language
# ever needs it, this moves onto the language specs like everything else here.
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
# would never fire on them. ``__snapshots__`` is where Jest and Vitest write the
# ``.snap`` output a snapshot test compares against; the ``.test.`` infix used to
# catch those files by accident, and stopped once filename rules became source
# rules.
#
# Support rather than test, deliberately: golden data is what a test reads, not
# a test. So the union counts it (#1103's reporter asked for exactly that) while
# search, which uses ``is_test_path``, still surfaces the golden file by name.
# Matched on the segment's words run together too, so ``test-data/`` and
# ``test_data/`` are the same golden data rather than a test tree.
_SUPPORT_DIR_TOKENS_ANYWHERE: frozenset[str] = frozenset(
    {"__fixtures__", "__snapshots__", "testdata"}
)


@dataclass(frozen=True, slots=True)
class _Conventions:
    """The registry-declared halves of the rules, resolved once."""

    stem_prefixes: tuple[str, ...]
    stem_suffixes: tuple[str, ...]
    infixes: tuple[str, ...]
    source_exts: frozenset[str]
    exact_stem_exts: frozenset[str]
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
        source_exts=REGISTRY.all_code_extensions(),
        exact_stem_exts=REGISTRY.extensions_for(("python", "rust")),
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


def _words(segment: str) -> list[str]:
    """A lowercased directory segment's words (``e2e-tests`` -> e2e, tests)."""
    return [word for word in _SEGMENT_WORD_SEPARATORS.split(segment) if word]


def _is_support_anywhere_dir(segment: str) -> bool:
    return (
        segment in _SUPPORT_DIR_TOKENS_ANYWHERE
        or "".join(_words(segment)) in _SUPPORT_DIR_TOKENS_ANYWHERE
    )


def _is_test_name(filename: str) -> bool:
    """Whether the filename alone marks a test (test_x.py, x_test.go, x.spec.ts)."""
    if not filename:
        return False
    rules = _conventions()
    lowered = filename.lower()
    ext = PurePosixPath(lowered).suffix
    if ext not in rules.source_exts:
        return False
    if ext in rules.exact_stem_exts and PurePosixPath(filename).stem in _TEST_EXACT_STEMS:
        return True
    stem = PurePosixPath(lowered).stem
    if (
        stem.startswith(rules.stem_prefixes)
        or stem.endswith(rules.stem_suffixes)
        or any(infix in lowered for infix in rules.infixes)
    ):
        return True
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
    return _has_test_segment(segments, original, filename, language) or _has_test_layout(
        segments, original
    )


def _has_test_segment(
    segments: list[str], original: list[str], filename: str, language: str | None
) -> bool:
    """Whether one directory's own name says it holds tests."""
    lang_tokens = _conventions().lang_dir_tokens.get((language or "").lower(), frozenset())
    # ``spec/`` needs corroboration from somewhere: the language declaring the
    # token, a test- or support-shaped filename, or a scaffolding dir beneath it
    # (``spec/support/helper.rb`` is RSpec whatever the filename says, and
    # path-only callers have no language to go on).
    corroborated = (
        _is_test_name(filename)
        or _is_support_name(filename)
        or any(seg in _SUPPORT_DIR_TOKENS for seg in segments)
    )
    return any(
        _is_test_segment(seg, orig, lang_tokens, corroborated)
        for seg, orig in zip(segments, original, strict=True)
    )


def _is_test_segment(seg: str, orig: str, lang_tokens: frozenset[str], corroborated: bool) -> bool:
    words = _words(seg)
    head = words[-1] if words else ""
    if seg in _TEST_DIR_TOKENS or head in _TEST_DIR_HEAD_WORDS:
        return True
    if tuple(words[-2:]) in _TEST_DIR_HEAD_PAIRS:
        return True
    if head in _AMBIGUOUS_TEST_DIR_TOKENS and (head in lang_tokens or corroborated):
        return True
    return _CAMEL_TESTS_HEAD_RE.search(orig) is not None


def _has_test_layout(segments: list[str], original: list[str]) -> bool:
    """Whether the directories form a known test layout (``src/test/java``)."""
    rules = _conventions()
    return (
        _in_test_project(segments)
        or any(_contains_run(segments, needle) for needle in rules.dir_paths)
        or any(
            _has_wildcard_pair(segments, original, prefix_seg, camel_suffix)
            for prefix_seg, camel_suffix in rules.dir_wildcards
        )
        or any(seg.endswith(rules.dir_suffixes) for seg in original)
    )


def _in_test_project(segments: list[str]) -> bool:
    return any(
        seg == _TEST_PROJECT_CONTAINER
        and segments[i + 2] == "src"
        and segments[i + 4] in _JVM_SOURCE_ROOTS
        for i, seg in enumerate(segments[:-4])
    )


def _contains_run(segments: list[str], needle: tuple[str, ...]) -> bool:
    span = len(needle)
    return any(tuple(segments[i : i + span]) == needle for i in range(len(segments) - span + 1))


def _has_wildcard_pair(
    segments: list[str], original: list[str], prefix_seg: str, camel_suffix: str
) -> bool:
    """``src/*Test``: a *prefix_seg* directly above a name ending in *camel_suffix*."""
    return any(
        segments[i] == prefix_seg
        and original[i + 1].endswith(camel_suffix)
        and len(original[i + 1]) > len(camel_suffix)
        for i in range(len(segments) - 1)
    )


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
    # question before the tree rules run, ``.github/`` included. A test-shaped
    # filename still wins, so ``testdata/build_test.go`` stays a test.
    if not named_test and any(_is_support_anywhere_dir(seg) for seg in segments):
        return "support"

    # ``.github/`` holds CI workflows, actions, issue templates and agent
    # instructions, so no directory there makes a test tree, however it is named
    # (``skills/unit-tests/SKILL.md``). Only a test-shaped source file counts:
    # the suite for a custom action's script is still a test.
    if _REPO_METADATA_DIR in segments:
        return "test" if named_test else ""

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


def is_unambiguous_test_path(path: str, language: str | None = None) -> bool:
    """Whether *path* is test material beyond a naming coincidence.

    For callers that *hide* something when the answer is yes, such as a
    contract break whose only callers are tests. A test-shaped filename alone
    does not qualify inside a ``src`` tree, where ``src/pkg/test_paths.py`` is a
    production module named for what it does. It needs a test directory
    (``tests/``, ``__tests__/``, ``src/test/java``) or to sit outside ``src``.
    Ambiguous paths read as production, so the doubt surfaces the finding.
    """
    if not is_test_related_path(path, language):
        return False
    original, lowered = _parts(path)
    if _is_test_dir(lowered[:-1], original[:-1], original[-1], language):
        return True
    return "src" not in lowered[:-1]


def is_test_to_production_pair(
    code_path: str, partner_path: str, *, code_language: str | None = None
) -> bool:
    """Whether exactly one of the two paths is test material.

    A test and the code it covers are supposed to change together, so a signal
    derived from history has nothing to say about such a pair. *partner_path*
    takes no language because callers reach this from a bare path recorded
    against another file, with no node to read a stored flag from.
    """
    return is_test_related_path(code_path, code_language) ^ is_test_related_path(partner_path)


_PASCAL_UNIT_SUFFIXES = frozenset({".pas", ".pp", ".dpr", ".dpk", ".lpr"})


def paired_test_names(rel_path: str) -> frozenset[str]:
    """Filenames a test for *rel_path* would conventionally carry, any directory."""
    p = PurePath(rel_path)
    stem = p.stem
    test_suffix = ".exs" if p.suffix == ".ex" else p.suffix
    names = {
        f"test_{stem}{test_suffix}",
        f"{stem}_test{test_suffix}",
        f"{stem}_spec{test_suffix}",
        f"{stem}.test.ts",
        f"{stem}.test.tsx",
        f"{stem}.test.js",
        f"{stem}.test.mts",
        f"{stem}.test.cts",
        f"{stem}.spec.ts",
        f"{stem}.spec.js",
        f"{stem}.spec.mts",
        f"{stem}.spec.cts",
    }
    if p.suffix.lower() in _PASCAL_UNIT_SUFFIXES:
        # Delphi pairs ``uFoo.pas`` with a ``TestFoo.dpr`` program; only a
        # lowercase ``u`` is the unit prefix (``Utils.pas`` keeps its U).
        names.add(f"Test{stem[1:] if stem[:1] == 'u' else stem}.dpr")
    return frozenset(names)
