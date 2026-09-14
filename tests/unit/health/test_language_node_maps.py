"""Table-driven checks that a language node map says what the grammar means.

A ``LanguageNodeMap`` is a set of node-type strings, so nothing about it fails
loudly when it is wrong: naming a node type the grammar never emits is silently
inert, and naming one that means something else silently produces confident
wrong numbers. Importing cleanly proves neither. These tests run the real
walker over small snippets and assert the function names and cyclomatic
complexity that come back, which is the only thing that does.

Each case is (label, source, expected rows), so adding a language is a table
entry rather than a new test.

Grammars are not all installed everywhere, so every case guards with
``_require_language`` the way ``test_complexity_walker`` does.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.languages import get_language_map


def _require_language(language: str) -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


def _rows(language: str, source: str, suffix: str) -> dict[str, int]:
    """Return ``{function name: ccn}`` from a real walk of *source*."""
    fc = walk_file(f"sample.{suffix}", language, source.encode("utf-8"))
    return {f.name: f.ccn for f in fc.functions}


# --------------------------------------------------------------------------- #
# Elixir: deliberately absent
# --------------------------------------------------------------------------- #


def test_elixir_has_no_complexity_map() -> None:
    """The absence is load-bearing, so it is asserted rather than assumed.

    tree-sitter-elixir emits ``call`` for ``defmodule``, ``def``, ``defp`` and
    an ordinary call alike, told apart only by the target's identifier text,
    which a set of node types cannot express. A map keyed on ``call`` reports
    the module as the function and hides every real one.
    """
    assert get_language_map("elixir") is None


def test_elixir_reports_nothing_rather_than_a_wrong_function() -> None:
    """No rows is the honest degrade. One row named ``defmodule`` is not.

    No grammar guard here on purpose: ``walk_file`` returns on the missing-map
    check before it ever asks for a parser, so this holds either way.
    """
    source = "defmodule A do\n  def one(x), do: x\nend\n\ndefmodule B do\n  def two(y), do: y\nend\n"
    fc = walk_file("sample.ex", "elixir", source.encode("utf-8"))
    assert fc.functions == []


# --------------------------------------------------------------------------- #
# Objective-C
# --------------------------------------------------------------------------- #

_OBJC_CASES: list[tuple[str, str, dict[str, int]]] = [
    (
        "file-scope globals and prototypes are not functions",
        """
static int gCounter = 0;
static NSString *gName = nil;
int helper(int x);

@implementation Ledger
- (int)post:(int)amount { if (amount > 0) { return 1; } return 0; }
@end
""",
        {"post": 2},
    ),
    (
        "a catch clause adds a branch",
        """
@implementation L
- (void)run {
  @try { [self go]; }
  @catch (NSException *e) { NSLog(@"x"); }
}
@end
""",
        {"run": 2},
    ),
    (
        "fast enumeration counts as a loop",
        """
@implementation L
- (int)count:(NSArray *)keys {
  int n = 0;
  for (NSString *k in keys) { n++; }
  return n;
}
@end
""",
        {"count": 2},
    ),
    (
        "a plain C function definition is still a function",
        """
int helper(int x) {
  for (int i = 0; i < x; i++) { x += i; }
  return x;
}
""",
        {"helper": 2},
    ),
    (
        "a ternary inside a declaration is reached",
        """
@implementation L
- (int)pick:(int)n { int b = n > 0 ? 1 : 2; return b; }
@end
""",
        {"pick": 2},
    ),
    (
        "a block nested in a method rolls into that method",
        """
@implementation L
- (void)run { void (^b)(void) = ^{ if (1) { NSLog(@"x"); } }; b(); }
@end
""",
        {"run": 2},
    ),
    (
        "a flat switch counts once for the dispatch",
        """
@implementation L
- (int)pick:(int)n {
  switch (n) { case 1: return 1; case 2: return 2; default: return 0; }
}
@end
""",
        {"pick": 2},
    ),
    (
        "a switch whose arms carry control flow counts them",
        """
