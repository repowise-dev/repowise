"""Single-file-component source preparation.

A ``.svelte`` or ``.vue`` file is three languages in one file: ``<script>``
blocks hold TS/JS, the markup is a framework-flavoured HTML, and ``<style>`` is
CSS. The markup grammars parse the file but hand each ``<script>`` body back as
one opaque ``raw_text`` node — a ``.scm`` query run against them captures no
symbol, no import and no call.

So a markup grammar is used here only to *locate* the JS-bearing regions:

* every ``<script>`` body,
* every markup expression — Svelte's ``{expr}`` and ``on:click={inc}``, Vue's
  ``@click="inc"`` / ``:class="cls"`` / ``{{ interpolation }}``.

:func:`prepare_source` then blanks every byte *outside* those regions to a
space, preserving newlines. The result is valid TypeScript at **byte-identical
offsets**, so ``typescript.scm``, the TypeScript ``LanguageConfig`` and every
code-health walker read correct line numbers straight off the original file
with no offset bookkeeping and no second coordinate space.

Keeping the markup expressions matters for more than call edges: a handler like
``function inc() {}`` that is only ever referenced from ``on:click={inc}`` or
``@click="inc"`` would otherwise carry no graph edge at all and read as dead
code. Markup-driven usage is the whole idiom of both frameworks, so dropping
the markup would make the dead-code pass wrong on nearly every component.

Only the *region-location* step differs per language, so it lives behind
:data:`_LOCATORS`; the blanking, fencing, caching and offset invariants are
shared. Adding a markup language means adding a :class:`Locator`, not a second
copy of the walker.

Binding forms are deliberately skipped rather than kept: Svelte's
``{#each items as item}`` and ``{#await}`` heads, and Vue's ``v-for="item in
items"`` and ``v-slot``/``#default``. They *parse* as JS — ``item in items`` is
the ``in`` operator — but mean something else, and a parse that succeeds with
the wrong meaning is worse than a skip. The names they bind are declarations,
not reads, so keeping them would mint phantom identifier references.

:func:`prepare_source` is also where a language registers a byte-preserving
sanitizer for a grammar gap that has nothing to do with multi-language files —
Pascal's project-file ``uses X in 'path'`` clauses being the current example,
handled by ``prepare_pascal_source`` in ``parser_helpers.py``. Same contract as
a :class:`Locator`, no-op unless the language matches.
"""

from __future__ import annotations

from collections.abc import Callable
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
_BLOCK_SIGILS = (b"/", b"#", b":", b"@")

# An expression whose body opens with ``{`` is an object literal
# (``use:action={{ a, b }}``, Vue's ``#default="{ row }"``). At statement
# position the TypeScript grammar reads that as a block, so these are dropped
# rather than mis-parsed — a deliberate ceiling: object literals in attributes
# carry identifier reads but essentially never call edges. Lifting it would need
# a real expression context, i.e. wrapping in parens, which no spare byte is
# available for.
_OBJECT_LITERAL_PREFIX = b"{"

# Both markup grammars nest ``tag_name`` under these.
_TAG_PARENTS = frozenset({"start_tag", "self_closing_tag"})

# Svelte's own namespaced elements (<svelte:component>, <svelte:head>, …) are
# compiler directives, not user components — they must never mint a call edge.
_SVELTE_NAMESPACE = "svelte:"

# Vue's built-in components, in the PascalCase spelling every kebab tag is
# normalised to below. Compiler/runtime intrinsics and vue-router's globally
# registered pair — none of them resolve to a component in the repo.
_VUE_BUILTIN_TAGS = frozenset(
    {
        "Component",
        "Transition",
        "TransitionGroup",
        "KeepAlive",
        "Teleport",
        "Suspense",
        "Slot",
        "Template",
        "RouterView",
        "RouterLink",
    }
)

# Vue attributes whose value is a *binding* form, not an expression to read.
_VUE_BINDING_ATTR_PREFIXES = ("v-slot", "#")

_NEWLINE = 0x0A
_CARRIAGE_RETURN = 0x0D
_SPACE = 0x20
_SEMICOLON = 0x3B
_OPEN_BRACE = 0x7B
_CLOSE_BRACE = 0x7D
_DOUBLE_QUOTE = 0x22
_SINGLE_QUOTE = 0x27

