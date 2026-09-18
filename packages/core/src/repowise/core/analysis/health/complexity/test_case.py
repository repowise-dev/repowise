"""Is one walked function a test case its framework would run?

The walker has always known whether a *file* is a test. Nothing knew whether a
*function* was, because every marker that needed to ask could lean on something
else: ``mock_saturated_test`` skips a function with no assertions at all, and
calls that a fixture. ``assertion_free_test`` inverts exactly that predicate, so
it inherits none of that protection and needs the real question answered.

Each language answers it a different way, and a row states which way:

* a **name prefix** (Python's ``test_``, Go's ``TestXxx``),
* a **callback callee**, for a framework whose cases are anonymous functions
  passed to ``it(...)`` / ``test(...)``,
* an **annotation**, for JUnit, which puts the answer in ``@Test``.

A name-prefix or callback-callee language is one row and nothing else. An
annotation language is not: ``_annotation_names`` reads Java's ``modifiers``
subtree, so a language that spells annotations differently (C#'s
``[Fact]`` under ``attribute_list``) needs that function taught the shape as
well as a row.

Three deliberate exclusions, each of which would otherwise be a false positive
the marker could never defend:

* Go's ``BenchmarkXxx`` / ``FuzzXxx`` / ``ExampleXxx``. A benchmark has nothing
  to assert, and an ``Example``'s oracle is its ``// Output:`` comment.
* ``describe`` / ``beforeEach`` and their kin. A suite callback is already
  transparent to the walker (``ast_utils._is_test_suite_callback``); a setup
  callback is a real function entry, and it is a fixture, not a case.
* Anything with no row: a language absent from :data:`TEST_CASE_DIALECTS`
  classifies nothing, so it produces no finding rather than a guess.

The verdict depends on the function node and the language, never on the file
path — ``HealthWalkCache`` keys on language and bytes, so a path-derived field
would be served from a byte-identical file elsewhere in the tree. The test-file
gate stays in the biomarker, which sees ``ctx.file_path``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

#: Suffix ``ast_utils._find_function_entry_name`` gives a callback entry, as in
#: ``"it callback"``. The text before it is the callee, arguments included.
_CALLBACK_SUFFIX = " callback"

_JAVA_MODIFIERS = "modifiers"
_JAVA_ANNOTATION_KINDS = ("marker_annotation", "annotation")


@dataclass(frozen=True)
class TestCaseDialect:
    """How one language marks a function as a test case. Data only."""

    #: Name prefixes matched case-INSENSITIVELY, which is what pytest's default
    #: ``python_functions = test*`` does.
    name_prefixes: tuple[str, ...] = ()

    #: Name prefixes matched case-SENSITIVELY, where the character after the
    #: prefix must not be lowercase. This is ``go test``'s own rule for
    #: ``TestXxx``, and it is what separates the test ``TestParse`` from the
    #: helper ``testScanner`` sitting beside it in the same file.
    runner_prefixes: tuple[str, ...] = ()

    #: Lowercase callee names whose callback argument is a case. Matched against
    #: the first dotted segment, so ``it.each(...)`` and ``it.skip`` both count.
    callback_callees: frozenset[str] = frozenset()

    #: Lowercase annotation names, last dotted segment only, so a fully
    #: qualified ``@org.junit.jupiter.api.Test`` matches the same row.
    annotations: frozenset[str] = frozenset()

    #: Exact names a rule above would otherwise claim. Go's ``TestMain`` matches
    #: ``TestXxx`` but is the test binary's entry point, not a case.
    excluded_names: frozenset[str] = frozenset()


_PY = TestCaseDialect(name_prefixes=("test",))

# Go's convention is the test binary's own rule, so it is exact rather than a
# guess. ``Benchmark`` / ``Fuzz`` / ``Example`` are excluded above.
_GO = TestCaseDialect(runner_prefixes=("Test",), excluded_names=frozenset({"TestMain"}))

_JS_TS = TestCaseDialect(callback_callees=frozenset({"it", "test", "specify"}))

# JUnit 5 plus JUnit 4's ``@Test``, which shares the name. ``@Disabled`` is not
# excluded: a disabled test is still a test, and whether it checks anything is
# still worth knowing.
_JAVA = TestCaseDialect(
    annotations=frozenset(
        {"test", "parameterizedtest", "repeatedtest", "testfactory", "testtemplate"}
    )
)

#: Keyed by ``LanguageTag``, as the sibling lexicons are.
TEST_CASE_DIALECTS: dict[str, TestCaseDialect] = {
    "go": _GO,
    "java": _JAVA,
    "javascript": _JS_TS,
    "python": _PY,
    "typescript": _JS_TS,
    "tsx": _JS_TS,
}


def _annotation_names(fn_node: Node) -> set[str]:
    """Lowercase last segments of the annotations on *fn_node*."""
    modifiers = next((c for c in fn_node.children if c.type == _JAVA_MODIFIERS), None)
    if modifiers is None:
        return set()
    names: set[str] = set()
    for child in modifiers.children:
        if child.type not in _JAVA_ANNOTATION_KINDS:
            continue
        name = child.child_by_field_name("name")
        if name is None or name.text is None:
            continue
        names.add(name.text.decode("utf-8", errors="replace").rsplit(".", 1)[-1].lower())
    return names


def _matches_runner_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    """``TestXxx`` but not ``testScanner``: exact case, no lowercase after."""
    for prefix in prefixes:
        if name.startswith(prefix) and not name[len(prefix) : len(prefix) + 1].islower():
            return True
    return False


def is_test_case(fn_node: Node, name: str, language: str) -> bool:
    """True when *fn_node* is a test case the language's runner would collect."""
    dialect = TEST_CASE_DIALECTS.get(language)
    if dialect is None or name in dialect.excluded_names:
        return False
    lowered = name.lower()
    if lowered.endswith(_CALLBACK_SUFFIX):
        if not dialect.callback_callees:
            return False
        callee = lowered[: -len(_CALLBACK_SUFFIX)]
        # ``it.each([1,2])`` carries its arguments in the entry name; the first
        # dotted segment is the framework function in every form of it.
        return callee.split(".", 1)[0].split("(", 1)[0] in dialect.callback_callees
    if dialect.name_prefixes and lowered.startswith(dialect.name_prefixes):
        return True
    if _matches_runner_prefix(name, dialect.runner_prefixes):
        return True
    return bool(dialect.annotations) and bool(dialect.annotations & _annotation_names(fn_node))
