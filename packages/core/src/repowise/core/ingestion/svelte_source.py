"""Svelte single-file-component source preparation.

A ``.svelte`` file is three languages in one file: ``<script>`` blocks hold
TS/JS, the markup is Svelte-flavoured HTML, and ``<style>`` is CSS.
``tree-sitter-svelte`` parses the markup but hands each ``<script>`` body back
as a single opaque ``raw_text`` node — a ``.scm`` query run against the Svelte
grammar captures no symbol, no import and no call.

So the Svelte grammar is used here only to *locate* the JS-bearing regions:

* every ``<script>`` body,
* every markup ``{expression}`` (``{label(item)}``, ``on:click={inc}``), plus
  the ``{#if cond}`` and ``{@html expr}`` heads, which are also plain JS.

:func:`prepare_source` then blanks every byte *outside* those regions to a
space, preserving newlines. The result is valid TypeScript at **byte-identical
offsets**, so ``typescript.scm``, the TypeScript ``LanguageConfig`` and every
code-health walker read correct line numbers straight off the original file
with no offset bookkeeping and no second coordinate space.

Keeping the markup expressions matters for more than call edges: a handler
like ``function inc() {}`` that is only ever referenced from ``on:click={inc}``
would otherwise carry no graph edge at all and read as dead code. Svelte's
whole idiom is markup-driven usage, so dropping the markup would make the
dead-code pass wrong on nearly every component.

``{#each items as item, i}`` and ``{#await p then v}`` heads are deliberately
*not* kept: their bodies are Svelte block syntax, not JS expressions, and
feeding them to the TypeScript grammar would only mint parse errors.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple

import structlog

if TYPE_CHECKING:
    from tree_sitter import Language, Node

log = structlog.get_logger(__name__)

# ``svelte_raw_text`` parents whose body is a plain JS expression. ``each_start``
# and ``await_start`` are excluded — see the module docstring.
_JS_EXPRESSION_PARENTS = frozenset({"expression", "if_start", "key_start", "html_tag"})

# First characters that mark a raw text as Svelte block syntax rather than a JS
# expression. The grammar normally routes these to their own node types, but a
# stray ``{/each}`` in a malformed component falls back to ``expression``.
_BLOCK_SIGILS = ("/", "#", ":", "@")

# An expression whose body opens with ``{`` is an object literal
# (``use:action={{ a, b }}``). At statement position the TypeScript grammar
# reads that as a block, so these are dropped rather than mis-parsed —
# a deliberate ceiling: object literals in attributes carry identifier reads
# but essentially never call edges. Lifting it would need a real expression
# context, i.e. wrapping in parens, which no spare byte is available for.
_OBJECT_LITERAL_PREFIX = "{"

_TAG_PARENTS = frozenset({"start_tag", "self_closing_tag"})

# Svelte's own namespaced elements (<svelte:component>, <svelte:head>, …) are
# compiler directives, not user components — they must never mint a call edge.
_SVELTE_NAMESPACE = "svelte:"

_NEWLINE = 0x0A
_CARRIAGE_RETURN = 0x0D
_SPACE = 0x20
_SEMICOLON = 0x3B
_OPEN_BRACE = 0x7B
_CLOSE_BRACE = 0x7D


class SvelteScan(NamedTuple):
    """What the Svelte grammar found in one component file."""

    # (start_byte, end_byte) of each JS-bearing region, in source order.
    js_spans: tuple[tuple[int, int], ...]
    # Byte offsets that must become ``;``. Every kept markup expression is
    # fenced on both sides: the trailing ``;`` stops ``{a}{b}`` running
    # together, and the leading one stops an unterminated final script
    # statement from swallowing the expression via ASI — ``const o = { x: 1 }``
    # followed by ``{() => f()}`` otherwise parses as one call expression.
    terminators: tuple[int, ...]
    # (component_name, line) for every capitalized markup tag.
    component_tags: tuple[tuple[str, int], ...]
    # True when the Svelte grammar itself could not parse the file cleanly.
    has_error: bool


_EMPTY_SCAN = SvelteScan(js_spans=(), terminators=(), component_tags=(), has_error=False)


def _svelte_language() -> Language | None:
    """Load the tree-sitter Svelte grammar, or None when unavailable."""
    return _cached_svelte_language()


@lru_cache(maxsize=1)
def _cached_svelte_language() -> Language | None:
    try:
        import tree_sitter_svelte
        from tree_sitter import Language

        return Language(tree_sitter_svelte.language())
    except Exception as exc:  # pragma: no cover - depends on install shape
        log.debug("tree-sitter grammar unavailable", language="svelte", reason=str(exc))
        return None


def _walk(node: Node, scan_state: dict) -> None:
    """Collect script bodies, JS expressions and component tags in one pass."""
    node_type = node.type

    if node_type == "script_element":
        for child in node.children:
            if child.type == "raw_text":
                scan_state["spans"].append((child.start_byte, child.end_byte))
    elif node_type == "svelte_raw_text":
        parent = node.parent
        if parent is not None and parent.type in _JS_EXPRESSION_PARENTS:
            body = (node.text or b"").decode("utf-8", errors="replace").lstrip()
            if body and not body.startswith((*_BLOCK_SIGILS, _OBJECT_LITERAL_PREFIX)):
                scan_state["spans"].append((node.start_byte, node.end_byte))
                # The enclosing node's first and last bytes are its ``{`` and
                # ``}``. Rewriting both to ``;`` fences the expression into its
                # own statement without shifting a single offset.
                if parent.start_byte < node.start_byte:
                    scan_state["terminators"].append(parent.start_byte)
                if parent.end_byte - 1 >= node.end_byte:
                    scan_state["terminators"].append(parent.end_byte - 1)
    elif node_type in _TAG_PARENTS:
        for child in node.children:
            if child.type != "tag_name":
                continue
            name = child.text.decode("utf-8", errors="replace") if child.text else ""
            if name[:1].isupper() and not name.startswith(_SVELTE_NAMESPACE):
                scan_state["tags"].append((name, child.start_point[0] + 1))

    for child in node.children:
        _walk(child, scan_state)


def scan(source: bytes) -> SvelteScan:
    """Locate the JS regions and component tags of a ``.svelte`` source.

    Returns an empty scan when the Svelte grammar is unavailable, which
    degrades the file to zero symbols rather than to wrong ones.
    """
    return _cached_scan(source)


# parse_file asks for the prepared source and the component tags back to back,
# and the health walkers re-prepare the same bytes. A tiny cache keeps that to
# one tree-sitter parse per file without holding sources alive across a repo.
@lru_cache(maxsize=4)
def _cached_scan(source: bytes) -> SvelteScan:
    language = _svelte_language()
    if language is None:
        return _EMPTY_SCAN

    try:
        from tree_sitter import Parser

        tree = Parser(language).parse(source)
    except Exception as exc:
        log.debug("svelte_scan_failed", error=str(exc))
        return _EMPTY_SCAN

    scan_state: dict = {"spans": [], "terminators": [], "tags": []}
    _walk(tree.root_node, scan_state)
    return SvelteScan(
        js_spans=tuple(sorted(scan_state["spans"])),
        terminators=tuple(sorted(scan_state["terminators"])),
        component_tags=tuple(scan_state["tags"]),
        has_error=tree.root_node.has_error,
    )


def _blank(source: bytes, scan_result: SvelteScan) -> bytes:
    """Blank every non-JS byte, preserving newlines and byte offsets."""
    out = bytearray(len(source))
    for index, byte in enumerate(source):
        out[index] = byte if byte in (_NEWLINE, _CARRIAGE_RETURN) else _SPACE
    for start, end in scan_result.js_spans:
        out[start:end] = source[start:end]
    for offset in scan_result.terminators:
        # Only ever rewrite a brace the scan pointed at — never a byte that
        # turned out to be part of a kept expression.
        if source[offset] in (_OPEN_BRACE, _CLOSE_BRACE):
            out[offset] = _SEMICOLON
    return bytes(out)


def component_call_sites(source: bytes, symbols: list) -> list:
    """Turn every capitalized markup tag into a call site on that component.

    ``<Foo />`` in the markup is how Svelte instantiates ``Foo`` — the exact
    analogue of a JSX element, which ``tsx.scm`` already captures as a call.
    The blanked TypeScript buffer has no markup left, so these are minted here
    from the Svelte grammar instead.
    """
    from .models import CallSite
    from .parser_helpers import _find_enclosing_symbol

    tags = scan(source).component_tags
    if not tags:
        return []

    symbol_ranges = sorted(
        [(s.start_line, s.end_line, s.id) for s in symbols],
        key=lambda t: (t[0], -t[1]),
    )

    calls: list = []
    seen: set[tuple[int, str]] = set()
    for name, line in tags:
        if (line, name) in seen:
            continue
        seen.add((line, name))
        calls.append(
            CallSite(
                target_name=name,
                receiver_name=None,
                caller_symbol_id=_find_enclosing_symbol(line, symbol_ranges),
                line=line,
                argument_count=None,
            )
        )
    return calls


def prepare_source(language: str, source: bytes) -> bytes:
    """Return *source* as the bytes the tree-sitter pipeline should parse.

    A no-op for every language but ``svelte``. Call this at each point that
    hands raw file bytes to a tree-sitter ``Parser`` — the ingestion parser and
    the three code-health walkers — so all of them see the same TypeScript
    projection of a component at the same offsets.
    """
    if language != "svelte" or not source:
        return source
    return _blank(source, scan(source))
