"""The facts a file page opens with, reduced to what a sentence can carry.

A file page is rendered without a model, so its first paragraph is the one
place a reader, the wiki list and ``get_context`` meet the file in prose. That
paragraph leads with the author's own words when the file has a docstring and
otherwise says what the file defines and who imports it. The sentences live in
``file_page.j2`` so they stay in the label catalog; this module only counts,
picks and orders, which the template language does badly.

Every value is derived from the parse, the import graph or the knowledge graph,
and every ordering is fixed (by name, or by kind then declaration order), so the
same index renders the same bytes.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.test_paths import is_test_related_path

from .structural import api_symbols, as_markdown, code_span, oneline, signature

# Names quoted in a sentence before it switches to "and N more"; one more
# than this is named in full, since "and 1 more" is longer than the name.
_NAMED_IN_SENTENCE = 3
# A dependency list this short is named in full, not counted.
_LIST_IN_FULL = 3
# One line of a symbol's docstring beside its signature.
_SYMBOL_DOC_LIMIT = 140
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")
# The opening names types first, then callables, then values: a module's
# classes say what it is about, its type variables and constants rarely do.
_KIND_ORDER = {
    "class": 0,
    "interface": 0,
    "struct": 0,
    "trait": 0,
    "enum": 0,
    "type_alias": 0,
    "function": 1,
}
# Not definitions in their own right: a Rust ``impl`` block restates its
# type's name, and a Rust field belongs to a struct.
_NOT_DEFINITIONS = frozenset({"impl", "property"})


def first_sentence(text: object, limit: int = _SYMBOL_DOC_LIMIT) -> str:
    """The first sentence of a docstring's prose, flattened to one line."""
    prose = "\n".join(ln for ln in as_markdown(text).splitlines() if not ln.startswith("#"))
    paragraph = prose.strip().split("\n\n", 1)[0]
    flat = " ".join(paragraph.split())
    sentence = _SENTENCE_END_RE.split(flat, 1)[0]
    line = oneline(sentence, limit)
    # A cut through a code span would leave it open and swallow the rest of
    # the line into code.
    if line.count("`") % 2:
        cut = line.endswith("…")
        line = line.rstrip("…") + "`" + ("…" if cut else "")
    return line


def _largest_group(paths: Iterable[str]) -> tuple[str, int]:
    """The directory holding most of *paths* and how many, or ("", 0).

    Only a clear winner is named: at least two files and more than any other
    directory. A tie or a spread of singletons names nothing, not an arbitrary
    directory.
    """
    counts = Counter(p.rsplit("/", 1)[0] if "/" in p else "." for p in paths)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if not ranked or ranked[0][1] < 2:
        return "", 0
    if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
        return "", 0
    return ranked[0]


def _neighbours(paths: list[str]) -> dict[str, Any]:
    """Count, short list and largest directory of an import neighbourhood."""
    tests = {p for p in paths if is_test_related_path(p)}
    code = [p for p in paths if p not in tests]
    # Where the code that uses it lives says more than where its tests do.
    directory, share = _largest_group(code)
    return {
        "count": len(paths),
        "tests": len(tests),
        "listed": [code_span(p) for p in sorted(paths)] if len(paths) <= _LIST_IN_FULL else [],
        "directory": code_span(directory) if directory else "",
        "share": share,
        "all_in_one": bool(directory) and share == len(paths),
    }


def file_facts(ctx: Any) -> dict[str, Any]:
    """The values ``file_page.j2`` builds its opening sentences from."""
    api = api_symbols(ctx.symbols)
    # Only the file's own top-level definitions: a method of a class the API
    # does not list is not what the file defines.
    top_level = sorted(
        (s for s in api if not s.get("parent_name") and s.get("kind") not in _NOT_DEFINITIONS),
        key=lambda s: _KIND_ORDER.get(s.get("kind", ""), 2),
    )
    named = len(top_level) if len(top_level) <= _NAMED_IN_SENTENCE + 1 else _NAMED_IN_SENTENCE
    purpose = ""
    if ctx.docstring:
        purpose = as_markdown(ctx.docstring)
    elif ctx.kg_node_summary and (ctx.is_test or "barrel" in ctx.kg_tags):
        # The graph names what a test file tests and that a barrel re-exports;
        # for other code its one-line role only restates the symbols, which
        # the sentences below do better.
        purpose = oneline(ctx.kg_node_summary)
    return {
        "name": code_span(ctx.file_path.rsplit("/", 1)[-1]),
        "purpose": purpose,
        "defines": [code_span(s["name"]) for s in top_level[:named]],
        "defines_more": len(top_level) - len(top_level[:named]),
        "importers": _neighbours(list(ctx.dependents)),
        "imports": _neighbours(list(ctx.dependencies)),
    }


def _entry(declared: str, doc: str = "") -> dict[str, Any]:
    return {"declared": code_span(declared), "doc": doc, "members": []}


def symbol_entries(symbols: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The API list: one entry per symbol, members nested under their type.

    A method whose class the API does not list is nested under that class's
    bare name, so it neither reads as a module function nor leaves the page.
    An ``impl`` block folds into the type it implements and is listed once.
    """
    entries: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for sym in api_symbols(symbols):
        name = sym["name"]
        declared = signature(sym.get("signature") or "") or name
        if name not in declared:
            declared = f"{name}: {declared}"
        doc = first_sentence(sym.get("docstring")) if sym.get("docstring") else ""
        parent_name = sym.get("parent_name") or ""
        if parent_name:
            parent = by_name.get(parent_name)
            if parent is None:
                parent = by_name[parent_name] = _entry(parent_name)
                entries.append(parent)
            parent["members"].append(_entry(declared, doc))
            continue
        if sym.get("kind") == "impl" and name in by_name:
            continue
        if name in by_name and not by_name[name]["doc"] and by_name[name]["declared"] == code_span(name):
            # A stand-in made for members seen before their type: fill it in.
            by_name[name].update(declared=code_span(declared), doc=doc)
            continue
        by_name[name] = _entry(declared, doc)
        entries.append(by_name[name])
    return entries


def with_subject(sentences: Iterable[str], first: str, rest: str) -> list[str]:
    """Fill each sentence's ``{subject}`` slot: *first* once, *rest* after.

    ``str.replace``, not ``format``: the sentences already carry file paths
    and signatures, whose braces ``format`` would read as fields.
    """
    return [s.replace("{subject}", first if i == 0 else rest) for i, s in enumerate(sentences)]