@implementation L
- (int)pick:(int)n {
  switch (n) {
    case 1: { if (n > 0) { return 1; } return 5; }
    case 2: { for (int i = 0; i < n; i++) { n += i; } return 2; }
    default: return 0;
  }
}
@end
""",
        {"pick": 6},
    ),
]


@pytest.mark.parametrize(
    ("source", "expected"),
    [(src, exp) for _, src, exp in _OBJC_CASES],
    ids=[label for label, _, _ in _OBJC_CASES],
)
def test_objective_c_complexity(source: str, expected: dict[str, int]) -> None:
    _require_language("objectivec")
    assert _rows("objectivec", source, "m") == expected


# --------------------------------------------------------------------------- #
# F#
# --------------------------------------------------------------------------- #

_FSHARP_CASES: list[tuple[str, str, dict[str, int]]] = [
    (
        "every function is named rather than anonymous",
        "let one x = x\nlet two y = y\n",
        {"one": 1, "two": 1},
    ),
    (
        "a branch and a loop each add one",
        'let f x =\n  let y = 1\n  if x > 0 then\n    for i in 1..x do\n      printfn "%d" i\n  0\n',
        {"f": 3},
    ),
    (
        "a flat match counts once for the dispatch, at any width",
        'let f x =\n  match x with\n  | 1 -> "a"\n  | 2 -> "b"\n  | _ -> "c"\n',
        {"f": 2},
    ),
    (
        "control flow inside a match arm counts on top of the dispatch",
        'let f x =\n  match x with\n  | 1 -> (if x > 5 then "a" else "b")\n'
        '  | _ -> (for i in 1..x do printfn "%d" i\n          "c")\n',
        {"f": 4},
    ),
    (
        "a with handler is a branch",
        "let f x =\n  try\n    x\n  with\n  | _ -> 0\n",
        {"f": 2},
    ),
]


@pytest.mark.parametrize(
    ("source", "expected"),
    [(src, exp) for _, src, exp in _FSHARP_CASES],
    ids=[label for label, _, _ in _FSHARP_CASES],
)
def test_fsharp_complexity(source: str, expected: dict[str, int]) -> None:
    _require_language("fsharp")
    assert _rows("fsharp", source, "fs") == expected


def test_fsharp_member_is_named_by_its_method_not_its_self_identifier() -> None:
    """``member this.M(x)`` is ``M``, not ``this`` and not ``<anonymous>``."""
    _require_language("fsharp")
    source = "type T() =\n  member this.M(x) =\n    let y = 1\n    if x > 0 then 1 else 2\n"
    assert set(_rows("fsharp", source, "fs")) == {"M"}


def test_fsharp_flat_match_agrees_with_rust() -> None:
    """The dispatch is worth one branch in both, so the languages agree.

    ``case_kinds`` names the ``rules`` wrapper rather than the ``rule`` arm. On
    the arm, a three-arm match would score 4 against Rust's 2.
    """
    _require_language("fsharp")
    _require_language("rust")
    fsharp = 'let f x =\n  match x with\n  | 1 -> "a"\n  | 2 -> "b"\n  | _ -> "c"\n'
    rust = 'fn f(x: i32) -> &str {\n    match x {\n        1 => "a",\n        2 => "b",\n        _ => "c",\n    }\n}\n'
    assert _rows("fsharp", fsharp, "fs")["f"] == _rows("rust", rust, "rs")["f"] == 2


def test_fsharp_multi_handler_with_counts_once_not_per_clause() -> None:
    """A documented departure, pinned so it is a decision rather than a surprise.

    CATCH is meant to add one per clause. ``rules`` is a single node however
    many handlers hang off it, and the alternative inflates every match.
    """
    _require_language("fsharp")
    source = (
        "let f x =\n  try\n    x\n  with\n"
        "  | :? System.IO.IOException -> 1\n"
        "  | :? System.OverflowException -> 2\n"
        "  | _ -> 0\n"
    )
    assert _rows("fsharp", source, "fs") == {"f": 2}


def test_fsharp_single_expression_body_is_a_known_undercount() -> None:
    """Pinning current behaviour so a traversal fix is a deliberate change.

    ``walk_file`` takes the ``body`` field and walks that node's children, so a
    body that IS the branch is never visited. Correct for block-bodied
    languages, wrong here. When the traversal root changes this should become
    2, and this test is the place that says so.
    """
    _require_language("fsharp")
    assert _rows("fsharp", "let f x =\n  if x > 0 then 1 else 2\n", "fs") == {"f": 1}
