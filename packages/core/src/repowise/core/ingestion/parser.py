"""Unified AST parser — one class for all languages.

Architecture
============
Per-language differences live in two places:
  1. ``packages/core/queries/<lang>.scm``  — tree-sitter S-expression queries
     that capture symbols and imports using consistent capture-name conventions.
  2. ``LANGUAGE_CONFIGS`` dict in this module — a ``LanguageConfig`` per language
     that maps node types to symbol kinds, defines visibility rules, etc.

``ASTParser`` itself contains *no* if/elif language branches.  Adding support
for a new language means writing one ``.scm`` file and one ``LanguageConfig``
entry.  No Python class, no new module.

Capture-name conventions (shared across ALL .scm files):
  @symbol.def       — the full definition node (line numbers, kind lookup)
  @symbol.name      — name identifier
  @symbol.params    — parameter list (optional)
  @symbol.modifiers — decorators / visibility modifiers (optional)
  @symbol.receiver  — Go method receiver (optional, used for parent detection)
  @import.statement — full import node
  @import.module    — module path being imported
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from functools import cache
from pathlib import Path

import structlog
from tree_sitter import Language, Node, Parser

from .extractors import (
    build_signature,
    extract_go_receiver_type,
    extract_heritage,
    extract_import_bindings,
    extract_module_docstring,
    extract_symbol_docstring,
    node_text,
    refine_elixir_call_kind,
    refine_fsharp_type_kind,
    refine_go_type_kind,
    refine_kotlin_class_kind,
    refine_pascal_type_kind,
)
from .extractors.bindings.elixir import elixir_import_modules
from .extractors.bindings.python import expand_bare_relative_imports
from .extractors.bindings.ts_js import (
    declarator_binds_callable,
    declarator_value_is_module_ref,
)
from .extractors.synthetic_symbols import extract_synthetic_symbols
from .extractors.visibility import (
    refine_cpp_visibility,
    refine_csharp_visibility,
    refine_ts_visibility,
    ts_deferred_export_names,
    ts_export_aliases,
)
from .language_configs import LANGUAGE_CONFIGS, LanguageConfig
from .languages.registry import REGISTRY as _LANG_REGISTRY
from .models import (
    CallReceiver,
    CallSite,
    FileInfo,
    Import,
    ParsedFile,
    Symbol,
    TypeReference,
    compute_content_hash,
)
from .parser_helpers import (
    TYPE_HEAD_EXTRACTORS,
    _build_qualified_name,
    _classify_param_origin,
    _collect_error_nodes,
    _count_arguments,
    _dedupe_objc_interface_symbols,
    _dedupe_pascal_interface_symbols,
    _elixir_call_is_definitional,
    _elixir_is_template_definition,
    _elixir_module_parent,
    _elixir_symbol_name,
    _find_enclosing_symbol,
    _fsharp_binding_end_line,
    _fsharp_binding_has_params,
    _fsharp_binding_is_nested,
    _fsharp_parent_is_type,
    _fsharp_parent_name,
    _has_callable_ancestor,
    _head_type_identifier,
    _is_async_node,
    _objc_call_is_block_variable,
    _objc_container_node,
    _objc_container_parent,
    _objc_is_macro_enum,
    _objc_message_selector,
    _objc_symbol_name,
    _qualified_cpp_parent,
    _qualified_pascal_parent,
    _run_query,
)
from .python_local_refs import extract_python_local_refs
from .sfc_source import component_call_sites, prepare_source
from .special_handlers import SPECIAL_HANDLER_LANGUAGES, parse_special

log = structlog.get_logger(__name__)

# Any single file emitting more than this many symbols is almost
# certainly machine-generated (large gRPC service contracts, OpenAPI
# bindings, SQL schema bindings). Warn rather than truncate — operators
# can decide whether to add the file to ``_NEVER_FLAG_PATTERNS`` or to
# exclude it via traversal.
_SYMBOL_COUNT_WARN_THRESHOLD = 500

QUERIES_DIR = Path(__file__).parent / "queries"

# Node types whose .scm patterns are anchored at module/program level
# (constants and module variables). They can never be function-local, so
# the callable-ancestor filter must not apply — and for TS/JS declarators
# it would misfire on the parent lexical_declaration kind mapping.
_MODULE_ANCHORED_NODE_TYPES = frozenset({"assignment", "variable_declarator"})

# Languages whose source reaches the parser as TypeScript/JavaScript. The two
# SFC tags are here because ``sfc_source`` projects their <script> blocks into
# a TS buffer at identical offsets, so every TS/JS code path applies verbatim.
_TS_JS_LANGUAGES = ("typescript", "javascript", "svelte", "vue")

# Languages whose query defines the ``@reference.*`` captures. Every other
# language would only scan the whole match list to find nothing, so the check
# is here rather than inside ``_extract_references``.
_REFERENCE_LANGUAGES = ("cpp", "c", "go", "rust", "kotlin")


def _call_receiver_from_node(node: Node, src: str) -> CallReceiver | None:
    """Describe an inner call captured as another call's receiver.

    The queries identify the complete inner AST node.  This helper reads only
    named tree-sitter fields, so nested arguments and formatting cannot change
    which call is carried into resolution.
    """

    arguments = node.child_by_field_name("arguments")
    argument_count = _count_arguments(arguments) if arguments is not None else None

    target = node.child_by_field_name("name")
    receiver = node.child_by_field_name("object")
    if target is None:
        function = node.child_by_field_name("function")
        if function is None:
            return None
        if function.type in ("identifier", "property_identifier", "field_identifier"):
            target = function
            receiver = None
        elif function.type == "generic_name":
            target = function.child_by_field_name("name") or next(
                (child for child in function.named_children if child.type == "identifier"), None
            )
            receiver = None
        else:
            target = (
                function.child_by_field_name("name")
                or function.child_by_field_name("property")
                or function.child_by_field_name("field")
            )
            receiver = (
                function.child_by_field_name("expression")
                or function.child_by_field_name("object")
                or function.child_by_field_name("argument")
            )

    if target is None:
        return None
    target_name = _node_text(target, src).strip()
    if not target_name:
        return None

    receiver_name = None
    if receiver is not None and receiver.type in ("identifier", "this"):
        receiver_name = _node_text(receiver, src).strip() or None
    return CallReceiver(target_name, receiver_name, argument_count)


# C/C++ node types that spell a type. Each one covers both the definition and
# the forward declaration of that type; only the ``body`` field tells them
# apart. See ``_is_bodiless_cpp_type``.
# ``union_specifier`` is absent because neither grammar's query captures one
# as a symbol today. If a union capture is ever added, add it here too, or a
# bodiless ``union U;`` goes back to reading as a definition.
_CPP_TYPE_SPECIFIER_NODES = frozenset({"class_specifier", "struct_specifier", "enum_specifier"})
_CPP_EXPORT_FORWARD_DECLARATION_NODES = frozenset({"declaration", "field_declaration"})

_CPP_PREPROC_CONDITIONAL_NODES = frozenset(
    {"preproc_if", "preproc_ifdef", "preproc_ifndef", "preproc_elif", "preproc_else"}
)
_CPP_PREPROC_ALTERNATIVE_NODES = frozenset({"preproc_elif", "preproc_else"})
_CPP_LINE_SPLICE_RE = re.compile(r"\\(?:\r\n?|\n)")
_CPP_PREPROC_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\r\n]*", re.DOTALL)
_CPP_MACRO_STACK_RE = re.compile(r'\b(push_macro|pop_macro)\s*\(\s*"([^"]+)"\s*\)')
_CPP_UNIVERSAL_CHARACTER_NAME_RE = re.compile(r"\\(?:u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8}))")
_CPP_IDENTIFIER_TOKEN_RE = re.compile(r"(?:[^\W\d]|\$)[\w$]*")
_CPP_INCLUDE_LIKE_DIRECTIVES = frozenset({"include_next", "import"})


class _CppMacroState(Enum):
    EMPTY_OBJECT = auto()
    NOT_EMPTY_OBJECT = auto()
    UNDEFINED = auto()
    UNKNOWN = auto()


class _CppMacroAction(Enum):
    DEFINE_EMPTY = auto()
    DEFINE_OTHER = auto()
    UNDEFINE = auto()
    PUSH = auto()
    POP = auto()
    UNKNOWN = auto()


@dataclass(frozen=True)
class _CppMacroEvent:
    position: int
    state: _CppMacroState
    definition: Node | None = None
    branch_path: tuple[int, ...] = ()


@dataclass(frozen=True)
class _CppMacroOperation:
    position: int
    action: _CppMacroAction
    name: str
    node: Node
    definition: Node | None = None


@dataclass(frozen=True)
class _CppMacroStackEntry:
    event: _CppMacroEvent | None
    branch_path: tuple[int, ...]


@dataclass(frozen=True)
class _CppMacroFacts:
    events: dict[str, tuple[_CppMacroEvent, ...]]
    barriers: tuple[int, ...]

    def empty_definition_at(self, name: str, declaration: Node) -> Node | None:
        """Prove that *name* is an empty object macro at *declaration*."""
        events = self.events.get(name, ())
        event_index = (
            bisect_left(events, declaration.start_byte, key=lambda event: event.position) - 1
        )
        active_event = events[event_index] if event_index >= 0 else None
        if (
            active_event is None
            or active_event.state is not _CppMacroState.EMPTY_OBJECT
            or active_event.definition is None
        ):
            return None

        declaration_branch = _cpp_preproc_branch_path(declaration)
        event_branch = active_event.branch_path
        definition_branch = _cpp_preproc_branch_path(active_event.definition)
        if declaration_branch[: len(event_branch)] != event_branch:
            return None
        if declaration_branch[: len(definition_branch)] != definition_branch:
            return None

        barrier_index = bisect_left(self.barriers, declaration.start_byte) - 1
        latest_barrier = self.barriers[barrier_index] if barrier_index >= 0 else None
        if latest_barrier is not None and latest_barrier > active_event.position:
            return None
        return active_event.definition


@dataclass(frozen=True)
class _CppExportType:
    range_node: Node
    name: str
    is_forward_declaration: bool


def _cpp_normalize_preproc_text(text: str) -> str:
    """Apply the preprocessing transforms needed for directive arguments."""
    without_splices = _CPP_LINE_SPLICE_RE.sub("", text)
    return _CPP_PREPROC_COMMENT_RE.sub(" ", without_splices)


def _cpp_preproc_call_parts(text: str) -> tuple[str | None, str]:
    """Return a generic preprocessing call's directive and normalized argument."""
    normalized = _cpp_normalize_preproc_text(text).strip()
    match = re.match(r"(?:#|%:)[^\S\r\n]*([A-Za-z_]\w*)", normalized)
    if match is None:
        return None, ""
    return match.group(1), normalized[match.end() :].lstrip()


