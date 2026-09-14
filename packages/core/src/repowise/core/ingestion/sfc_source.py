"""Single-file-component source preparation.

A ``.svelte``, ``.vue`` or ``.razor`` file is more than one language in one
file: ``<script>`` blocks hold TS/JS, the markup is a framework-flavoured
HTML, and ``@code`` / ``@{ }`` regions hold C#. The markup grammars parse
the file but hand each ``<script>`` body back as one opaque ``raw_text``
node, so a ``.scm`` query run against them captures no symbol, no import and
no call.

So a markup grammar is used here only to *locate* the code-bearing regions:

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
copy of the walker: grammar-backed for Svelte and Vue, byte-scanned for
Razor, which has no usable tree-sitter grammar (see the ``razor`` locator).

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
    """How one markup language exposes its code regions and component tags.

    Two shapes are supported. A **grammar-backed** locator (Svelte, Vue)
    parses the file with a tree-sitter markup grammar and ``visit`` walks
    every node. A **byte-scanned** locator (Razor) has ``grammar_module`` /
    ``visit`` left as None and ``byte_scan`` instead walks the raw bytes;
    there is no usable ``tree-sitter-razor`` on PyPI, and an HTML grammar
    actively mis-parses Razor (``List<Order>`` reads as an HTML element).
    Both shapes append to the same ``state`` dict and produce the same
    :class:`SfcScan`.
    """

    # Importable module exposing a ``language()`` capsule (None = byte-scan).
    grammar_module: str | None = None
    # Called for every node (grammar-backed locators); appends to ``state``.
    visit: Callable[[Node, dict], None] | None = None
    # Maps a raw markup tag name to the component name it instantiates, or
    # None when the tag is not a user component.
    component_name: Callable[[str], str | None] | None = None
    # Byte-scanned locators (Razor): called once with the raw source bytes;
    # appends ``(start, end)`` spans / terminator offsets / ``(name, line)``
    # component tags to ``state`` exactly like a grammar walk would.
    byte_scan: Callable[[bytes, dict], None] | None = None


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
# Razor / Blazor
# ---------------------------------------------------------------------------

# Razor's C#-bearing constructs, without the leading ``@`` sigil (the scan
# compares against ``source[at + 1:]``). ``@code`` / ``@functions`` / ``@{ }``
# hold class-body or statement content.
_RAZOR_BLOCK_OPENERS = (b"code", b"functions", b"{")

# Razor comments hide both markup and C#. HTML comments only hide markup: a
# Razor expression inside ``<!-- -->`` still runs, so only the tag pass skips
# them.
_RAZOR_COMMENT_CLOSE = b"*@"
_HTML_COMMENT_OPEN = b"<!--"
_HTML_COMMENT_CLOSE = b"-->"


def _csharp_literal_end(source: bytes, index: int) -> int | None:
    """Index just past the C# literal or comment starting at ``index``.

    The brace-depth walk must not count braces inside these: a ``{`` in a
    string would leave the block unclosed or swallow the markup after it.
    """
    two = source[index : index + 2]
    if two == b"//":
        newline = source.find(b"\n", index + 2)
        return len(source) if newline < 0 else newline
    if two == b"/*":
        close = source.find(b"*/", index + 2)
        return len(source) if close < 0 else close + 2

    cursor = index
    verbatim = False
    # ``$`` and ``@`` may appear in either order. An interpolated literal's
    # braces are balanced by construction, so skipping the whole literal is
    # right for it too.
    while source[cursor : cursor + 1] in (b"@", b"$"):
        verbatim = verbatim or source[cursor : cursor + 1] == b"@"
        cursor += 1
    quote = source[cursor : cursor + 1]

    if quote == b'"':
        cursor += 1
        while cursor < len(source):
            byte = source[cursor : cursor + 1]
            if verbatim:
                if byte == b'"':
                    if source[cursor + 1 : cursor + 2] == b'"':
                        cursor += 2
                        continue
                    return cursor + 1
                cursor += 1
                continue
            if byte == b"\\":
                cursor += 2
                continue
            if byte == b'"':
                return cursor + 1
            cursor += 1
        return len(source)

    if quote == b"'" and cursor == index:
        # A char literal is at most a few bytes (``'\u0041'``); an apostrophe
        # in markup inside ``@{ }`` must not swallow the rest of the block.
        probe = cursor + 1
        while probe < len(source) and probe <= cursor + 8:
            byte = source[probe : probe + 1]
            if byte == b"\\":
                probe += 2
                continue
            if byte == b"'":
                return probe + 1
            probe += 1
    return None


def _razor_byte_scan(source: bytes, state: dict) -> None:
    """Locate C# regions and component tags in ``.razor`` / ``.cshtml`` bytes.

    The scanner walks the raw bytes with no grammar: ``@code { ... }`` /
    ``@functions { ... }`` / ``@{ ... }`` interiors are projected as C#
    (brace-depth matched so nested object initialisers survive), and
    PascalCase tags (``<RadzenDataGrid>``) are recorded as component
    instantiations. Everything else (directives, markup, attributes) is
    blanked by :func:`_blank`.

    Deliberate exclusions, mirroring the Svelte/Vue ceilings:

    * ``@@`` is the Razor escape for a literal ``@`` (``@@code`` renders
      ``@code``), skipped so an escaped sigil never opens a false block.
    * A ``@`` that is part of a longer identifier token (``email@host``,
      ``user@@example.com``) is not a directive: the following character
      must be alphabetic or ``{`` for the sigil to count.
    * ``@* ... *@`` comments are skipped by both passes. Commented-out code
      compiles to nothing, so a block or tag inside one would be a wrong edge.
    * ``@using X`` directives are NOT projected (Razor drops the trailing
      ``;``, which the C# grammar needs; the projection would have to
      rewrite bytes to satisfy it). The ``@inject`` / ``@bind`` /
      ``@on*`` attribute-value forms are two-way bindings, not call edges,
      the same posture as Svelte's ``{#each}`` heads and Vue's ``v-for``.
    """
    comments: list[tuple[int, int]] = []

    # -- C# regions ----------------------------------------------------------
    # Scanned FIRST so the component-tag pass below can skip their interiors:
    # ``List<Order>`` inside a C# region is a generic type argument, not a
    # component tag, and the C# grammar (not the tag pass) owns that text.
    # One sequential walk, so a ``@code`` inside a comment and a ``@*`` inside
    # a C# string are each jumped over rather than matched.
    pos = 0
    while True:
        at = source.find(b"@", pos)
        if at < 0:
            break
        following = source[at + 1 : at + 2]
        # Skip the @@ escape.
        if following == b"@":
            pos = at + 2
            continue
        if following == b"*":
            close = source.find(_RAZOR_COMMENT_CLOSE, at + 2)
            end = len(source) if close < 0 else close + len(_RAZOR_COMMENT_CLOSE)
            comments.append((at, end))
            pos = end
            continue
        rest = source[at + 1 :]
        matched = None
        for opener in _RAZOR_BLOCK_OPENERS:
            if rest.startswith(opener):
                matched = opener
                break
        if matched is None:
            # Not a block opener: keep scanning for the next sigil.
            pos = at + 1
            continue

        open_brace = at + 1 + len(matched)
        if matched == b"{":
            # For ``@{`` the sigil IS the opener: the brace sits at ``at + 1``,
            # not one past a keyword like ``code`` / ``functions``.
            open_brace = at + 1
        # Skip whitespace between the keyword and the opening brace.
        while open_brace < len(source) and source[open_brace : open_brace + 1] in (
            b" ",
            b"\t",
            b"\r",
            b"\n",
        ):
            open_brace += 1
        if open_brace >= len(source) or source[open_brace : open_brace + 1] != b"{":
            pos = at + 1
            continue

        depth = 0
        end = None
        cursor = open_brace
        while cursor < len(source):
            byte = source[cursor : cursor + 1]
            if byte in (b'"', b"'", b"/", b"@", b"$"):
                skip = _csharp_literal_end(source, cursor)
                if skip is not None:
                    cursor = skip
                    continue
            if byte == b"{":
                depth += 1
            elif byte == b"}":
                depth -= 1
                if depth == 0:
                    end = cursor
                    break
            cursor += 1
        if end is None:
            pos = at + 1
            continue

        # The interior is the C# body. Fence it with both braces so a
        # sibling block cannot run into it and vice versa.
        interior_start, interior_end = open_brace + 1, end
        if interior_end > interior_start:
            state["spans"].append((interior_start, interior_end))
            state["terminators"].append(open_brace)
            state["terminators"].append(end)
        pos = end + 1

    # -- component tags: <PascalCase ...> / <PascalCase /> ------------------
    # Skip any ``<`` inside a C# region or a Razor comment: ``List<Order>`` is
    # a generic type argument, ``a < b`` is a comparison, and a commented-out
    # tag renders nothing. HTML comments are jumped over for the same reason.
    hidden = tuple(state["spans"]) + tuple(comments)

    def _is_hidden(offset: int) -> bool:
        return any(start <= offset < end for start, end in hidden)

    pos = 0
    while True:
        lt = source.find(b"<", pos)
        if lt < 0:
            break
        if _is_hidden(lt):
            pos = lt + 1
            continue
        if source.startswith(_HTML_COMMENT_OPEN, lt):
            close = source.find(_HTML_COMMENT_CLOSE, lt + len(_HTML_COMMENT_OPEN))
            pos = len(source) if close < 0 else close + len(_HTML_COMMENT_CLOSE)
            continue
        name_start = lt + 1
        name_end = name_start
        while name_end < len(source) and (
            source[name_end : name_end + 1].isalnum()
            or source[name_end : name_end + 1] in (b"_", b".")
        ):
            name_end += 1
        if name_end > name_start:
            # A namespace-qualified tag (``<Shared.Grid />``) instantiates the
            # last dotted segment.
            raw = source[name_start:name_end].decode("utf-8", errors="replace")
            name = _razor_component_name(raw.rsplit(".", 1)[-1])
            if name:
                line = source.count(b"\n", 0, lt) + 1
                state["tags"].append((name, line))
        pos = lt + 1


def _razor_component_name(name: str) -> str | None:
    """``<RadzenDataGrid>`` instantiates a component; ``<div>`` is HTML.

    The Svelte rule transfers verbatim: a PascalCase tag names a user
    component, a lowercase tag is a plain element. Razor components are
    PascalCase by convention, so no kebab- or ``+``-normalisation is
    needed.
    """
    if not name or not name[0].isupper():
        return None
    return name


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
    # Razor has no usable tree-sitter grammar on PyPI, and an HTML grammar
    # actively mis-parses it (``List<Order>`` reads as an HTML element). The
    # locator byte-scans instead: ``@code`` / ``@functions`` / ``@{ }``
    # interiors project as C#, PascalCase tags become component calls.
    "razor": Locator(
        component_name=_razor_component_name,
        byte_scan=_razor_byte_scan,
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
    state: dict = {"spans": [], "terminators": [], "tags": []}

    # Byte-scanned locator (Razor): no markup grammar exists, so the raw
    # bytes are scanned directly. There is no tree to carry a parse-error
    # flag, and a byte scan cannot mis-parse, so ``has_error`` stays False.
    if locator.byte_scan is not None:
        locator.byte_scan(source, state)
        return SfcScan(
            js_spans=tuple(sorted(state["spans"])),
            terminators=tuple(sorted(state["terminators"])),
            component_tags=tuple(state["tags"]),
            has_error=False,
        )

    grammar = _grammar(locator.grammar_module or "")
    if grammar is None:
        return _EMPTY_SCAN

    try:
        from tree_sitter import Parser

        tree = Parser(grammar).parse(source)
    except Exception as exc:
        log.debug("sfc_scan_failed", language=language, error=str(exc))
        return _EMPTY_SCAN

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
    if language == "objectivec" and source:
        from .parser_helpers import prepare_objectivec_source

        return prepare_objectivec_source(source)
    return source