# Only a byte the scan pointed at *and* that is a real delimiter is ever
# rewritten to ``;`` — never a byte that turned out to be part of a kept
# expression. Svelte fences with braces, Vue with attribute quotes.
_FENCE_BYTES = frozenset({_OPEN_BRACE, _CLOSE_BRACE, _DOUBLE_QUOTE, _SINGLE_QUOTE})


class SfcScan(NamedTuple):
    """What a markup grammar found in one component file."""

    # (start_byte, end_byte) of each JS-bearing region, in source order.
    js_spans: tuple[tuple[int, int], ...]
    # Byte offsets that must become ``;``. Every kept markup expression is
    # fenced on both sides: the trailing ``;`` stops ``{a}{b}`` running
    # together, and the leading one stops an unterminated final script
    # statement from swallowing the expression via ASI — ``const o = { x: 1 }``
    # followed by ``{() => f()}`` otherwise parses as one call expression.
    terminators: tuple[int, ...]
    # (component_name, line) for every markup tag naming a component.
    component_tags: tuple[tuple[str, int], ...]
    # True when the markup grammar itself could not parse the file cleanly.
    has_error: bool


_EMPTY_SCAN = SfcScan(js_spans=(), terminators=(), component_tags=(), has_error=False)


class Locator(NamedTuple):
    """How one markup language exposes its JS regions and component tags."""

    # Importable module exposing a ``language()`` capsule.
    grammar_module: str
    # Called for every node; appends to ``state`` (spans/terminators/tags).
    visit: Callable[[Node, dict], None]
    # Maps a raw markup tag name to the component name it instantiates, or
    # None when the tag is not a user component.
    component_name: Callable[[str], str | None]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _should_keep(body: bytes) -> bool:
    """True when a located markup expression is safe to project as JS."""
    stripped = body.lstrip()
    return bool(stripped) and not stripped.startswith((*_BLOCK_SIGILS, _OBJECT_LITERAL_PREFIX))


def _keep(state: dict, start: int, end: int, open_fence: int, close_fence: int) -> None:
    """Record a kept span plus the two delimiter bytes that fence it."""
    state["spans"].append((start, end))
    if open_fence < start:
        state["terminators"].append(open_fence)
    if close_fence >= end:
        state["terminators"].append(close_fence)