def _cpp_identifier_continues(char: str) -> bool:
    """Whether *char* can continue a C++ identifier in supported grammars."""
    return char in {"_", "$", "\\"} or f"a{char}".isidentifier()


def _cpp_normalize_identifier(text: str) -> str:
    """Normalize literal and universal-character-name identifier spellings."""

    def replace_ucn(match: re.Match[str]) -> str:
        digits = match.group(1) or match.group(2)
        try:
            codepoint = int(digits, 16)
            if 0xD800 <= codepoint <= 0xDFFF:
                return match.group()
            return chr(codepoint)
        except ValueError:
            return match.group()

    decoded = _CPP_UNIVERSAL_CHARACTER_NAME_RE.sub(replace_ucn, text)
    return unicodedata.normalize("NFC", decoded)


def _cpp_known_macro_from_argument(
    argument: str, known_names_by_length: tuple[str, ...]
) -> str | None:
    """Match the first preprocessing token against locally known macro names."""
    normalized = _cpp_normalize_identifier(_cpp_normalize_preproc_text(argument)).lstrip()
    for name in known_names_by_length:
        if not normalized.startswith(name):
            continue
        remainder = normalized[len(name) :]
        if not remainder or not _cpp_identifier_continues(remainder[0]):
            return name
    return None


def _cpp_known_macros_in_text(text: str, known_names: set[str]) -> set[str]:
    """Return locally defined macro identifiers referenced anywhere in *text*."""
    normalized = _cpp_normalize_identifier(_cpp_normalize_preproc_text(text))
    return {
        match.group()
        for match in _CPP_IDENTIFIER_TOKEN_RE.finditer(normalized)
        if match.group() in known_names
    }


def _cpp_macro_stack_operation(text: str) -> tuple[str | None, str | None]:
    """Return a push/pop operation and normalized target from pragma text."""
    normalized = _cpp_normalize_preproc_text(text).replace(r"\"", '"')
    operation_match = re.search(r"\b(push_macro|pop_macro)\b", normalized)
    if operation_match is None:
        return None, None
    match = _CPP_MACRO_STACK_RE.search(normalized)
    if match is None:
        return operation_match.group(1), None
    return match.group(1), _cpp_normalize_identifier(match.group(2))


def _cpp_preproc_branch_path(node: Node) -> tuple[int, ...]:
    """Return the effective C/C++ preprocessor branches around *node*."""
    branch_ids: list[int] = []
    covered_conditionals: set[int] = set()
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in _CPP_PREPROC_ALTERNATIVE_NODES:
            # An ``else``/``elif`` is its own branch; do not also label its
            # contents as belonging to the parent conditional's main branch.
            if ancestor.id not in covered_conditionals:
                branch_ids.append(ancestor.id)
            parent = ancestor.parent
            if parent is not None and parent.type in _CPP_PREPROC_CONDITIONAL_NODES:
                covered_conditionals.add(parent.id)
        elif (
            ancestor.type in _CPP_PREPROC_CONDITIONAL_NODES
            and ancestor.id not in covered_conditionals
        ):
            branch_ids.append(ancestor.id)
        ancestor = ancestor.parent
    branch_ids.reverse()
    return tuple(branch_ids)


def _build_cpp_macro_facts(matches: list[dict], src: str) -> _CppMacroFacts:
    """Build the conservative, source-ordered macro facts used by C++ recovery."""
    macro_definitions: dict[int, tuple[Node, str]] = {}
    preproc_calls: dict[int, Node] = {}
    include_nodes: dict[int, Node] = {}
    call_sites: dict[int, tuple[Node, str]] = {}

    for capture_dict in matches:
        def_nodes = capture_dict.get("symbol.def", [])
        name_nodes = capture_dict.get("symbol.name", [])
        if (
            def_nodes
            and def_nodes[0].type in ("preproc_def", "preproc_function_def")
            and name_nodes
        ):
            macro_node = def_nodes[0]
            macro_name = _cpp_normalize_identifier(_node_text(name_nodes[0], src))
            if macro_name:
                macro_definitions[macro_node.id] = (macro_node, macro_name)

        for node in capture_dict.get("symbol.cpp_preproc_call", []):
            preproc_calls[node.id] = node
        for node in capture_dict.get("symbol.cpp_macro_state_barrier", []):
            include_nodes[node.id] = node

        site_nodes = capture_dict.get("call.site", [])
        target_nodes = capture_dict.get("call.target", [])
        if site_nodes and target_nodes:
            call_sites[site_nodes[0].id] = (site_nodes[0], _node_text(target_nodes[0], src))

    known_names = {name for _, name in macro_definitions.values()}
    known_names_by_length = tuple(sorted(known_names, key=len, reverse=True))

    # A macro whose replacement contains a pragma stack operation is itself a
    # state hazard when invoked. Resolve simple wrapper aliases transitively;
    # anything more dynamic remains conservative at the invocation site.
    hazard_targets: dict[str, set[str]] = {name: set() for name in known_names}
    hazard_unknown: set[str] = set()
    replacement_references: dict[str, set[str]] = {name: set() for name in known_names}
    for macro_node, macro_name in macro_definitions.values():
        value_node = macro_node.child_by_field_name("value")
        replacement = _node_text(value_node, src) if value_node is not None else ""
        replacement_operation, target = _cpp_macro_stack_operation(replacement)
        if replacement_operation is not None:
            if target in known_names:
                hazard_targets[macro_name].add(target)
            elif target is None:
                hazard_unknown.add(macro_name)
        replacement_references[macro_name].update(
            _cpp_known_macros_in_text(replacement, known_names) - {macro_name}
        )

    alias_dependents: dict[str, set[str]] = {name: set() for name in known_names}
    for macro_name, references in replacement_references.items():
        for referenced_name in references:
            alias_dependents[referenced_name].add(macro_name)

    pending = deque(name for name in known_names if hazard_targets[name] or name in hazard_unknown)
    queued = set(pending)
    while pending:
        referenced_name = pending.popleft()
        queued.remove(referenced_name)
        for macro_name in alias_dependents[referenced_name]:
            inherited_targets = hazard_targets[referenced_name] - hazard_targets[macro_name]
            inherited_unknown = (
                referenced_name in hazard_unknown and macro_name not in hazard_unknown
            )
            if not inherited_targets and not inherited_unknown:
                continue
            hazard_targets[macro_name].update(inherited_targets)
            if inherited_unknown:
                hazard_unknown.add(macro_name)
            if macro_name not in queued:
                pending.append(macro_name)
                queued.add(macro_name)

    object_hazard_names = {
        macro_name
        for macro_node, macro_name in macro_definitions.values()
        if macro_node.type == "preproc_def"
        and (hazard_targets[macro_name] or macro_name in hazard_unknown)
    }

    # Object-like macros expand wherever their identifier token appears, not
    # only as a standalone expression. Walk the existing tree only when a
    # local definition proves that the name wraps a stack operation. An include
    # is a barrier at its own position, but without preprocessing context it is
    # not evidence that every later identifier is an imported wrapper.
    possible_invocations: dict[int, Node] = {}
    if object_hazard_names:
        root = next(iter(macro_definitions.values()))[0]
        while root.parent is not None:
            root = root.parent
        pending_nodes = [root]
        while pending_nodes:
            node = pending_nodes.pop()
            if node.type in {"preproc_def", "preproc_function_def"}:
                continue
            if node.type == "identifier":
                name = _cpp_normalize_identifier(_node_text(node, src))
                if name in object_hazard_names:
                    possible_invocations[node.id] = node
            pending_nodes.extend(node.children)

    operations: list[_CppMacroOperation] = []
    operation_keys: set[tuple[int, _CppMacroAction, str]] = set()
    barriers = {node.start_byte for node in include_nodes.values()}

    def add_operation(
        node: Node,
        action: _CppMacroAction,
        name: str,
        *,
        definition: Node | None = None,
    ) -> None:
        key = (node.start_byte, action, name)
        if key in operation_keys:
            return
        operation_keys.add(key)
        operations.append(
            _CppMacroOperation(
                position=node.start_byte,
                action=action,
                name=name,
                node=node,
                definition=definition,
            )
        )

    def add_stack_operation(node: Node, text: str, *, direct: bool) -> bool:
        operation, target = _cpp_macro_stack_operation(text)
        if operation is None:
            return False
        if target in known_names:
            action = (
                _CppMacroAction.PUSH
                if direct and operation == "push_macro"
                else _CppMacroAction.POP
                if direct and operation == "pop_macro"
                else _CppMacroAction.UNKNOWN
            )
            add_operation(node, action, target)
        elif target is None:
            barriers.add(node.start_byte)
        return True

    for macro_node, macro_name in macro_definitions.values():
        is_empty_object = (
            macro_node.type == "preproc_def" and macro_node.child_by_field_name("value") is None
        )
        add_operation(
            macro_node,
            (_CppMacroAction.DEFINE_EMPTY if is_empty_object else _CppMacroAction.DEFINE_OTHER),
            macro_name,
            definition=macro_node if is_empty_object else None,
        )

    for node in preproc_calls.values():
        directive, argument = _cpp_preproc_call_parts(_node_text(node, src))
        if directive in _CPP_INCLUDE_LIKE_DIRECTIVES:
            barriers.add(node.start_byte)
        elif directive == "undef":
            target = _cpp_known_macro_from_argument(argument, known_names_by_length)
            if target is not None:
                add_operation(node, _CppMacroAction.UNDEFINE, target)
        elif directive == "pragma":
            add_stack_operation(node, argument, direct=True)

    direct_pragma_call_ids: set[int] = set()
    for node, target_text in call_sites.values():
        normalized_target = _cpp_normalize_identifier(target_text)
        if normalized_target in {"_Pragma", "__pragma"}:
            direct_pragma_call_ids.add(node.id)
            add_stack_operation(node, _node_text(node, src), direct=True)

    def add_indirect_hazards(node: Node, text: str, target_text: str = "") -> None:
        if add_stack_operation(node, text, direct=False):
            return
        target_name = _cpp_normalize_identifier(target_text)
        if not target_name:
            target_name = _cpp_known_macro_from_argument(text, known_names_by_length) or ""
        referenced_names = _cpp_known_macros_in_text(text, known_names)
        if target_name:
            referenced_names.add(target_name)
        affected_names = set().union(
            *(hazard_targets.get(name, set()) for name in referenced_names)
        )
        for affected_name in affected_names:
            add_operation(node, _CppMacroAction.UNKNOWN, affected_name)
        if affected_names or referenced_names & hazard_unknown:
            # The wrapper may push or pop even when its final macro value is
            # conservatively represented as UNKNOWN. Taint the stack too, so
            # a later direct pop cannot restore a stale local snapshot.
            barriers.add(node.start_byte)

    for node, target_text in call_sites.values():
        if node.id not in direct_pragma_call_ids:
            add_indirect_hazards(node, _node_text(node, src), target_text)

    for node in possible_invocations.values():
        add_indirect_hazards(node, _node_text(node, src))

    operations.sort(key=lambda operation: operation.position)
    sorted_barriers = sorted(barriers)
    events: dict[str, list[_CppMacroEvent]] = {}
    current: dict[str, _CppMacroEvent] = {}
    stacks: dict[str, list[_CppMacroStackEntry]] = {}
    barrier_index = 0

    def append_event(operation: _CppMacroOperation, event: _CppMacroEvent) -> None:
        events.setdefault(operation.name, []).append(event)
        current[operation.name] = event

    for operation in operations:
        crossed_barrier = False
        while (
            barrier_index < len(sorted_barriers)
            and sorted_barriers[barrier_index] < operation.position
        ):
            barrier_index += 1
            crossed_barrier = True
        if crossed_barrier:
            # Includes and opaque pragma wrappers may mutate both the macro
            # and its push/pop stack. A later local definition can establish
            # the current value again, but only a later local push can
            # establish a stack entry that is safe to restore.
            current.clear()
            stacks.clear()

        branch_path = _cpp_preproc_branch_path(operation.node)
        if operation.action in {
            _CppMacroAction.DEFINE_EMPTY,
            _CppMacroAction.DEFINE_OTHER,
            _CppMacroAction.UNDEFINE,
            _CppMacroAction.UNKNOWN,
        }:
            state = {
                _CppMacroAction.DEFINE_EMPTY: _CppMacroState.EMPTY_OBJECT,
                _CppMacroAction.DEFINE_OTHER: _CppMacroState.NOT_EMPTY_OBJECT,
                _CppMacroAction.UNDEFINE: _CppMacroState.UNDEFINED,
                _CppMacroAction.UNKNOWN: _CppMacroState.UNKNOWN,
            }[operation.action]
            append_event(
                operation,
                _CppMacroEvent(
                    position=operation.position,
                    state=state,
                    definition=operation.definition,
                    branch_path=branch_path,
                ),
            )
            continue

        if operation.action is _CppMacroAction.PUSH:
            snapshot = current.get(operation.name)
            if (
                snapshot is not None
                and branch_path[: len(snapshot.branch_path)] != snapshot.branch_path
            ):
                snapshot = None
            stacks.setdefault(operation.name, []).append(
                _CppMacroStackEntry(event=snapshot, branch_path=branch_path)
            )
            continue

        stack = stacks.get(operation.name, [])
        entry = stack.pop() if stack else None
        current_event = current.get(operation.name)
        if (
            entry is None
            and current_event is not None
            and bisect_left(sorted_barriers, operation.position) == 0
            and branch_path[: len(current_event.branch_path)] == current_event.branch_path
        ):
            restored = _CppMacroEvent(
                position=operation.position,
                state=current_event.state,
                definition=current_event.definition,
                branch_path=branch_path,
            )
        elif (
            entry is None
            or branch_path[: len(entry.branch_path)] != entry.branch_path
            or entry.event is None
        ):
            restored = _CppMacroEvent(
                position=operation.position,
                state=_CppMacroState.UNKNOWN,
                branch_path=branch_path,
            )
            if entry is not None:
                stack.clear()
        else:
            restored = _CppMacroEvent(
                position=operation.position,
                state=entry.event.state,
                definition=entry.event.definition,
                branch_path=branch_path,
            )
        append_event(operation, restored)

    return _CppMacroFacts(
        events={name: tuple(name_events) for name, name_events in events.items()},
        barriers=tuple(sorted(barriers)),
    )


