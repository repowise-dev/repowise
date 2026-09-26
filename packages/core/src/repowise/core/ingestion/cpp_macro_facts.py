"""C/C++ macro state facts: which object macros are provably empty at a source position."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

from tree_sitter import Node

from .extractors import node_text as _node_text

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
