"""Extract Method returns every live-out write, or refuses the span.

A variable the span writes and the code after it reads has to come back as the
helper's return. Line liveness sees plain, conditional, ``x++`` and ``x += 1``
writes; it cannot place what a closure does, because a closure runs when it is
called, not where it is written. Two shapes leaked through before: a closure in
the span assigning an outer local (the helper's copy is written instead), and a
span writing a local that a closure written above it reads (hermes
``apps/desktop/src/lib/ansi.ts::parseAnsi``: ``pushText`` reads the ``bold`` /
``fg`` a span inside the loop sets, and the plan returned neither).

Each fixture marks the writes of the watched variable ``W``, the reads after
them ``R`` and, where one exists, the closure that reads it ``C``. A span that
covers a write, ends before a read and does not carry the reading closure along
must return the variable.
"""

from __future__ import annotations

import textwrap
from unittest import mock

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import slice as slicer
from repowise.core.analysis.health.dataflow.analyze import analyze_file


def _marked(src: str, mark: str) -> list[int]:
    return [i + 1 for i, line in enumerate(src.splitlines()) if line.rstrip().endswith(mark)]


def _spans(language: str, ext: str, src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file(f"m.{ext}", language, src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    # The fixtures are small, so most spans hold nearly the whole body; the
    # body-share worth gate would hide the soundness gate under test.
    with mock.patch.object(slicer, "_MAX_BODY_SHARE", float("inf")):
        return slicer.find_extractions(res.functions[0], get_language_map(language))


def _leaks(language: str, ext: str, var: str, src: str) -> tuple[list, list]:
    """(spans that drop the live-out *var*, spans that return it)."""
    src = textwrap.dedent(src)
    writes, reads, closures = _marked(src, "W"), _marked(src, "R"), _marked(src, "C")
    leaking, returning = [], []
    for x in _spans(language, ext, src):
        if not any(x.start_line <= w <= x.end_line for w in writes):
            continue
        if not any(r > x.end_line for r in reads):
            continue
        if any(x.start_line <= c <= x.end_line for c in closures):
            continue  # the reading closure moves with the write
        (returning if var in x.returns else leaking).append(x)
    return leaking, returning


# Plain writes line liveness already sees: conditional, increment, augmented.
_RETURNED = [
    (
        "python",
        "py",
        """
def f(a, b, c):
    x = 0
    y = a + b
    if y > c:
        x += 1  # W
    z = y * 2
    print(z)
    print(a)
    print(b)
    return x  # R
""",
    ),
    (
        "typescript",
        "ts",
        """
function f(a: number, b: number, c: number): number {
  let x = 0;
  const y = a + b;
  if (y > c) {
    x++; // W
  }
  const z = y * 2;
  log(z);
  log(a);
  log(b);
  return x; // R
}
""",
    ),
    (
        "go",
        "go",
        """
package m
func f(a int, b int, c int) int {
	x := 0
	y := a + b
	if y > c {
		x++ // W
	}
	z := y * 2
	log(z)
	log(a)
	log(b)
	return x // R
}
""",
    ),
    (
        "java",
        "java",
        """
class M {
  int f(int a, int b, int c) {
    int x = 0;
    int y = a + b;
    if (y > c) {
      x++; // W
    }
    int z = y * 2;
    log(z);
    log(a);
    log(b);
    return x; // R
  }
}
""",
    ),
    (
        "rust",
        "rs",
        """
fn f(a: i32, b: i32, c: i32) -> i32 {
    let mut x = 0;
    let y = a + b;
    if y > c {
        x += 1; // W
    }
    let z = y * 2;
    log(z);
    log(a);
    log(b);
    return x; // R
}
""",
    ),
    (
        "cpp",
        "cpp",
        """
int f(int a, int b, int c) {
  int x = 0;
  int y = a + b;
  if (y > c) {
    x++; // W
  }
  int z = y * 2;
  log(z);
  log(a);
  log(b);
  return x; // R
}
""",
    ),
]


@pytest.mark.parametrize(("language", "ext", "src"), _RETURNED, ids=[r[0] for r in _RETURNED])
def test_plain_live_out_write_is_returned(language: str, ext: str, src: str):
    leaking, returning = _leaks(language, ext, "x", src)
    assert leaking == []
    assert returning, "the span holding the write should still be offered, returning x"


# A closure in the span assigns an outer local that is read after the span.
_CLOSURE_WRITES = [
    (
        "typescript",
        "ts",
        """
function f(a: number, b: number, c: number): number {
  let x = 0;
  let y = a + b;
  if (y > c) {
    y = c;
  }
  const bump = () => { x = y; }; // W
  bump();
  log(y);
  log(a);
  return x; // R
}
""",
    ),
    (
        "go",
        "go",
        """
package m
func f(a int, b int, c int) int {
	x := 0
	y := a + b
	if y > c {
		y = c
	}
	bump := func() { x = y } // W
	bump()
	log(y)
	log(a)
	return x // R
}
""",
    ),
    (
        "rust",
        "rs",
        """
fn f(a: i32, b: i32, c: i32) -> i32 {
    let mut x = 0;
    let mut y = a + b;
    if y > c {
        y = c;
    }
    let mut bump = || { x = y; }; // W
    bump();
    log(y);
    log(a);
    return x; // R
}
""",
    ),
    (
        "cpp",
        "cpp",
        """
int f(int a, int b, int c) {
  int x = 0;
  int y = a + b;
  if (y > c) {
    y = c;
  }
  auto bump = [&]() { x = y; }; // W
  bump();
  log(y);
  log(a);
  return x; // R
}
""",
    ),
]


@pytest.mark.parametrize(
    ("language", "ext", "src"), _CLOSURE_WRITES, ids=[r[0] for r in _CLOSURE_WRITES]
)
def test_closure_write_in_span_is_refused(language: str, ext: str, src: str):
    # A span that declares ``x`` too keeps the closure's variable with it and
    # may return it; one that leaves the declaration behind must not be offered.
    leaking, returning = _leaks(language, ext, "x", src)
    assert leaking == []
    lines = textwrap.dedent(src).splitlines()
    declared = next(i for i, line in enumerate(lines, 1) if "x = 0" in line or "x := 0" in line)
    assert all(x.start_line <= declared for x in returning)


# The parseAnsi shape: a closure above the loop reads state a span sets.
_CLOSURE_READS = [
    (
        "python",
        "py",
        """
def f(codes, cleaned):
    out = []
    bold = False
    def push(t):  # C
        out.append((bold, t))
    for c in codes:
        push(c)
        if c == 0:
            bold = False  # W
        elif c == 1:
            bold = True  # W
        print(c)
        print(cleaned)
    push(cleaned)  # R
    return out
""",
    ),
    (
        "typescript",
        "ts",
        """
function parseAnsi(cleaned: string): string[] {
  const segments: string[] = [];
  let bold = false;
  const pushText = (t: string) => { // C
    segments.push(bold ? t : "");
  };
  let match: string[] | null;
  while ((match = next(cleaned)) !== null) {
    pushText(match[0]);
    if (match[2] === "m") {
      const code = Number(match[1]);
      if (code === 0) {
        bold = false; // W
      } else if (code === 1) {
        bold = true; // W
      }
      log(code);
    }
    log(match);
  }
  pushText(cleaned); // R
  return segments;
}
""",
    ),
    (
        # This repo's scala.py::expand_scala_import_clauses: ``_flush`` reads
        # (and through ``nonlocal`` resets) the ``trailer`` the loop sets.
        "python",
        "py",
        """
def f(children, src):
    clauses = []
    trailer = None
    def _flush():  # C
        nonlocal trailer
        if trailer is not None:
            clauses.append(trailer)
        trailer = None
    for child in children:
        if child.type == "identifier":
            trailer = child  # W
        elif child.type == ",":
            _flush()
        print(src)
        print(child)
    _flush()  # R
    return clauses
""",
    ),
    (
        "go",
        "go",
        """
package m
func f(codes []int) []string {
	out := []string{}
	bold := false
	push := func(t string) { // C
		if bold {
			out = append(out, t)
		}
	}
	for _, c := range codes {
		push("a")
		if c == 0 {
			bold = false // W
		} else if c == 1 {
			bold = true // W
		}
		log(c)
	}
	push("b") // R
	return out
}
""",
    ),
]


@pytest.mark.parametrize(
    ("language", "ext", "src"), _CLOSURE_READS, ids=["python", "typescript", "python-nonlocal", "go"]
)
def test_write_read_by_an_earlier_closure_is_refused(language: str, ext: str, src: str):
    leaking, _ = _leaks(language, ext, "trailer" if "trailer" in src else "bold", src)
    assert leaking == []


def test_closure_after_the_span_still_makes_a_return():
    """A closure written after the span reads the value the span left, so the
    ordinary OUT still covers it: line liveness counts its read where it sits."""
    src = """
function f(a: number, b: number, c: number): number[] {
  let x = 0;
  const y = a + b;
  if (y > c) {
    x = 1; // W
  } else {
    x = 2; // W
  }
  log(a);
  log(b);
  return [a, b].map(v => v + x); // R
}
"""
    leaking, returning = _leaks("typescript", "ts", "x", src)
    assert leaking == []
    assert returning