def _is_bodiless_cpp_type(language: str, node_type: str, def_node: Node) -> bool:
    """True for a C/C++ type forward declaration such as ``class Env;``.

    ``declaration_node_types`` already catches a *function* prototype, whose
    tree-sitter node genuinely is a ``declaration``. A type forward declaration
    is not: it arrives as the very same ``class_specifier`` a definition uses,
    minus the ``body`` field. Left unmarked it reads as a whole class defined
    on one line, so every consumer that distinguishes a declaration from a
    definition — call resolution's declaration/definition pairing and the
    dead-code passes — silently treats the header line as the real thing.
    """
    if language not in ("cpp", "c"):
        return False
    if node_type == "template_declaration":
        # ``template <typename T> class Foo;``. The wrapper carries no ``body``
        # field of its own — the inner specifier does — so asking the wrapper
        # would call every template a declaration. Ask the type it wraps.
        inner = next(
            (c for c in def_node.children if c.type in _CPP_TYPE_SPECIFIER_NODES),
            None,
        )
        return inner is not None and inner.child_by_field_name("body") is None
    if node_type not in _CPP_TYPE_SPECIFIER_NODES:
        return False
    if def_node.child_by_field_name("body") is not None:
        return False
    # ``typedef struct CBMAutomaton CBMAutomaton;`` — C's opaque-handle idiom.
    # The tag and the typedef name are the same identifier, so both patterns
    # match at one position and dedup keeps a single symbol. That symbol is the
    # typedef name, which *is* a deletable API artifact and must stay
    # reportable. Erring toward under-marking: a forward-declared tag under a
    # differently-named typedef (``typedef struct Impl_s Handle;``) is left
    # unmarked too, which only costs a suppression we never had.
    parent = def_node.parent
    return parent is None or parent.type != "type_definition"


def _cpp_export_macro_parent(node: Node, parent_names: dict[int, str]) -> str | None:
    """Return the real type name for a member inside a macro-decorated C++ type."""
    ancestor = node.parent
    while ancestor is not None:
        parent_name = parent_names.get(ancestor.id)
        if parent_name is not None:
            return parent_name
        ancestor = ancestor.parent
    return None


@cache
def _load_compiled_query(lang: str, grammar_tag: str | None = None) -> object | None:
    """Process-wide cache of compiled tree-sitter Query objects.

    Compiling `.scm` queries is non-trivial; in process-pool parsing each worker
    would otherwise recompile per file. ``grammar_tag`` may differ from
    ``lang`` when a language reuses another's grammar at a different
    variant — e.g. ``.tsx`` files reuse ``typescript.scm`` but must bind
    to the JSX-aware ``tsx`` grammar so React components don't drown in
    ERROR nodes.
    """
    grammar = grammar_tag or lang
    language = _get_language(grammar)
    if language is None:
        return None

    # The spec names the query file, so a language can reuse another's
    # queries wholesale (svelte -> typescript.scm). Every other spec declares
    # ``<tag>.scm``, which is what the default preserves.
    spec = _LANG_REGISTRY.get(lang)
    scm_name = (spec.scm_file if spec and spec.scm_file else None) or f"{lang}.scm"
    scm_path = QUERIES_DIR / scm_name
    if not scm_path.exists():
        log.debug("No .scm query file found", language=lang, path=str(scm_path))
        return None

    scm_text = scm_path.read_text(encoding="utf-8")
    # Grammar-variant-specific additions (e.g. JSX node captures that are
    # only valid against the ``tsx`` grammar but not the plain ``typescript``
    # one). Appended to the base SCM only when the variant scm file exists.
    if grammar_tag and grammar_tag != lang:
        extra_scm = QUERIES_DIR / f"{grammar_tag}.scm"
        if extra_scm.exists():
            scm_text = scm_text + "\n" + extra_scm.read_text(encoding="utf-8")
    try:
        from tree_sitter import Query  # type: ignore[attr-defined]

        return Query(language, scm_text)
    except Exception as exc:
        log.warning("Failed to compile query", language=lang, error=str(exc))
        return None


# Languages that intentionally have no AST parser.  Derived from the
# centralised LanguageRegistry — only non-code passthrough languages are
# included (not the extra git-blame-only languages).

# Excludes "openapi" (handled by special_handlers) and "unknown".
_PASSTHROUGH_LANGUAGES: frozenset[str] = _LANG_REGISTRY.unparseable_data_languages()

# ---------------------------------------------------------------------------
# Language registry — maps language tag → tree-sitter Language object
# ---------------------------------------------------------------------------


def _build_language_registry() -> dict[str, Language]:
    """Lazily load installed tree-sitter language packages.

    Driven by ``LanguageSpec.grammar_package`` / ``grammar_loader`` /
    ``shares_grammar_with`` from the centralised registry.
    """
    registry: dict[str, Language] = {}

    for spec in _LANG_REGISTRY.all_specs():
        # Languages that share another's grammar (e.g. C → cpp)
        if spec.shares_grammar_with:
            shared = registry.get(spec.shares_grammar_with)
            if shared:
                registry[spec.tag] = shared
            continue

        if not spec.grammar_package:
            continue

        try:
            mod = __import__(spec.grammar_package)
            loader_fn = getattr(mod, spec.grammar_loader)
            lang_obj = Language(loader_fn())
            registry[spec.tag] = lang_obj
        except Exception as exc:
            log.debug(
                "tree-sitter language unavailable",
                language=spec.tag,
                reason=str(exc),
            )

    # TypeScript's tsx variant — special case: same package, different loader
    if "typescript" in registry and "tsx" not in registry:
        try:
            import tree_sitter_typescript as _ts_mod

            registry["tsx"] = Language(_ts_mod.language_tsx())
        except Exception as exc:
            log.debug("tree-sitter language unavailable", language="tsx", reason=str(exc))

    return registry


_LANGUAGE_REGISTRY: dict[str, Language] = {}

# Languages already reported as having a config but no installed grammar, so
# the report is one line per language per process instead of one per file. A
# repo with a few thousand shell scripts otherwise emitted a few thousand
# identical lines, all saying the same three facts.
_MISSING_GRAMMAR_REPORTED: set[str] = set()


def missing_grammar_languages(language_tags: Iterable[str]) -> list[str]:
    """Of *language_tags*, those that parse via tree-sitter but have no grammar.

    Answers the question once, in the parent, before up to eight spawned
    workers each rediscover it and log their own copy of the answer at a level
    the CLI discards anyway.

    Deliberately uses ``find_spec`` rather than importing: the whole point is
    to stay cheap enough to run on every index. Building the real registry here
    would import every tree-sitter package into the parent process, which is
    memory the parse pool is about to need for something else.

    A tag with no :data:`LANGUAGE_CONFIGS` entry is not a gap — nothing claims
    to parse it — so it is skipped rather than reported.

    Ceiling: "importable" is not "loadable". A grammar whose compiled ABI does
    not match the installed ``tree_sitter`` imports fine and then raises inside
    ``Language()``, which this cannot see, so that case reports nothing here
    and stays a per-worker debug line. Reporting it properly means loading the
    grammars, which is the cost this function exists to avoid.
    """
    import importlib.util

    specs = {spec.tag: spec for spec in _LANG_REGISTRY.all_specs()}
    missing: list[str] = []
    for tag in language_tags:
        if tag not in LANGUAGE_CONFIGS:
            continue
        spec = specs.get(tag)
        if spec is None:
            continue
        package = spec.grammar_package
        if not package and spec.shares_grammar_with:
            shared = specs.get(spec.shares_grammar_with)
            package = shared.grammar_package if shared else None
        if not package:
            continue
        try:
            if importlib.util.find_spec(package) is None:
                missing.append(tag)
        except (ImportError, ValueError):
            missing.append(tag)
    return sorted(missing)


