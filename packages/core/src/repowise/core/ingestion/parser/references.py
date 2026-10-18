"""Reference sites (a function named without a call) and type references."""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from ..extractors import node_text
from ..models import CallSite, FileInfo, Import, Symbol, TypeReference
from ..parser_helpers import (
    TYPE_HEAD_EXTRACTORS,
    _classify_param_origin,
    _find_enclosing_symbol,
    _head_type_identifier,
    _rust_shadowed_by_type_param,
)

_node_text = node_text

# Languages whose query defines the ``@reference.*`` captures. The SFC tags
# reuse the TS captures but stay out: resolution has no local-shadow scan for them.
_REFERENCE_LANGUAGES = ("cpp", "c", "go", "rust", "kotlin", "typescript", "javascript", "python")

# Languages where a bare name resolves only through the file's own symbols or
# a name it imports, so any other bare name cannot pass the reference floor.
_NAMED_BINDING_LANGUAGES = frozenset({"typescript", "javascript", "python"})


def _bare_reference_names(
    language: str, symbols: list[Symbol], imports: list[Import]
) -> frozenset[str] | None:
    """Names a receiver-less reference in this file could resolve to, or None for any.

    Most bare identifiers in a value position are locals; dropping them here
    skips their per-site work. A wildcard import keeps every name.
    """
    if language not in _NAMED_BINDING_LANGUAGES:
        return None
    imported = {name for imp in imports for name in imp.local_names}
    if "*" in imported:
        return None
    return frozenset(imported.union(s.name for s in symbols))


def _extract_references(
    matches: list[dict],
    file_info: FileInfo,
    src: str,
    symbols: list[Symbol],
    imports: list[Import],
) -> list[CallSite]:
    """Extract sites that name a function without calling it.

    Six shapes carry a function by name and never call it where a parser
    can see: a dispatch-table entry, a callback field, an argument to a
    registration macro, a func value in argument position, a struct-field
    initialiser, and a ``::`` callable reference. Each leaves the named
    function with no inbound edge, which read as a ``safe_to_delete``
    unused export and took out whole handler and interop layers.
    TS/JS and Python capture any value position: an argument, a list or
    object entry, a return, an assignment and a default export.

    ``@reference.receiver`` is optional; capturing it is what lets
    ``_add_reference_edges`` restrict a bare name to free functions and
    allow a qualified name to reach a method.

    Self-gating on the reference captures, so a language whose query
    defines none produces nothing and pays two dict lookups. Two guards
    keep the broad syntactic positions from claiming ordinary code:

    * A macro argument requires a SCREAMING_CASE callee and must be that
      macro's only argument. Uppercase alone is not enough: assertion
      macros are spelled the same way and take values, so ``EXPECT_EQ(
      capacity, 100)`` bound a local to whatever free function shared its
      name. Registering something registers one thing, which separates
      ``BENCHMARK(BM_Foo)`` from ``TEST(SuiteName, TestName)``.
    * A table entry must sit outside any function body. A dispatch table is
      a file-, namespace- or class-scope aggregate; the same braces inside
      a function are a constructor member-init or a local aggregate, where
      ``{data, size}`` names parameters, not functions.

    Whether the name denotes a function at all is settled later, at
    resolution, where the symbol kind is known.
    """
    from ..language_data import get_builtin_calls

    scope = _ReferenceScope(
        builtins=get_builtin_calls(file_info.language),
        bindable=_bare_reference_names(file_info.language, symbols, imports),
        symbol_ranges=sorted(
            [(s.start_line, s.end_line, s.id) for s in symbols],
            key=lambda t: (t[0], -t[1]),
        ),
        callable_ids={s.id for s in symbols if s.kind in ("function", "method")},
    )
    references: list[CallSite] = []

    for capture_dict in matches:
        plain_nodes = capture_dict.get("reference.name", [])
        table_nodes = capture_dict.get("reference.table", [])
        if not plain_nodes and not table_nodes:
            continue

        receiver_nodes = capture_dict.get("reference.receiver", [])
        receiver = (
            _node_text(receiver_nodes[0], src).strip() if receiver_nodes else None
        ) or None
        if not _macro_argument_allowed(capture_dict, plain_nodes, src):
            continue

        candidates = [(node, False) for node in plain_nodes]
        candidates += [(node, True) for node in table_nodes]
        for name_node, is_table in candidates:
            site = scope.site(name_node, is_table, receiver, src)
            if site is not None:
                references.append(site)

    return references


