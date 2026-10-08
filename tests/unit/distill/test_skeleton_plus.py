"""Plus mode: every line outside a function body kept, every body elided.

Sources are parsed at test time so the bounds are the ones the index would
persist. Each function/method body line is tagged ``BODY``; that tag is the
oracle, independent of the parser and of the skeleton.
"""

from __future__ import annotations

import re

import pytest

from repowise.core.distill.skeleton import SkeletonSymbol, build_skeleton
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

_MARKER_RE = re.compile(r"^\s*\.\.\. (\d+) lines \((\d+)-(\d+)\)$")

_SOURCES = {
    "a.py": ("python", '''"""Mod doc."""
import os

LIMIT = 3


@decorator
def top(a, b):
    x = 1  # BODY
    y = 2  # BODY
    return x + y  # BODY


class Box:
    """Box doc."""

    size: int = 0
    COLOR = "red"

    @property
    def area(
        self,
    ) -> int:
        v = self.size  # BODY
        w = v * v  # BODY
        return w  # BODY

    def inner(self):
        def helper():  # BODY
            return 1  # BODY
        return helper()  # BODY


# trailing comment
REGISTRY = {"a": top}
'''),
    "a.ts": ("typescript", '''import { x } from "./x";

export const LIMIT = 3;

export function top(a: number, b: number): number {
  const s = a + b; // BODY
  const t = s * 2; // BODY
  return t; // BODY
}

export class Box {
  size = 0;
  private color: string = "red";

  constructor(size: number) {
    this.size = size; // BODY
    this.color = "blue"; // BODY
    console.log(size); // BODY
  }

  area(): number {
    const v = this.size; // BODY
    const w = v * v; // BODY
    return w; // BODY
  }
}

export interface Shape {
  area(): number;
  name: string;
}

export const arrow = (a: number) => {
  const r = a; // BODY
  const q = r + 1; // BODY
  return q; // BODY
};
'''),
    "a.go": ("go", '''package main

import "fmt"

const Limit = 3

type Box struct {
	Size  int
	Color string
}

func (b *Box) Area() int {
	v := b.Size // BODY
	w := v * v // BODY
	return w // BODY
}

// Top adds.
func Top(a, b int) int {
	s := a + b // BODY
	fmt.Println(s) // BODY
	return s // BODY
}

var registry = map[string]int{"a": 1}
'''),
    "A.java": ("java", '''package demo;

import java.util.List;

public class A {
    private static final int LIMIT = 3;
    private int size;

    @Override
    public String toString() {
        String s = "x"; // BODY
        s = s + size; // BODY
        return s; // BODY
    }

    public A(int size) {
        this.size = size; // BODY
        int t = size * 2; // BODY
        System.out.println(t); // BODY
    }
}
'''),
    "a.rs": ("rust", '''use std::fmt;

const LIMIT: u32 = 3;

pub struct Box {
    pub size: u32,
    color: String,
}

impl Box {
    pub fn area(&self) -> u32 {
        let v = self.size; // BODY
        let w = v * v; // BODY
        w // BODY
    }
}

#[inline]
fn top(a: u32, b: u32) -> u32 {
    let s = a + b; // BODY
    let t = s * 2; // BODY
    t // BODY
}
'''),
}


def _runs(indices: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for i in indices:
        if runs and runs[-1][1] == i - 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


@pytest.mark.parametrize("path", sorted(_SOURCES))
def test_plus_keeps_everything_but_function_bodies(path: str) -> None:
    language, source = _SOURCES[path]
    parsed = ASTParser().parse_file(_make_file_info(path, language), source.encode())
    symbols = [
        SkeletonSymbol(name=s.name, kind=s.kind, start_line=s.start_line, end_line=s.end_line)
        for s in parsed.symbols
    ]
    result = build_skeleton(source, symbols, mode="plus")
    assert result.mode == "plus"

    lines = source.splitlines()
    kept: list[int] = []  # 1-indexed source lines present verbatim
    elided: list[tuple[int, int]] = []
    line_no = 1
    for out in result.text.splitlines():
        match = _MARKER_RE.match(out)
        if match:
            a, b = int(match.group(2)), int(match.group(3))
            assert a == line_no and int(match.group(1)) == b - a + 1
            elided.append((a, b))
            line_no = b + 1
            continue
        assert out == lines[line_no - 1]
        kept.append(line_no)
        line_no += 1
    assert line_no - 1 == len(lines)

    body = [i for i, ln in enumerate(lines, 1) if "BODY" in ln]
    assert kept == [i for i in range(1, len(lines) + 1) if i not in body]
    assert elided == _runs(body)
    for sym in parsed.symbols:
        if sym.kind in ("function", "method"):
            assert any(i in kept for i in range(sym.start_line, sym.start_line + 2)), sym.name


def test_an_unknown_mode_still_reports_signatures() -> None:
    source = "def f():\n    a = 1\n    b = 2\n    return a + b\n"
    sym = SkeletonSymbol(name="f", kind="function", start_line=1, end_line=4)
    assert build_skeleton(source, [sym], mode="bogus").mode == "signatures"