def _decode(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _record_script_bodies(node: Node, state: dict) -> None:
    """``<script>`` bodies reach both grammars as a single ``raw_text`` child."""
    for child in node.children:
        if child.type == "raw_text":
            state["spans"].append((child.start_byte, child.end_byte))


# ---------------------------------------------------------------------------
# Svelte
# ---------------------------------------------------------------------------


def _svelte_visit(node: Node, state: dict) -> None:
    node_type = node.type

    if node_type == "script_element":
        _record_script_bodies(node, state)
    elif node_type == "svelte_raw_text":
        parent = node.parent
        if (
            parent is not None
            and parent.type in _JS_EXPRESSION_PARENTS
            and _should_keep(node.text or b"")
        ):
            # The enclosing node's first and last bytes are its ``{`` and
            # ``}``. Rewriting both to ``;`` fences the expression into its
            # own statement without shifting a single offset.
            _keep(
                state,
                node.start_byte,
                node.end_byte,
                parent.start_byte,
                parent.end_byte - 1,
            )
    elif node_type in _TAG_PARENTS:
        _record_tags(node, state, _svelte_component_name)


def _svelte_component_name(name: str) -> str | None:
    if not name[:1].isupper() or name.startswith(_SVELTE_NAMESPACE):
        return None
    return name


# ---------------------------------------------------------------------------
# Vue
# ---------------------------------------------------------------------------


def _vue_visit(node: Node, state: dict) -> None:
    node_type = node.type

    if node_type == "script_element":
        _record_script_bodies(node, state)
    elif node_type == "attribute":
        _vue_attribute(node, state)
    elif node_type == "text":
        _vue_interpolations(node, state)
    elif node_type in _TAG_PARENTS:
        _record_tags(node, state, _vue_component_name)


def _is_vue_expression_attr(name: str) -> bool:
    """True for directive attributes whose value is a JS expression.

    Plain HTML attributes are excluded: ``class="btn primary"`` is a literal
    string, and projecting it would produce two juxtaposed identifiers — a
    guaranteed parse error on nearly every element in the corpus.
    """
    if name.startswith(_VUE_BINDING_ATTR_PREFIXES) or name == "v-for":
        return False
    return name.startswith((":", "@", "v-"))


def _vue_attribute(node: Node, state: dict) -> None:
    """``@click="inc(1)"`` — the span sits inside ``quoted_attribute_value``.

    The surrounding quote bytes are the two fence bytes, exactly as Svelte uses
    its surrounding braces.
    """
    name_node = None
    quoted = None
    for child in node.children:
        if child.type == "attribute_name":
            name_node = child
        elif child.type == "quoted_attribute_value":
            quoted = child

    if name_node is None or quoted is None:
        return
    if not _is_vue_expression_attr(_decode(name_node)):
        return

    inner = next((c for c in quoted.children if c.type == "attribute_value"), None)
    if inner is None or not _should_keep(inner.text or b""):
        return

    _keep(state, inner.start_byte, inner.end_byte, quoted.start_byte, quoted.end_byte - 1)


def _vue_interpolations(node: Node, state: dict) -> None:
    """``{{ expr }}`` sits inside a ``text`` node, not its own grammar node.

    tree-sitter-html has no interpolation rule, so the ``{{``/``}}`` pair is
    found by scanning within an already-located node. The scan works in bytes
    so the offsets stay byte-exact for non-ASCII markup.
    """
    raw = node.text or b""
    base = node.start_byte
    pos = 0
    while True:
        open_at = raw.find(b"{{", pos)
        if open_at < 0:
            return
        close_at = raw.find(b"}}", open_at + 2)
        if close_at < 0:
            return
        inner_start, inner_end = open_at + 2, close_at
        if _should_keep(raw[inner_start:inner_end]):
            # Fence with the inner brace of each pair: the second ``{`` and the
            # first ``}``. The outer two blank to spaces like any other markup.
            _keep(
                state,
                base + inner_start,
                base + inner_end,
                base + open_at + 1,
                base + close_at,
            )
        pos = close_at + 2


def _de_kebab(name: str) -> str:
    if "-" not in name:
        return name
    return "".join(part[:1].upper() + part[1:] for part in name.split("-") if part)


def _vue_component_name(name: str) -> str | None:
    """``<my-widget />`` and ``<MyWidget />`` name the same component.

    Vue resolves a kebab-case tag to its PascalCase component, so the tag is
    normalised before the built-in filter runs — that way ``<keep-alive>`` and
    ``<KeepAlive>`` are both recognised as intrinsics. A tag containing ``-``
    is always a custom element; no native HTML element has one. A bare
    lowercase tag (``<div>``) is a native element and is deliberately *not*
    capitalised into existence here.
    """
    name = _de_kebab(name)
    if not name[:1].isupper() or name in _VUE_BUILTIN_TAGS:
        return None
    return name


def vue_component_name_from_stem(stem: str, parent_dir: str = "") -> str:
    """The component name a ``.vue`` filename declares.

    Unlike a markup tag, a filename is *always* a component, so this
    capitalises unconditionally: ``warningBar.vue`` declares ``WarningBar``,
    which is what a parent writes both in its import and in its markup.

    ``components/Logo/index.vue`` is the ``Logo`` component — the directory
    index convention — so a bare ``index`` stem defers to its directory.

    Sharing this with :func:`_vue_component_name` is the point: a tag and the
    file it resolves to are normalised by the same rule, so they cannot
    disagree. Measured on the validation corpus, this lifts filename/import
    agreement from 58.4% to 82.9% of ``.vue`` default imports; the remainder
    are genuine renames that no naming rule can recover.
    """
    if stem.lower() == "index" and parent_dir:
        stem = parent_dir
    stem = _de_kebab(stem)
    return stem[:1].upper() + stem[1:]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _record_tags(node: Node, state: dict, name_of: Callable[[str], str | None]) -> None:
    for child in node.children:
        if child.type != "tag_name":
            continue
        component = name_of(_decode(child))
        if component:
            state["tags"].append((component, child.start_point[0] + 1))


_LOCATORS: dict[str, Locator] = {
    "svelte": Locator(
        grammar_module="tree_sitter_svelte",
        visit=_svelte_visit,
        component_name=_svelte_component_name,
    ),
    # tree-sitter-html parses a Vue SFC cleanly: <template>, <script> and
    # <style> are just elements to it. There is no tree-sitter-vue on PyPI, and
    # this grammar covers plain HTML too.
    "vue": Locator(
        grammar_module="tree_sitter_html",
        visit=_vue_visit,
        component_name=_vue_component_name,
    ),
}


@lru_cache(maxsize=len(_LOCATORS))
def _grammar(module_name: str) -> Language | None:
    """Load a tree-sitter markup grammar, or None when unavailable."""
    try:
        import importlib

        from tree_sitter import Language

        return Language(importlib.import_module(module_name).language())
    except Exception as exc:  # pragma: no cover - depends on install shape
        log.debug("tree-sitter grammar unavailable", module=module_name, reason=str(exc))
        return None


def _walk(node: Node, visit: Callable[[Node, dict], None], state: dict) -> None:
    visit(node, state)
    for child in node.children:
        _walk(child, visit, state)


def scan(language: str, source: bytes) -> SfcScan:
    """Locate the JS regions and component tags of one SFC source.

    Returns an empty scan for a non-SFC language or when the markup grammar is
    unavailable, which degrades the file to zero symbols rather than to wrong
    ones.
    """
    if language not in _LOCATORS:
        return _EMPTY_SCAN
    return _cached_scan(language, source)


# parse_file asks for the prepared source and the component tags back to back,
# and the health walkers re-prepare the same bytes. A tiny cache keeps that to
# one tree-sitter parse per file without holding sources alive across a repo.
@lru_cache(maxsize=4)
def _cached_scan(language: str, source: bytes) -> SfcScan:
    locator = _LOCATORS[language]
    grammar = _grammar(locator.grammar_module)
    if grammar is None:
        return _EMPTY_SCAN

    try:
        from tree_sitter import Parser

        tree = Parser(grammar).parse(source)
    except Exception as exc:
        log.debug("sfc_scan_failed", language=language, error=str(exc))
        return _EMPTY_SCAN

    state: dict = {"spans": [], "terminators": [], "tags": []}
    _walk(tree.root_node, locator.visit, state)
    return SfcScan(
        js_spans=tuple(sorted(state["spans"])),
        terminators=tuple(sorted(state["terminators"])),
        component_tags=tuple(state["tags"]),
        has_error=tree.root_node.has_error,
    )


def _blank(source: bytes, scan_result: SfcScan) -> bytes:
    """Blank every non-JS byte, preserving newlines and byte offsets."""
    out = bytearray(len(source))
    for index, byte in enumerate(source):
        out[index] = byte if byte in (_NEWLINE, _CARRIAGE_RETURN) else _SPACE
    for start, end in scan_result.js_spans:
        out[start:end] = source[start:end]
    for offset in scan_result.terminators:
        if source[offset] in _FENCE_BYTES:
            out[offset] = _SEMICOLON
    return bytes(out)


def component_call_sites(language: str, source: bytes, symbols: list) -> list:
    """Turn every component tag in the markup into a call site.

    ``<Foo />`` is how both frameworks instantiate ``Foo`` — the exact analogue
    of a JSX element, which ``tsx.scm`` already captures as a call. The blanked
    TypeScript buffer has no markup left, so these are minted here from the
    markup grammar instead. Returns ``[]`` for every non-SFC language.
    """
    from .models import CallSite
    from .parser_helpers import _find_enclosing_symbol

    tags = scan(language, source).component_tags
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


def prepare_source(language: str, source: bytes, *, path: str | None = None) -> bytes:
    """Return *source* as the bytes the tree-sitter pipeline should parse.

    A no-op for every language without a registered locator or sanitizer.
    Call this at each point that hands raw file bytes to a tree-sitter
    ``Parser`` — the ingestion parser and the three code-health walkers —
    so all of them see the same projection at the same offsets.

    ``path`` only matters for a sanitizer that needs it (currently just
    Pascal, which gates its project-file ``uses X in 'path'`` sanitizer on
    the extension): every call site already has it in scope from its own
    file read, so threading it through here costs callers nothing and lets
    ``_LOCATORS``-style languages ignore it entirely.
    """
    if language in _LOCATORS:
        if not source:
            return source
        return _blank(source, scan(language, source))
    if language == "pascal" and source:
        from .parser_helpers import prepare_pascal_source

        return prepare_pascal_source(source, path)
    return source