def _get_language(tag: str) -> Language | None:
    global _LANGUAGE_REGISTRY
    if not _LANGUAGE_REGISTRY:
        _LANGUAGE_REGISTRY = _build_language_registry()
    return _LANGUAGE_REGISTRY.get(tag)


# Private alias for internal use (kept for compatibility with _find_parent)
_node_text = node_text


def _normalize_php_receiver(text: str) -> str:
    """Spell PHP's receiver the way the resolver's strategies expect.

    `self::` and `static::` are the same dispatch as `$this`, and the self/this
    strategy tests `in ("self", "this")` — so without this the whole implicit-
    receiver population misses. `parent::` needs the heritage walk and is left
    to miss rather than guessed at.
    """
    name = text.lstrip("$")
    return "this" if name in ("self", "static") else name


# ---------------------------------------------------------------------------
# ASTParser
# ---------------------------------------------------------------------------


class ASTParser:
    """Unified AST parser — works for all languages via .scm query files.

    Usage::

        parser = ASTParser()
        parsed = parser.parse_file(file_info, source_bytes)

    Adding a new language:
    1. Write ``packages/core/queries/<lang>.scm``
    2. Add one entry to ``LANGUAGE_CONFIGS``
    That's it.  No Python class, no new module.
    """

    def __init__(self) -> None:
        pass

    def parse_file(self, file_info: FileInfo, source: bytes) -> ParsedFile:
        """Parse *source* bytes and return a fully populated ParsedFile."""
        lang = file_info.language
        content_hash = compute_content_hash(source)

        # Non-tree-sitter formats (OpenAPI, Dockerfile, Makefile, SQL) parse
        # via dedicated handlers. Checked before the grammar lookup: none of
        # these tags carry a LanguageConfig, so the no-grammar fallback below
        # would otherwise swallow them.
        if lang in SPECIAL_HANDLER_LANGUAGES:
            parsed = parse_special(file_info, source, lang)
            parsed.content_hash = content_hash
            return parsed

        config = LANGUAGE_CONFIGS.get(lang)
        # .tsx files need the JSX-aware grammar; tree-sitter-typescript's
        # default `language_typescript` errors out on every `<Component />`
        # and the resulting ERROR-node recovery hoists nested helpers
        # (handlers defined inside component bodies) to the top level.
        grammar_tag = "tsx" if lang == "typescript" and file_info.path.endswith(".tsx") else lang
        language = _get_language(grammar_tag)

        # tree-sitter-fsharp ships a second grammar (``language_signature``)
        # for .fsi signature files, and a spec loads exactly one. Read with
        # the implementation grammar, a signature file's ``val`` and member
        # signatures land in ERROR recovery, which hoists whatever the
        # recovery invents into the symbol list. The regex tier still gives
        # these files their ``open`` imports, which is all a signature file
        # contributes that another file does not also state.
        signature_file = lang == "fsharp" and file_info.path.endswith(".fsi")

        if config is None or language is None or signature_file:
            if config is not None and language is None and lang not in _MISSING_GRAMMAR_REPORTED:
                # Once per language, not once per file: the fact is about the
                # environment, and it does not become truer on the four
                # thousandth shell script.
                _MISSING_GRAMMAR_REPORTED.add(lang)
                log.debug("tree-sitter grammar unavailable", language=lang)
            # Languages without a grammar may still carry regex-tier import
            # extraction (their specs declare import_support="partial");
            # symbols stay empty — the regex tier claims no symbol knowledge.
            from .lightweight_imports import extract_lightweight_imports

            return ParsedFile(
                file_info=file_info,
                symbols=[],
                imports=extract_lightweight_imports(file_info, source),
                exports=[],
                docstring=None,
                parse_errors=[],
                content_hash=content_hash,
            )

        # An SFC (.svelte, .vue) is three languages in one file.
        # ``prepare_source`` blanks the markup and <style> so what reaches the
        # TypeScript grammar is valid TS at byte-identical offsets — no offset
        # translation is needed anywhere downstream. A no-op for every other
        # language without a registered locator/sanitizer -- Pascal's
        # sanitizers (project-file `in '...'` clauses, ERROR-node blanking)
        # live behind the same hook; see prepare_pascal_source in
        # parser_helpers.py for why they're wired in here rather than as
        # ad-hoc if-blocks.
        # ``content_hash`` above deliberately hashes the ORIGINAL bytes, so
        # incremental update still tracks the real file.
        original_source = source
        source = prepare_source(lang, source, path=file_info.path)

        parser = Parser(language)
        tree = parser.parse(source)
        src = source.decode("utf-8", errors="replace")
        root = tree.root_node

        parse_errors = _collect_error_nodes(root)

        # Adaptive TSX grammar fallback: if a .ts file contains JSX markup,
        # tree-sitter-typescript produces ERROR nodes. Re-parse using the TSX
        # grammar ONLY IF:
        #   1. Initial parse yielded error nodes (parse_errors is non-empty)
        #   2. The file projects to TypeScript and is not already on tsx
        #   3. Source contains JSX-specific closing tokens (b"/>" or b"</")
        # A .vue render function may be written in JSX
        # (``vnodes.push(<i class={c} />)``), which is the single TS parse
        # failure across a 1,593-file .vue corpus. ``source`` here is the
        # markup-blanked projection, so the template's own ``</`` and ``/>``
        # are already spaces — the token test still keys on real JSX only.
        # The swap is strictly safe: it only takes effect when TSX yields
        # FEWER errors than the first parse.
        if (
            parse_errors
            and lang in ("typescript", "vue")
            and grammar_tag != "tsx"
            and (b"/>" in source or b"</" in source)
        ):
            tsx_language = _get_language("tsx")
            if tsx_language is not None:
                tsx_tree = Parser(tsx_language).parse(source)
                tsx_errors = _collect_error_nodes(tsx_tree.root_node)
                if len(tsx_errors) < len(parse_errors):
                    tree = tsx_tree
                    root = tree.root_node
                    parse_errors = tsx_errors
                    # Both grammar_tag AND language must be reassigned.
                    # grammar_tag is consumed immediately below by
                    # self._get_query(lang, language, grammar_tag), which
                    # appends tsx.scm to the base typescript.scm query.
                    # That append is what supplies the
                    # jsx_opening_element / jsx_self_closing_element captures
                    # that restore JSX component call-site edges.
                    # Reassigning only ``tree`` / ``root`` would fix parse
                    # errors but leave those edges missing — the dead-code
                    # false-positive would remain.
                    grammar_tag = "tsx"
                    language = tsx_language

        query = self._get_query(lang, language, grammar_tag)

        # Execute the compiled query ONCE per file. The five extraction
        # passes below all consume the same capture dicts read-only;
        # re-running ``cursor.matches()`` per pass multiplied the most
        # expensive part of parsing by five.
        matches = _run_query(query, root) if query is not None else []

        symbols = self._extract_symbols(matches, config, file_info, src)
        # Per-language synthetic-symbol pass — recognises source-generator
        # attributes (e.g. CommunityToolkit.Mvvm) and adds the symbols the
        # generator would emit at compile time. No-op for languages
        # without a registered extractor.
        synthetic = extract_synthetic_symbols(root, src, file_info)
        if synthetic:
            existing_ids = {s.id for s in symbols}
            symbols.extend(s for s in synthetic if s.id not in existing_ids)
        imports = self._extract_imports(matches, config, file_info, src)
        calls = self._extract_calls(matches, config, file_info, src, symbols)
        # An SFC instantiates a component by writing its tag in the markup
        # (``<Foo />``), which the blanked TS buffer no longer contains. Mint
        # those call sites from the markup grammar directly — the same way
        # tsx.scm turns ``<Component />`` into a call for React. Returns [] for
        # every non-SFC language.
        calls.extend(component_call_sites(lang, original_source, symbols))
        references = (
            self._extract_references(matches, file_info, src, symbols)
            if lang in _REFERENCE_LANGUAGES
            else []
        )
        heritage = extract_heritage(matches, config, file_info, src)
        exports = self._derive_exports(symbols, config)
        export_aliases = ts_export_aliases(src) if lang in _TS_JS_LANGUAGES else {}
        docstring = extract_module_docstring(root, src, lang)
        type_refs = self._extract_type_refs(matches, src, lang)

        # Same-file reference rescue (Python only): top-level symbols used
        # elsewhere in their own module in a non-call / non-import position
        # (callable passed as an arg, type annotation, decorator, default)
        # carry no graph edge, so the dead-code unused-export pass would flag
        # them. Stamp the referenced names so the analyzer can rescue them.
        local_refs: frozenset[str] = frozenset()
        if lang == "python":
            top_level_names = {s.name for s in symbols if s.name and not s.parent_name}
            local_refs = extract_python_local_refs(src, top_level_names)

        if len(symbols) > _SYMBOL_COUNT_WARN_THRESHOLD:
            log.warning(
                "parser.symbol_bloat",
                path=file_info.path,
                language=lang,
                symbol_count=len(symbols),
                threshold=_SYMBOL_COUNT_WARN_THRESHOLD,
            )

        return ParsedFile(
            file_info=file_info,
            symbols=symbols,
            imports=imports,
            exports=exports,
            export_aliases=export_aliases,
            calls=calls,
            heritage=heritage,
            docstring=docstring,
            parse_errors=parse_errors,
            content_hash=content_hash,
            type_refs=type_refs,
            local_refs=local_refs,
            references=references,
        )

    # ------------------------------------------------------------------
    # Query loading
    # ------------------------------------------------------------------

    def _get_query(
        self, lang: str, language: Language, grammar_tag: str | None = None
    ) -> object | None:
        """Load and cache the compiled tree-sitter Query for *lang*."""
        return _load_compiled_query(lang, grammar_tag)

    # ------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------

    def _extract_symbols(
        self,
        matches: list[dict],
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        seen: set[tuple[int, str]] = set()  # (start_line, name) — dedup decorated dupes
        # Parallel to ``symbols`` (same indices) -- only populated/consumed
        # for Pascal and Objective-C, to dedupe interface-declaration vs.
        # implementation method pairs after the loop. See
        # _dedupe_pascal_interface_symbols / _dedupe_objc_interface_symbols.
        node_types: list[str] = []
        # Also parallel to ``symbols``, Objective-C only: which of @interface /
        # @implementation / @protocol declared each member, so a protocol's
        # method is never deduped against a same-named class method.
        objc_container_kinds: list[str | None] = []

        # Deferred-export names (``export { x }`` / ``export default x``),
        # computed once per file for the TS/JS visibility refinement.
        ts_deferred_exports: frozenset[str] | None = None
        if file_info.language in _TS_JS_LANGUAGES:
            ts_deferred_exports = ts_deferred_export_names(src)

        # tree-sitter-cpp parses ``struct EXPORT Name { ... }`` and
        # ``struct EXPORT Name;`` with ``EXPORT`` as the specifier name and the
        # real type name as a bare declarator. cpp.scm marks those matches so
        # the specifier can keep its class/struct kind while the outer recovery
        # node supplies the real range and nested-member context when present.
        cpp_export_type_defs: dict[int, _CppExportType] = {}
        cpp_export_type_parents: dict[int, str] = {}
        cpp_export_type_capture_ids: set[int] = set()
        cpp_export_macro_names: set[str] = set()
        cpp_export_macro_def_ids: set[int] = set()
        if file_info.language == "cpp":
            cpp_export_matches = [
                capture_dict
                for capture_dict in matches
                if capture_dict.get("symbol.cpp_export_type", [])
            ]
            has_forward_candidate = any(
                capture_dict["symbol.cpp_export_type"][0].type
                in _CPP_EXPORT_FORWARD_DECLARATION_NODES
                for capture_dict in cpp_export_matches
            )
            cpp_macro_facts = (
                _build_cpp_macro_facts(matches, src) if has_forward_candidate else None
            )

            for capture_dict in cpp_export_matches:
                type_nodes = capture_dict.get("symbol.cpp_export_type", [])
                def_nodes = capture_dict.get("symbol.def", [])
                name_nodes = capture_dict.get("symbol.name", [])
                macro_nodes = capture_dict.get("symbol.cpp_export_macro", [])
                if not type_nodes or not def_nodes or not name_nodes:
                    continue
                type_name = _node_text(name_nodes[0], src)
                if not type_name:
                    continue
                capture_node = type_nodes[0]
                is_forward_declaration = capture_node.type in _CPP_EXPORT_FORWARD_DECLARATION_NODES
                range_node = capture_node
                if (
                    is_forward_declaration
                    and capture_node.parent is not None
                    and capture_node.parent.type == "template_declaration"
                ):
                    range_node = capture_node.parent
                active_macro_def: Node | None = None
                if is_forward_declaration:
                    if cpp_macro_facts is None:
                        continue
                    macro_names = {
                        _cpp_normalize_identifier(_node_text(node, src)) for node in macro_nodes
                    } - {""}
                    if len(macro_names) != 1:
                        continue
                    macro_name = next(iter(macro_names))
                    # A bodiless decorated type is syntactically identical to
                    # an ordinary ``struct Tag variable;`` declaration.
                    active_macro_def = cpp_macro_facts.empty_definition_at(macro_name, capture_node)
                    if active_macro_def is None:
                        continue
                else:
                    # Body-form matches are unambiguous. Preserve #1896's
                    # name-based suppression across conditional definitions.
                    cpp_export_macro_names.update(
                        _cpp_normalize_identifier(_node_text(node, src)) for node in macro_nodes
                    )
                cpp_export_type_defs[def_nodes[0].id] = _CppExportType(
                    range_node=range_node,
                    name=type_name,
                    is_forward_declaration=is_forward_declaration,
                )
                cpp_export_type_capture_ids.add(capture_node.id)
                cpp_export_type_parents[capture_node.id] = type_name
                cpp_export_type_parents[range_node.id] = type_name
                if active_macro_def is not None:
                    cpp_export_macro_def_ids.add(active_macro_def.id)

        cpp_export_type_parent_ids = frozenset(cpp_export_type_parents)

        for capture_dict in matches:
            def_nodes = capture_dict.get("symbol.def", [])
            name_nodes = capture_dict.get("symbol.name", [])
            params_nodes = capture_dict.get("symbol.params", [])
            modifier_nodes = capture_dict.get("symbol.modifiers", [])
            receiver_nodes = capture_dict.get("symbol.receiver", [])
            captured_export_type_nodes = capture_dict.get("symbol.cpp_export_type", [])

            if (
                captured_export_type_nodes
                and captured_export_type_nodes[0].id not in cpp_export_type_capture_ids
            ):
                # The query also sees ordinary ``struct Tag variable;`` forms;
                # discard only the unsupported recovery match.
                continue

            if not def_nodes or not name_nodes:
                continue

            def_node = def_nodes[0]
            name = _node_text(name_nodes[0], src)
            if not name:
                continue

            if file_info.language == "elixir":
                # A `def` inside `quote do ... end` is macro body, not a
                # definition of the module that writes it.
                if _elixir_is_template_definition(def_node, src):
                    continue
                # `defimpl Proto, for: Type` is named for the module the
                # compiler generates, not for the protocol alone.
                name = _elixir_symbol_name(def_node, name, src)

            elif file_info.language == "objectivec":
                # `typedef NS_ENUM(NSInteger, Kind) { ... }` has no grammar
                # rule, so only its enum *cases* survive as declarators and
                # each would become a symbol named as though it were the type.
                if _objc_is_macro_enum(def_node, src):
                    continue
                # A method is named by its whole selector
                # (`initWithName:age:`), which no single node holds, and a
                # category by the class it extends plus its own name.
                name = _objc_symbol_name(def_node, name, src)

            export_type = cpp_export_type_defs.get(def_node.id)
            if export_type is not None and name != export_type.name:
                # The ordinary struct/class query sees the same specifier, but
                # tree-sitter calls the export macro its name. Keep only the
                # dedicated match whose name is the outer declarator.
                continue

            if def_node.type == "preproc_def" and (
                _cpp_normalize_identifier(name) in cpp_export_macro_names
                or def_node.id in cpp_export_macro_def_ids
            ):
                # Body-form macros are suppressed by name as before #1901;
                # ambiguous forward declarations suppress only the exact
                # active definition that made recovery safe.
                continue

            start_line = def_node.start_point[0] + 1
            if export_type is not None:
                start_line = export_type.range_node.start_point[0] + 1
            dedup_key = (start_line, name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Kind from node type
            node_type = def_node.type
            kind = config.symbol_node_types.get(node_type)
            if kind is None:
                continue

            # Skip symbols nested inside another function/method body. The
            # Tree-sitter query is recursive, so helpers defined inside a
            # React component or an async orchestrator method get hoisted
            # to the top-level symbol list and read as unused public
            # exports. Filtering by callable ancestor restricts extraction
            # to module-top-level + class-body members. Class bodies don't
            # match (``class_definition`` is not callable), so methods are
            # preserved. Module-anchored node types skip the check: their
            # .scm patterns only match at module/program level, and a TS
            # variable_declarator's parent (lexical_declaration → "function")
            # would otherwise read as a callable ancestor.
            if node_type not in _MODULE_ANCHORED_NODE_TYPES and _has_callable_ancestor(
                def_node, config.symbol_node_types, cpp_export_type_parent_ids
            ):
                continue

            # F#: a ``let`` nested in another binding's body has the same node
            # shape as a top-level one, so the ancestor filter above cannot
            # see it -- what it captures is the binding's left-hand side, and
            # a nested binding's left-hand side has no callable ancestor
            # either. A ``let`` inside a type body is a field and stays.
            if file_info.language == "fsharp" and node_type in (
                "function_declaration_left",
                "value_declaration_left",
            ):
                if _fsharp_binding_is_nested(def_node):
                    continue
                # A value binding that carries parameter patterns beside its
                # name is a function the grammar reparsed as a value because
                # of its return-type annotation.
                if node_type == "value_declaration_left" and _fsharp_binding_has_params(
                    def_node
                ):
                    kind = "function"

            # Refine "struct" kind for Go type_spec (check if struct or interface body)
            if kind == "struct" and config.parent_extraction == "receiver":
                kind = refine_go_type_kind(def_node, src)

            # Refine "class" kind for Kotlin (interface / enum class share class_declaration)
            if (
                kind == "class"
                and file_info.language == "kotlin"
                and def_node.type == "class_declaration"
            ):
                kind = refine_kotlin_class_kind(def_node)

            # Refine "class" kind for Pascal (declType wraps class / record /
            # object / interface / class-helper / enum / set / array / alias
            # in one node shape -- see the spec docstring and
            # refine_pascal_type_kind's own docstring for the disambiguation).
            if kind == "class" and file_info.language == "pascal" and def_node.type == "declType":
                kind = refine_pascal_type_kind(def_node)

            # Elixir: every definition is a ``call``, so the node type cannot
            # name the kind and the config maps it to a deliberately
            # non-callable placeholder (see refine_elixir_call_kind). The
            # keyword in the call's target is what actually says what was
            # defined.
            if file_info.language == "elixir" and def_node.type == "call":
                kind = refine_elixir_call_kind(def_node, src)

            # F# writes a class, a struct and an interface with the same
            # ``anon_type_defn`` node; only the body says which it is.
            if (
                kind == "class"
                and file_info.language == "fsharp"
                and def_node.type == "anon_type_defn"
            ):
                kind = refine_fsharp_type_kind(def_node)

            # Dart: a function is a ``function_signature`` whose BODY is a
            # sibling ``function_body`` node (members wrap the signature in
            # ``method_signature``). Two consequences the generic path can't
            # see: local functions nested inside another function's body have
            # no callable *ancestor* (the enclosing signature is a sibling),
            # so filter them here; and the symbol's line range must extend to
            # the trailing body sibling or call-site attribution stops at the
            # signature line.
            end_line = def_node.end_point[0] + 1
            if export_type is not None:
                end_line = export_type.range_node.end_point[0] + 1
            # F#: the captured node is the binding's left-hand side, so its
            # own span stops at the parameter list. Extend it over the body
            # (and any return-type annotation between the two) or every call
            # in the body is attributed to whatever encloses the binding.
            if file_info.language == "fsharp" and node_type in (
                "function_declaration_left",
                "value_declaration_left",
            ):
                end_line = _fsharp_binding_end_line(def_node)
            if file_info.language == "dart" and node_type in (
                "function_signature",
                "getter_signature",
                "setter_signature",
            ):
                ancestor = def_node.parent
                is_local = False
                while ancestor is not None:
                    if ancestor.type in ("function_body", "function_expression"):
                        is_local = True
                        break
                    ancestor = ancestor.parent
                if is_local:
                    continue
                anchor = def_node
                if def_node.parent is not None and def_node.parent.type == "method_signature":
                    anchor = def_node.parent
                body_sibling = anchor.next_named_sibling
                if body_sibling is not None and body_sibling.type == "function_body":
                    end_line = body_sibling.end_point[0] + 1

            # Refine module-level assignments: SCREAMING_CASE names are
            # constants by convention; the rest are module variables
            # (singletons like ``app = FastAPI()``, registries, caches).
            # ``str.isupper()`` requires at least one cased char, so names
            # with no letters (``_``, ``__all__``) fall to "variable" rather
            # than being mislabelled constants by ``name == name.upper()``.
            if node_type in _MODULE_ANCHORED_NODE_TYPES:
                # TS/JS: the symbol query admits call_expression values so
                # forwardRef / memo / onCall / styled() bindings exist at all,
                # which also lets `const svc = require('./svc')` through. Those
                # bind a module and are already imports — drop them here rather
                # than in the query, which cannot see past the await / paren /
                # non-null / member-pick shells.
                if file_info.language in _TS_JS_LANGUAGES and declarator_value_is_module_ref(
                    def_node, src
                ):
                    continue
                # A declarator whose value is structurally callable is not
                # data, whatever its name looks like: `const C =
                # forwardRef(fn)` and `const f = function(){}` are a component
                # and a function. Naming decides only for the rest, which is
                # what it was ever able to answer.
                callable_kind = (
                    declarator_binds_callable(def_node, src)
                    if file_info.language in _TS_JS_LANGUAGES
                    else None
                )
                kind = callable_kind or ("constant" if name.isupper() else "variable")

            # Params signature text
            params_text = _node_text(params_nodes[0], src) if params_nodes else ""

            # Visibility
            modifier_texts = [_node_text(m, src) for m in modifier_nodes]

            # Rust: outer attributes (#[...]) are preceding siblings of the item
            rust_attrs: list[str] = []
            if file_info.language == "rust" and def_node.parent is not None:
                siblings = def_node.parent.children
                for j, sib in enumerate(siblings):
                    if sib.id == def_node.id:
                        k = j - 1
                        while k >= 0 and siblings[k].type == "attribute_item":
                            attr_text = _node_text(siblings[k], src).strip()
                            # Strip #[ and ] to get the inner attribute text
                            if attr_text.startswith("#[") and attr_text.endswith("]"):
                                rust_attrs.append(attr_text[2:-1])
                            k -= 1
                        break

            # C#: [Obsolete] / [System.Obsolete] are ``attribute_list`` nodes.
            # In tree-sitter-c-sharp the attribute_list is child[0] of the
            # declaration node itself (method_declaration, class_declaration, etc.),
            # NOT a preceding sibling in the class body. Iterate def_node.children
            # and collect attribute_list nodes until the first non-attribute child.
            # Strip the outer [ ] so the inner content matches the same
            # _DEPRECATED_DECORATOR_BASES the analyzer uses for every other lang.
            csharp_attrs: list[str] = []
            if file_info.language == "csharp":
                for child in def_node.children:
                    if child.type != "attribute_list":
                        break
                    attr_text = _node_text(child, src).strip()
                    # "[Obsolete]" → "Obsolete"
                    if attr_text.startswith("[") and attr_text.endswith("]"):
                        csharp_attrs.append(attr_text[1:-1])

            # C/C++: [[deprecated]] / [[deprecated("reason")]] are
            # ``attribute_declaration`` nodes. In tree-sitter-cpp the
            # attribute_declaration is child[0] of function_definition itself
            # (NOT a preceding sibling at translation_unit level). Iterate
            # def_node.children and collect attribute_declaration nodes until
            # the first non-attribute child.
            # Strip the outer [[ ]] so the inner content lands in the same
            # checker as the Rust and C# forms.
            cpp_attrs: list[str] = []
            if file_info.language in ("cpp", "c"):
                for child in def_node.children:
                    if child.type != "attribute_declaration":
                        break
                    attr_text = _node_text(child, src).strip()
                    # "[[deprecated]]" → "deprecated"
                    if attr_text.startswith("[[") and attr_text.endswith("]]"):
                        cpp_attrs.append(attr_text[2:-2])

            visibility = config.visibility_fn(name, modifier_texts)
            is_exported_symbol = False
            # C/C++ visibility is dictated by AST context (access
            # specifiers / storage class / export attributes), not by
            # modifier text. Refine after the generic fn ran.
            if file_info.language in ("cpp", "c"):
                visibility, is_exported_symbol = refine_cpp_visibility(def_node, visibility, src)
            # C#: an unmodified declaration's default depends on what encloses
            # it, which the modifier-text fn cannot see.
            elif file_info.language == "csharp":
                visibility = refine_csharp_visibility(def_node, visibility)
            # TS/JS: a top-level declaration is only public when exported —
            # inline, via ``export { x }`` lists, or ``export default x``.
            elif file_info.language in _TS_JS_LANGUAGES:
                visibility = refine_ts_visibility(def_node, visibility, name, ts_deferred_exports)

            # Parent class detection
            parent_name = self._find_parent(def_node, config, receiver_nodes, src)

            if parent_name is None and file_info.language == "cpp" and export_type is None:
                parent_name = _cpp_export_macro_parent(def_node, cpp_export_type_parents)

            # Dart mixin_declaration exposes no ``name`` field, so
            # ``_find_parent``'s field lookup misses mixin members.
            if parent_name is None and file_info.language == "dart":
                ancestor = def_node.parent
                while ancestor is not None:
                    if ancestor.type == "mixin_declaration":
                        ident = next((c for c in ancestor.children if c.type == "identifier"), None)
                        if ident is not None:
                            parent_name = _node_text(ident, src)
                        break
                    if ancestor.type in config.parent_class_types:
                        break
                    ancestor = ancestor.parent

            # F#: no type node carries a ``name`` field -- the name hangs off
            # a ``type_name`` child -- so the generic walk finds the ancestor
            # and then reads nothing off it.
            if parent_name is None and file_info.language == "fsharp":
                parent_name = _fsharp_parent_name(def_node, src)

            # C/C++ qualified definitions: ``void Foo::method() { … }``
            # carries the class as the scope of a ``qualified_identifier``
            # parent of the name node. Without this resolution, every
            # ``Class::method`` lands as a free function and bloats the
            # unused_export pass with thousands of method symbols.
            if parent_name is None and file_info.language in ("cpp", "c") and name_nodes:
                parent_name = _qualified_cpp_parent(name_nodes[0], src)

            # Pascal out-of-line implementation: ``function TFoo.Bar(...);``
            # -- the ``defProc`` node lives in the unit's implementation
            # section, outside the class's ``declType`` body declared in the
            # interface section, so nesting-based ``_find_parent`` above
            # can't see it. The qualifying class lives beside the captured
            # name in the ``genericDot`` header instead.
            if parent_name is None and file_info.language == "pascal" and name_nodes:
                parent_name = _qualified_pascal_parent(name_nodes[0], src)

            # Elixir: the enclosing ``defmodule`` is a ``call`` with no
            # ``name`` field for the generic nesting walk to read, so the
            # module name has to be dug out of its first argument.
            if parent_name is None and file_info.language == "elixir":
                parent_name = _elixir_module_parent(def_node, src)

            # Objective-C: an @interface / @implementation / @protocol names
            # itself with a bare first identifier and no ``name`` field, so
            # the nesting walk above finds the right ancestor and reads
            # nothing off it.
            if parent_name is None and file_info.language == "objectivec":
                parent_name = _objc_container_parent(def_node, config.parent_class_types, src)

            # A ``field_declaration`` cannot occur outside a class body, so a
            # missing parent means the class did not parse. Grammar recovery,
            # not a member function.
            if node_type == "function_declarator" and parent_name is None:
                continue

            # Upgrade function → method when a parent class is detected.
            # F#: a nested module is a parent too (for id uniqueness), but it
            # is not a type, so a `let` inside one stays a function.
            if parent_name and kind == "function" and (
                file_info.language != "fsharp" or _fsharp_parent_is_type(def_node)
            ):
                kind = "method"

            # Build signature
            signature = build_signature(node_type, name, params_text, def_node, src)

            # Docstring
            docstring = extract_symbol_docstring(def_node, src, file_info.language)

            # Async detection
            is_async = _is_async_node(def_node, src)

            sym_id = (
                f"{file_info.path}::{parent_name}::{name}"
                if parent_name
                else f"{file_info.path}::{name}"
            )
            qualified = _build_qualified_name(file_info.path, parent_name, name)

            symbols.append(
                Symbol(
                    id=sym_id,
                    name=name,
                    qualified_name=qualified,
                    kind=kind,  # type: ignore[arg-type]
                    signature=signature,
                    start_line=start_line,
                    end_line=end_line,
                    docstring=docstring,
                    decorators=(
                        [m for m in modifier_texts if m.startswith("@")]
                        + rust_attrs
                        + csharp_attrs
                        + cpp_attrs
                    ),
                    visibility=visibility,  # type: ignore[arg-type]
                    is_async=is_async,
                    language=file_info.language,
                    parent_name=parent_name,
                    is_exported_symbol=is_exported_symbol,
                    is_declaration=(
                        node_type in config.declaration_node_types
                        or (export_type is not None and export_type.is_forward_declaration)
                        or (
                            export_type is None
                            and _is_bodiless_cpp_type(file_info.language, node_type, def_node)
                        )
                    ),
                )
            )
            node_types.append(node_type)
            if file_info.language == "objectivec":
                container = _objc_container_node(def_node, config.parent_class_types)
                objc_container_kinds.append(container.type if container else None)

        if file_info.language == "pascal":
            symbols = _dedupe_pascal_interface_symbols(symbols, node_types)

        # A .m file routinely declares its private methods in a class
        # extension and defines them below in the @implementation, which
        # builds each symbol id twice in one file.
        if file_info.language == "objectivec":
            symbols = _dedupe_objc_interface_symbols(symbols, node_types, objc_container_kinds)

        return symbols

    def _find_parent(
        self,
        def_node: Node,
        config: LanguageConfig,
        receiver_nodes: list[Node],
        src: str,
    ) -> str | None:
        """Determine the parent class/type for a symbol."""
        if config.parent_extraction == "receiver":
            # Go: extract type name from receiver parameter list
            if receiver_nodes:
                return extract_go_receiver_type(_node_text(receiver_nodes[0], src))
            return None

        if config.parent_extraction in ("nesting", "impl"):
            # Walk up the AST to find a class/impl ancestor
            ancestor = def_node.parent
            while ancestor is not None:
                if ancestor.type in config.parent_class_types:
                    name_node = ancestor.child_by_field_name("name") or (
                        ancestor.child_by_field_name("type")  # Rust impl_item
                    )
                    if name_node:
                        # For Rust impl blocks with generic types (e.g. impl<T> Foo<T>),
                        # extract only the base type name, not the full generic signature.
                        if name_node.type == "generic_type":
                            inner = name_node.child_by_field_name("type")
                            if inner and inner.type == "type_identifier":
                                name_node = inner
                        elif name_node.type == "scoped_type_identifier":
                            inner = name_node.child_by_field_name("name")
                            if inner and inner.type == "type_identifier":
                                name_node = inner
                        return _node_text(name_node, src)
                ancestor = ancestor.parent
            return None

        return None  # "none" mode

    # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def _extract_imports(
        self,
        matches: list[dict],
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
    ) -> list[Import]:
        imports: list[Import] = []
        seen_raws: set[str] = set()
        seen_pascal_units: set[str] = set()
        seen_elixir_modules: set[str] = set()

        for capture_dict in matches:
            stmt_nodes = capture_dict.get("import.statement", [])
            module_nodes = capture_dict.get("import.module", [])

            if not stmt_nodes or not module_nodes:
                continue

            stmt_node = stmt_nodes[0]

            # Pascal: `uses UnitA, UnitB, Ns.UnitC;` -- pascal.scm's
            # unquantified pattern (see that file's comment on why) fires
            # once PER moduleName, so a 3-unit clause arrives as 3 separate
            # matches sharing one @import.statement span, each carrying a
            # single-element ``module_nodes``. Handled before the
            # ``seen_raws`` dedup below: that guard exists to skip a
            # statement re-matched by an *overlapping* pattern (the normal
            # case elsewhere), but here every match legitimately carries a
            # different unit despite the identical raw statement text --
            # dedup-by-raw would keep only the first and silently drop the
            # rest, which is exactly the bug this branch fixes.
            #
            # Deduped separately by unit name (case-insensitive -- Pascal
            # identifiers are): a unit named in both the ``interface`` and
            # ``implementation`` ``uses`` clauses of the same file is a
            # single logical dependency and must not become two Import
            # entries for it.
            if file_info.language == "pascal":
                raw = _node_text(stmt_node, src).strip()
                unit_name = _node_text(module_nodes[0], src).strip()
                if unit_name and unit_name.lower() not in seen_pascal_units:
                    seen_pascal_units.add(unit_name.lower())
                    imports.append(
                        Import(
                            raw_statement=raw,
                            module_path=unit_name,
                            # ``uses UnitA;`` exposes UnitA's ENTIRE public
                            # interface section, unlike Python/JS's
                            # name-scoped `from x import y` -- Pascal has no
                            # per-symbol import syntax to name a specific
                            # one. ``imported_names=[]`` (empty, not
                            # wildcard) meant dead_code/analyzer.py's
                            # `sym_name in imported_names` / `"*" in
                            # imported_names` file-level unused-export
                            # rescue could structurally never fire for
                            # Pascal -- every public symbol fell straight
                            # through to the (now Phase-1-fixed, but still
                            # best-effort) symbol-level call/type_use
                            # rescue instead. ``["*"]`` is this codebase's
                            # existing wildcard-import sentinel (see the
                            # Python `import *` and CJS re-export branches
                            # of this function) and is the semantically
                            # correct value here, not a workaround: it says
                            # exactly what a Pascal `uses` clause does.
                            imported_names=["*"],
                            is_relative=False,
                            resolved_file=None,
                            bindings=[],
                            is_reexport=False,
                        )
                    )
                continue

            # Elixir: `alias Foo.{Bar, Baz}` names two modules in one
            # statement, so dedup by raw statement text (below) would drop all
            # but the first. Deduped by module path instead, which is also
            # what makes a module aliased twice in one file one dependency.
            if file_info.language == "elixir":
                raw = _node_text(stmt_node, src).split("\n", 1)[0].strip()
                directive_node = stmt_node.child_by_field_name("target")
                directive = _node_text(directive_node, src).strip() if directive_node else ""
                for module_path in elixir_import_modules(module_nodes[0], src):
                    if module_path in seen_elixir_modules:
                        continue
                    seen_elixir_modules.add(module_path)
                    imports.append(
                        Import(
                            raw_statement=raw,
                            # `import Foo` pulls in every public function;
                            # alias/require/use bind the module itself, which
                            # is the same wildcard sentinel the regex tier
                            # writes for this language.
                            module_path=module_path,
                            imported_names=["*"] if directive == "import" else [],
                            is_relative=False,
                            resolved_file=None,
                            bindings=[],
                            is_reexport=False,
                        )
                    )
                continue

            # F#: `open Foo.Bar` binds every public name in Foo.Bar, which is
            # the wildcard sentinel this codebase already uses -- and which
            # the call resolver reads to decide which imports a bare name may
            # be looked up in (F# bare names are lexically scoped).
            # `open type Foo.Bar.Baz` binds a TYPE's static members instead,
            # so the module the file depends on is the path holding the type.
            if file_info.language == "fsharp":
                raw = _node_text(stmt_node, src).strip()
                if raw in seen_raws:
                    continue
                seen_raws.add(raw)
                module_path = _node_text(module_nodes[0], src).strip()
                if not module_path:
                    continue
                names: list[str] = ["*"]
                if any(child.type == "type" for child in stmt_node.children):
                    head, _, type_name = module_path.rpartition(".")
                    if head:
                        module_path, names = head, [type_name]
                    else:
                        names = []
                imports.append(
                    Import(
                        raw_statement=raw,
                        module_path=module_path,
                        imported_names=names,
                        is_relative=False,
                        resolved_file=None,
                        bindings=[],
                        is_reexport=False,
                    )
                )
                continue

            raw = _node_text(stmt_node, src).strip()
            if raw in seen_raws:
                continue
            seen_raws.add(raw)

            module_text = _node_text(module_nodes[0], src).strip().strip("\"'` ")
            if not module_text:
                continue

            # Scala: the query's ``(identifier)`` capture is only the FIRST
            # path segment (``import com.foo.Bar`` arrived as ``com``), and
            # one declaration can hold several clauses, brace selectors,
            # renames, and wildcards. Reconstruct full dotted paths and emit
            # one Import per selected name.
            if file_info.language == "scala" and stmt_node.type == "import_declaration":
                from .extractors.bindings.scala import expand_scala_import_clauses
                from .models import NamedBinding

                for clause_path, clause_names in expand_scala_import_clauses(stmt_node, src):
                    local = clause_names[0]
                    exported = None if local == "*" else clause_path.rsplit(".", 1)[-1]
                    imports.append(
                        Import(
                            raw_statement=raw,
                            module_path=clause_path,
                            imported_names=clause_names,
                            is_relative=False,
                            resolved_file=None,
                            bindings=[
                                NamedBinding(
                                    local_name=local,
                                    exported_name=exported,
                                    source_file=None,
                                )
                            ],
                            is_reexport=False,
                        )
                    )
                continue

            # Dart: URIs are relative unless schemed (``package:``/``dart:``),
            # ``export`` directives are barrel re-exports, and the legacy
            # dotted ``part of library.name;`` form resolves through the
            # library-name index (the ``library:`` prefix is the resolver's
            # contract, shared with the lightweight regex tier).
            if file_info.language == "dart":
                module_path = module_text
                if module_nodes[0].type == "dotted_identifier_list":
                    module_path = f"library:{module_text}"
                imported_names, bindings = extract_import_bindings(
                    stmt_node, src, file_info.language
                )
                imports.append(
                    Import(
                        raw_statement=raw,
                        module_path=module_path,
                        imported_names=imported_names,
                        is_relative=not module_path.startswith(("package:", "dart:", "library:")),
                        resolved_file=None,
                        bindings=bindings,
                        is_reexport=stmt_node.type == "library_export",
                    )
                )
                continue

            # Dynamic ESM import: ``import('./mod')``. The query captures the
            # call_expression, which would otherwise fall into the CommonJS
            # branch below and be dropped on the floor — a dynamic import
            # holds no ``require()`` for ``collect_cjs_requires`` to find.
            # The construct binds a module namespace at runtime, so record a
            # wildcard rather than a static name.  Downstream unused-export
            # analysis treats ``*`` as namespace consumption and therefore
            # keeps the target's exports live without a broad analyzer
            # exemption.
            if (
                file_info.language in _TS_JS_LANGUAGES
                and stmt_node.type == "call_expression"
                and (_fn := stmt_node.child_by_field_name("function")) is not None
                and _fn.type == "import"
            ):
                imports.append(
                    Import(
                        raw_statement=raw,
                        module_path=module_text,
                        imported_names=["*"],
                        is_relative=module_text.startswith("."),
                        resolved_file=None,
                        bindings=[],
                        is_reexport=False,
                    )
                )
                continue

            # CommonJS assignment / Object.assign shapes: the query captures
            # the outer statement once; walk it for every require() it
            # contains (a hub like Object.assign(module.exports,
            # require('./a'), require('./b')) is several imports) and mark
            # module.exports/exports shapes as re-exports so barrel logic
            # treats CJS hubs like ESM barrels.
            if file_info.language in ("javascript", "typescript") and stmt_node.type in (
                "assignment_expression",
                "call_expression",
            ):
                from .extractors.bindings.ts_js import (
                    cjs_statement_is_reexport,
                    collect_cjs_requires,
                )

                cjs_reexport = cjs_statement_is_reexport(stmt_node, src)
                for cjs_module in collect_cjs_requires(stmt_node, src):
                    imports.append(
                        Import(
                            raw_statement=raw,
                            module_path=cjs_module,
                            imported_names=["*"] if cjs_reexport else [],
                            is_relative=cjs_module.startswith("."),
                            resolved_file=None,
                            bindings=[],
                            is_reexport=cjs_reexport,
                        )
                    )
                continue

            # Rust #[path = "..."] attribute overrides module file location.
            # In tree-sitter-rust, outer attributes are preceding siblings of
            # the item, not children.
            if file_info.language == "rust" and stmt_node.type == "mod_item":
                parent = stmt_node.parent
                if parent is not None:
                    siblings = parent.children
                    for j, sib in enumerate(siblings):
                        if sib.id == stmt_node.id:
                            # Walk backward through preceding attribute_item siblings
                            k = j - 1
                            while k >= 0 and siblings[k].type == "attribute_item":
                                attr_text = _node_text(siblings[k], src)
                                path_match = re.search(r'path\s*=\s*"([^"]+)"', attr_text)
                                if path_match:
                                    module_text = path_match.group(1)
                                    break
                                k -= 1
                            break

            # JVM wildcard imports: the grammar query captures the scoped
            # identifier only — the trailing ``*`` is a sibling node, so
            # ``import com.foo.*`` arrives as ``com.foo`` and the resolvers'
            # package fan-out branch can never fire. Restore it from the
            # raw statement text.
            if file_info.language in ("java", "kotlin") and not module_text.endswith("*"):
                stmt_text = raw.rstrip().rstrip(";").rstrip()
                if stmt_text.endswith(".*"):
                    module_text += ".*"

            # Language-specific import name + binding extraction
            imported_names, bindings = extract_import_bindings(stmt_node, src, file_info.language)
            is_relative = (
                module_text.startswith(".")
                or module_text.startswith("./")
                or module_text.startswith(("self::", "super::", "crate::"))
            )

            is_reexport = False
            if file_info.language == "rust" and stmt_node.type == "use_declaration":
                for child in stmt_node.children:
                    if child.type == "visibility_modifier":
                        is_reexport = True
                        break
            # Swift: ``@_exported import FooKit`` re-exports the module —
            # importers of THIS module see FooKit's symbols too.
            elif file_info.language == "swift" and raw.startswith("@_exported"):
                is_reexport = True

            imports.append(
                Import(
                    raw_statement=raw,
                    module_path=module_text,
                    imported_names=imported_names,
                    is_relative=is_relative,
                    resolved_file=None,
                    bindings=bindings,
                    is_reexport=is_reexport,
                )
            )

        if file_info.language == "python":
            imports = expand_bare_relative_imports(imports)

        return imports

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_calls(
        self,
        matches: list[dict],
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
        symbols: list[Symbol],
    ) -> list[CallSite]:
        """Extract function/method call sites from the AST."""
        from .language_data import get_builtin_calls

        _call_builtins = get_builtin_calls(file_info.language)

        symbol_ranges = sorted(
            [(s.start_line, s.end_line, s.id) for s in symbols],
            key=lambda t: (t[0], -t[1]),
        )

        calls: list[CallSite] = []

        for capture_dict in matches:
            site_nodes = capture_dict.get("call.site", [])
            target_nodes = capture_dict.get("call.target", [])
            arg_nodes = capture_dict.get("call.arguments", [])
            receiver_nodes = capture_dict.get("call.receiver", [])
            receiver_call_nodes = capture_dict.get("call.receiver_call", [])
            scope_nodes = capture_dict.get("call.scope", [])

            if not site_nodes or not target_nodes:
                continue

            site_node = site_nodes[0]
            target_name = _node_text(target_nodes[0], src).strip()
            if not target_name:
                continue

            # Objective-C: a message send binds one `method:` child per
            # keyword, so `[view setTitle:t forState:s]` matches the one
            # query pattern twice. Join the whole selector on the first match
            # so it can meet the symbol side, and drop the rest.
            if file_info.language == "objectivec":
                if site_node.type == "message_expression":
                    joined = _objc_message_selector(site_node, target_nodes[0], src)
                    if joined is None:
                        continue
                    target_name = joined
                # A block held in a parameter or a local is invoked with C call
                # syntax, so `completionBlock(hit)` is indistinguishable from a
                # call to a C function by name alone. Left in, the resolver
                # binds it to whatever same-named @property the repo holds.
                elif not receiver_nodes and _objc_call_is_block_variable(
                    site_node, target_name, src
                ):
                    continue

            if target_name in _call_builtins:
                continue

            # Elixir: a definition head (`add(a, b)` in `def add(a, b)`) and a
            # module attribute (`@doc "..."`) are both ``call`` nodes, and no
            # query predicate can see the parent that tells them apart. Left
            # in, every function in the repo would call itself.
            if file_info.language == "elixir" and _elixir_call_is_definitional(site_node, src):
                continue

            line = site_node.start_point[0] + 1
            receiver_name = _node_text(receiver_nodes[0], src).strip() if receiver_nodes else None
            if receiver_name and file_info.language == "php":
                receiver_name = _normalize_php_receiver(receiver_name)
            # F#: a dotted static path (``Path.Combine(a, b)``) collapses into
            # one identifier node in this grammar -- there is no dot node to
            # capture a receiver from -- so the split happens on the text.
            # After the builtin check above, so a name is filtered as written.
            if file_info.language == "fsharp" and receiver_name is None and "." in target_name:
                receiver_name, _, target_name = target_name.rpartition(".")
                receiver_name = receiver_name.strip()
                target_name = target_name.strip()
                if not target_name:
                    continue
            receiver_call = (
                _call_receiver_from_node(receiver_call_nodes[0], src)
                if receiver_call_nodes
                else None
            )
            scope_name = _node_text(scope_nodes[0], src).strip() if scope_nodes else None

            arg_count: int | None = None
            if arg_nodes:
                arg_node = arg_nodes[0]
                arg_count = _count_arguments(arg_node)

            supplied_props: frozenset[str] | None = None
            if site_node.type in ("jsx_self_closing_element", "jsx_opening_element"):
                props_set: set[str] = set()
                has_spread = False
                for child in site_node.children:
                    if child.type == "jsx_attribute":
                        for sub in child.children:
                            if sub.type in ("property_identifier", "identifier"):
                                props_set.add(_node_text(sub, src))
                                break
                    elif child.type == "jsx_expression":
                        for sub in child.children:
                            if sub.type == "spread_element":
                                has_spread = True
                                break
                if not has_spread:
                    supplied_props = frozenset(props_set)

            caller_id = _find_enclosing_symbol(line, symbol_ranges)

            calls.append(
                CallSite(
                    target_name=target_name,
                    receiver_name=receiver_name,
                    caller_symbol_id=caller_id,
                    line=line,
                    argument_count=arg_count,
                    receiver_call=receiver_call,
                    scope_name=scope_name,
                    edge_type=(
                        "references"
                        if site_node.type in config.reference_call_node_types
                        else "calls"
                    ),
                    supplied_props=supplied_props,
                )
            )

        deduplicated: dict[tuple[int, str, str | None], CallSite] = {}
        for call in calls:
            key = (call.line, call.target_name, call.receiver_name)
            existing = deduplicated.get(key)
            # Two of the three scoped-call patterns can match the same two-part call
            # (one keeps the qualifier, one does not) and both dedup to this key, so
            # the richer record has to win whichever order they arrive in. The
            # three-part pattern (ns::util::fn()) never collides here, it's the
            # only pattern that can match a nested qualified_identifier, so it
            # always lands as a fresh key.
            if (
                existing is None
                or (existing.receiver_call is None and call.receiver_call is not None)
                or (existing.scope_name is None and call.scope_name is not None)
            ):
                deduplicated[key] = call
        return list(deduplicated.values())

    def _extract_references(
        self,
        matches: list[dict],
        file_info: FileInfo,
        src: str,
        symbols: list[Symbol],
    ) -> list[CallSite]:
        """Extract sites that name a function without calling it.

        Six shapes carry a function by name and never call it where a parser
        can see: a dispatch-table entry, a callback field, an argument to a
        registration macro, a func value in argument position, a struct-field
        initialiser, and a ``::`` callable reference. Each leaves the named
        function with no inbound edge, which read as a ``safe_to_delete``
        unused export and took out whole handler and interop layers (#1602).

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
          ``{data, size}`` names parameters. Measured on fmt, admitting those
          turned ``data``, ``size``, ``begin``, ``end``, ``capacity`` and
          ``buffer`` into edges, every one of them wrong.

        Whether the name denotes a function at all is settled later, at
        resolution, where the symbol kind is known.
        """
        from .language_data import get_builtin_calls

        builtins = get_builtin_calls(file_info.language)

        symbol_ranges = sorted(
            [(s.start_line, s.end_line, s.id) for s in symbols],
            key=lambda t: (t[0], -t[1]),
        )
        callable_ids = {s.id for s in symbols if s.kind in ("function", "method")}

        references: list[CallSite] = []
        seen: set[tuple[int, str, str | None]] = set()

        for capture_dict in matches:
            plain_nodes = capture_dict.get("reference.name", [])
            table_nodes = capture_dict.get("reference.table", [])
            if not plain_nodes and not table_nodes:
                continue

            receiver_nodes = capture_dict.get("reference.receiver", [])
            receiver = (
                _node_text(receiver_nodes[0], src).strip() if receiver_nodes else None
            ) or None

            via_nodes = capture_dict.get("reference.via", [])
            if via_nodes:
                via = _node_text(via_nodes[0], src).strip()
                if not via or not via.isupper():
                    continue
                arg_list = plain_nodes[0].parent if plain_nodes else None
                if arg_list is None or len(arg_list.named_children) != 1:
                    continue

            candidates = [(node, False) for node in plain_nodes]
            candidates += [(node, True) for node in table_nodes]
            for name_node, is_table in candidates:
                name = _node_text(name_node, src).strip()
                if not name or name in builtins:
                    continue
                line = name_node.start_point[0] + 1
                enclosing = _find_enclosing_symbol(line, symbol_ranges)
                if is_table and enclosing in callable_ids:
                    continue
                if (line, name, receiver) in seen:
                    continue
                seen.add((line, name, receiver))
                references.append(
                    CallSite(
                        target_name=name,
                        receiver_name=receiver,
                        caller_symbol_id=enclosing,
                        line=line,
                        argument_count=None,
                    )
                )

        return references

    # ------------------------------------------------------------------
    # Export derivation
    # ------------------------------------------------------------------

    def _derive_exports(
        self,
        symbols: list[Symbol],
        config: LanguageConfig,
    ) -> list[str]:
        """Derive the list of exported names from parsed symbols.

        Note on TS/JS: export visibility is resolved upstream during symbol
        extraction by ``refine_ts_visibility()``, which demotes non-exported
        declarations to private. This expression relies on that classification to
        filter top-level public symbols accurately for TS/JS.
        """
        return [s.name for s in symbols if s.visibility == "public" and s.parent_name is None]

    # ------------------------------------------------------------------
    # Type reference extraction (non-import positions)
    # ------------------------------------------------------------------

    def _extract_type_refs(
        self,
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
                line = type_node.start_point[0] + 1
                key = (head, line)
                if key in seen:
                    continue
                seen.add(key)
                origin = _classify_param_origin(type_node)
                refs.append(TypeReference(type_name=head, line=line, origin=origin))

        return refs


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

_DEFAULT_PARSER: ASTParser | None = None


def parse_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Module-level convenience: parse a file using the default ASTParser."""
    global _DEFAULT_PARSER
    if _DEFAULT_PARSER is None:
        _DEFAULT_PARSER = ASTParser()
    return _DEFAULT_PARSER.parse_file(file_info, source)
