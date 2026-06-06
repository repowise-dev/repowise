"""Regex import extraction for Clojure (.clj / .cljc / .cljs).

Captured forms — the require-ish blocks of the ``ns`` macro plus the
standalone function forms:

    (ns foo.bar
      (:require [baz.qux :as q]
                [other.ns :refer [x y]]
                plain.ns))
    (:use [foo.bar])
    (require '[foo.bar :as fb])

Each require spec contributes its leading namespace symbol; ``:as`` /
``:refer`` / ``:rename`` options and their arguments are skipped by
position (they follow the namespace inside the vector). ``(:import …)``
blocks are deliberately NOT captured — they name JVM classes, which the
lightweight tier has no mechanism to resolve. Legacy prefix lists
``(:require (foo bar baz))`` are also out of scope (recorded cut).
"""

from __future__ import annotations

import re

from ..models import Import

_BLOCK_HEAD_RE = re.compile(r"\((?::(require|use|require-macros)|(require|use))[\s'\[(]")

# A namespace symbol: lowercase-ish start, at least segments/dashes; bans
# keywords (leading :) by construction.
_NS_SYMBOL_RE = re.compile(r"[A-Za-z*+!?<>=][\w*+!?<>=.-]*")

_STRING_OR_COMMENT_RE = re.compile(r'"(?:\\.|[^"\\])*"|;[^\n]*')


def _block_span(text: str, open_paren: int) -> int:
    """Return the index just past the paren that closes ``text[open_paren]``."""
    depth = 0
    for i in range(open_paren, len(text)):
        ch = text[i]
        if ch == "(" or ch == "[":
            depth += 1
        elif ch == ")" or ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _spec_namespaces(block: str) -> list[str]:
    """Leading namespace symbol of every require spec inside *block*.

    A spec is either a bare symbol at block depth 1 or a vector/list whose
    first symbol is the namespace; symbols at deeper positions (``:refer``
    vectors) and option arguments are skipped.
    """
    names: list[str] = []
    depth = 0
    expect_ns = True  # next symbol at the current position starts a spec
    i = 0
    while i < len(block):
        ch = block[i]
        if ch in "([":
            depth += 1
            # depth 1 = the require block itself (bare specs follow), depth 2
            # = a spec vector (its head symbol is the namespace); anything
            # deeper is an option payload (:refer vectors etc.)
            expect_ns = depth <= 2
            i += 1
        elif ch in ")]":
            depth -= 1
            expect_ns = depth == 1
            i += 1
        elif ch == '"':
            m = _STRING_OR_COMMENT_RE.match(block, i)
            i = m.end() if m else i + 1
        elif ch == ";":
            nl = block.find("\n", i)
            i = len(block) if nl == -1 else nl + 1
        elif ch == ":":
            # keyword: consume it and stop expecting a namespace until the
            # next spec boundary (its argument is an alias/refer payload)
            m = _NS_SYMBOL_RE.match(block, i + 1)
            i = m.end() if m else i + 1
            expect_ns = False
        elif ch == "'":
            i += 1  # quote before a spec — transparent
        elif ch.isspace() or ch == ",":
            i += 1
        else:
            m = _NS_SYMBOL_RE.match(block, i)
            if not m:
                i += 1
                continue
            symbol = m.group(0)
            if expect_ns and depth >= 1:
                # bare specs sit at depth 1, vector specs put the ns at the
                # head of depth 2; either way this is the spec's first symbol
                names.append(symbol)
                expect_ns = depth == 1  # bare symbols may repeat at depth 1
            i = m.end()
    return names


def extract_clojure_imports(text: str) -> list[Import]:
    # Blank out strings and line comments first — a require form inside a
    # docstring or a commented-out require must not mint edges.
    text = _STRING_OR_COMMENT_RE.sub(
        lambda m: '""' if m.group(0).startswith('"') else "", text
    )
    imports: list[Import] = []
    seen: set[str] = set()
    for match in _BLOCK_HEAD_RE.finditer(text):
        open_paren = match.start()
        block = text[open_paren : _block_span(text, open_paren)]
        # strip the head token so it is not mistaken for a namespace
        head_end = match.end() - 1  # keep the char that closed the head match
        inner = block[head_end - open_paren :]
        for namespace in _spec_namespaces("(" + inner):
            if namespace in seen:
                continue
            seen.add(namespace)
            imports.append(
                Import(
                    raw_statement=f"(:require {namespace})",
                    module_path=namespace,
                    imported_names=[],
                    is_relative=False,
                    resolved_file=None,
                )
            )
    return imports