def _macro_argument_allowed(capture_dict: dict, plain_nodes: list[Node], src: str) -> bool:
    """A registration-macro argument needs a SCREAMING_CASE callee and to be its only argument."""
    via_nodes = capture_dict.get("reference.via", [])
    if not via_nodes:
        return True
    via = _node_text(via_nodes[0], src).strip()
    if not via or not via.isupper():
        return False
    arg_list = plain_nodes[0].parent if plain_nodes else None
    return arg_list is not None and len(arg_list.named_children) == 1


@dataclass
class _ReferenceScope:
    """Per-file state the reference sites of one file are filtered against."""

    builtins: frozenset[str]
    bindable: frozenset[str] | None
    symbol_ranges: list[tuple[int, int, str]]
    callable_ids: set[str]
    seen: set[tuple[int, str, str | None]] = field(default_factory=set)

    def site(
        self, name_node: Node, is_table: bool, receiver: str | None, src: str
    ) -> CallSite | None:
        """The reference *name_node* makes, or None when it is filtered or repeated."""
        name = _node_text(name_node, src).strip()
        if not name or name in self.builtins:
            return None
        if self.bindable is not None and receiver is None and name not in self.bindable:
            return None
        line = name_node.start_point[0] + 1
        enclosing = _find_enclosing_symbol(line, self.symbol_ranges)
        if is_table and enclosing in self.callable_ids:
            return None
        if (line, name, receiver) in self.seen:
            return None
        self.seen.add((line, name, receiver))
        return CallSite(
            target_name=name,
            receiver_name=receiver,
            caller_symbol_id=enclosing,
            line=line,
            argument_count=None,
        )


def _extract_type_refs(
    matches: list[dict],
    src: str,
    lang: str = "",
) -> list[TypeReference]:
    """Collect ``@param.type`` captures into TypeReference records.

    C# emits these from constructor / method / delegate / primary-ctor
    parameter types; Go emits them from parameter, struct-field, return,
    and composite-literal type positions (see ``go.scm``). The graph
    builder resolves each reference to a defining file via the
    language-specific resolver index and emits a file-level edge.

    The head-identifier extractor is language-specific (Go unwraps
    ``*T`` / ``[]T`` / ``map[K]V`` / ``pkg.T``); see
    ``TYPE_HEAD_EXTRACTORS``. Capture origin is inferred from the
    enclosing node: ``constructor_declaration`` → ``ctor_param``,
    ``method_declaration`` → ``method_param`` (C#);
    ``field_declaration`` → ``field_type``, ``composite_literal`` →
    ``composite_literal`` (Go).
    """
    head_of = TYPE_HEAD_EXTRACTORS.get(lang, _head_type_identifier)

    refs: list[TypeReference] = []
    seen: set[tuple[str, int]] = set()

    for capture_dict in matches:
        type_nodes = capture_dict.get("param.type", [])
        if not type_nodes:
            continue
        for type_node in type_nodes:
            head = head_of(type_node, src)
            if not head:
                continue
            # ``struct Wrapper<Item>`` binds Item as a type parameter; the
            # capture cannot tell it from a real ``struct Item``.
            if lang == "rust" and _rust_shadowed_by_type_param(type_node, head, src):
                continue
            line = type_node.start_point[0] + 1
            key = (head, line)
            if key in seen:
                continue
            seen.add(key)
            origin = _classify_param_origin(type_node)
            refs.append(TypeReference(type_name=head, line=line, origin=origin))

    return refs
