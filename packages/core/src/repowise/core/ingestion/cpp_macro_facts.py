"""C/C++ macro state facts: which object macros are provably empty at a source position."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field
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
_CPP_MACRO_DEFINITION_NODES = frozenset({"preproc_def", "preproc_function_def"})


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


_CPP_STACK_ACTIONS = {"push_macro": _CppMacroAction.PUSH, "pop_macro": _CppMacroAction.POP}
_CPP_ACTION_STATES = {
    _CppMacroAction.DEFINE_EMPTY: _CppMacroState.EMPTY_OBJECT,
    _CppMacroAction.DEFINE_OTHER: _CppMacroState.NOT_EMPTY_OBJECT,
    _CppMacroAction.UNDEFINE: _CppMacroState.UNDEFINED,
    _CppMacroAction.UNKNOWN: _CppMacroState.UNKNOWN,
}


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
        active_event = self._empty_event_before(name, declaration.start_byte)
        if active_event is None or active_event.definition is None:
            return None

        declaration_branch = _cpp_preproc_branch_path(declaration)
        if not _branch_within(declaration_branch, active_event.branch_path):
            return None
        definition_branch = _cpp_preproc_branch_path(active_event.definition)
        if not _branch_within(declaration_branch, definition_branch):
            return None

        barrier_index = bisect_left(self.barriers, declaration.start_byte) - 1
        if barrier_index >= 0 and self.barriers[barrier_index] > active_event.position:
            return None
        return active_event.definition

    def _empty_event_before(self, name: str, position: int) -> _CppMacroEvent | None:
        """The event in force for *name* at *position*, when it is an empty definition."""
        events = self.events.get(name, ())
        event_index = bisect_left(events, position, key=lambda event: event.position) - 1
        if event_index < 0 or events[event_index].state is not _CppMacroState.EMPTY_OBJECT:
            return None
        return events[event_index]


def _branch_within(path: tuple[int, ...], branch: tuple[int, ...]) -> bool:
    """Whether *path* lies inside the preprocessor *branch*."""
    return path[: len(branch)] == branch


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


@dataclass
class _MacroCaptures:
    """The query captures macro state tracking reads, keyed by node id."""

    definitions: dict[int, tuple[Node, str]] = field(default_factory=dict)
    preproc_calls: dict[int, Node] = field(default_factory=dict)
    include_nodes: dict[int, Node] = field(default_factory=dict)
    call_sites: dict[int, tuple[Node, str]] = field(default_factory=dict)

    @classmethod
    def from_matches(cls, matches: list[dict], src: str) -> _MacroCaptures:
        captures = cls()
        for capture_dict in matches:
            definition = _macro_definition(capture_dict, src)
            if definition is not None:
                captures.definitions[definition[0].id] = definition
            for node in capture_dict.get("symbol.cpp_preproc_call", []):
                captures.preproc_calls[node.id] = node
            for node in capture_dict.get("symbol.cpp_macro_state_barrier", []):
                captures.include_nodes[node.id] = node
            site_nodes = capture_dict.get("call.site", [])
            target_nodes = capture_dict.get("call.target", [])
            if site_nodes and target_nodes:
                captures.call_sites[site_nodes[0].id] = (
                    site_nodes[0],
                    _node_text(target_nodes[0], src),
                )
        return captures

    @property
    def known_names(self) -> set[str]:
        return {name for _, name in self.definitions.values()}


def _macro_definition(capture_dict: dict, src: str) -> tuple[Node, str] | None:
    """The macro node and normalized name of a ``#define`` match, if it is one."""
    def_nodes = capture_dict.get("symbol.def", [])
    name_nodes = capture_dict.get("symbol.name", [])
    if not def_nodes or def_nodes[0].type not in _CPP_MACRO_DEFINITION_NODES or not name_nodes:
        return None
    macro_name = _cpp_normalize_identifier(_node_text(name_nodes[0], src))
    return (def_nodes[0], macro_name) if macro_name else None


@dataclass
class _MacroHazards:
    """Known macros whose expansion runs a push/pop pragma, directly or through aliases.

    ``targets`` maps each macro to the known macros its expansion pushes or pops;
    ``unknown`` holds macros whose stack operation names no recoverable target.
    """

    targets: dict[str, set[str]]
    unknown: set[str]

    def is_hazard(self, name: str) -> bool:
        return bool(self.targets[name]) or name in self.unknown

    def affected_by(self, referenced_names: set[str]) -> set[str]:
        return set().union(*(self.targets.get(name, set()) for name in referenced_names))

    def inherit_through_aliases(self, references: dict[str, set[str]]) -> None:
        """Propagate hazards from each macro to every macro whose replacement names it."""
        dependents: dict[str, set[str]] = {name: set() for name in self.targets}
        for macro_name, referenced_names in references.items():
            for referenced_name in referenced_names:
                dependents[referenced_name].add(macro_name)

        pending = deque(name for name in self.targets if self.is_hazard(name))
        queued = set(pending)
        while pending:
            referenced_name = pending.popleft()
            queued.remove(referenced_name)
            for macro_name in dependents[referenced_name]:
                if self._inherit(macro_name, referenced_name) and macro_name not in queued:
                    pending.append(macro_name)
                    queued.add(macro_name)

    def _inherit(self, macro_name: str, referenced_name: str) -> bool:
        """Copy *referenced_name*'s hazards onto *macro_name*; True when anything changed."""
        inherited_targets = self.targets[referenced_name] - self.targets[macro_name]
        inherited_unknown = referenced_name in self.unknown and macro_name not in self.unknown
        if not inherited_targets and not inherited_unknown:
            return False
        self.targets[macro_name].update(inherited_targets)
        if inherited_unknown:
            self.unknown.add(macro_name)
        return True


def _macro_hazards(
    definitions: dict[int, tuple[Node, str]], known_names: set[str], src: str
) -> _MacroHazards:
    """Find the macros whose replacement contains a pragma stack operation."""
    # A macro whose replacement contains a pragma stack operation is itself a
    # state hazard when invoked. Resolve simple wrapper aliases transitively;
    # anything more dynamic remains conservative at the invocation site.
    hazards = _MacroHazards(targets={name: set() for name in known_names}, unknown=set())
    references: dict[str, set[str]] = {name: set() for name in known_names}
    for macro_node, macro_name in definitions.values():
        value_node = macro_node.child_by_field_name("value")
        replacement = _node_text(value_node, src) if value_node is not None else ""
        replacement_operation, target = _cpp_macro_stack_operation(replacement)
        if replacement_operation is not None:
            if target in known_names:
                hazards.targets[macro_name].add(target)
            elif target is None:
                hazards.unknown.add(macro_name)
        references[macro_name].update(
            _cpp_known_macros_in_text(replacement, known_names) - {macro_name}
        )
    hazards.inherit_through_aliases(references)
    return hazards


def _object_macro_invocations(
    definitions: dict[int, tuple[Node, str]], hazards: _MacroHazards, src: str
) -> dict[int, Node]:
    """Every identifier that may expand an object-like hazard macro, keyed by node id."""
    # Object-like macros expand wherever their token appears. Only local
    # definitions count: an include is a barrier, not evidence that later
    # identifiers are imported wrappers.
    object_hazard_names = {
        macro_name
        for macro_node, macro_name in definitions.values()
        if macro_node.type == "preproc_def" and hazards.is_hazard(macro_name)
    }
    invocations: dict[int, Node] = {}
    if not object_hazard_names:
        return invocations
    root = next(iter(definitions.values()))[0]
    while root.parent is not None:
        root = root.parent
    pending_nodes = [root]
    while pending_nodes:
        node = pending_nodes.pop()
        if node.type in _CPP_MACRO_DEFINITION_NODES:
            continue
        if (
            node.type == "identifier"
            and _cpp_normalize_identifier(_node_text(node, src)) in object_hazard_names
        ):
            invocations[node.id] = node
        pending_nodes.extend(node.children)
    return invocations


class _MacroOperations:
    """Macro operations in capture order, plus the positions where macro state turns opaque."""

    def __init__(self, known_names: set[str], hazards: _MacroHazards) -> None:
        self.known_names = known_names
        self.known_names_by_length = tuple(sorted(known_names, key=len, reverse=True))
        self.hazards = hazards
        self.operations: list[_CppMacroOperation] = []
        self.barriers: set[int] = set()
        self._keys: set[tuple[int, _CppMacroAction, str]] = set()

    def add(
        self,
        node: Node,
        action: _CppMacroAction,
        name: str,
        *,
        definition: Node | None = None,
    ) -> None:
        key = (node.start_byte, action, name)
        if key in self._keys:
            return
        self._keys.add(key)
        self.operations.append(
            _CppMacroOperation(
                position=node.start_byte,
                action=action,
                name=name,
                node=node,
                definition=definition,
            )
        )

    def add_definitions(self, definitions: dict[int, tuple[Node, str]]) -> None:
        for macro_node, macro_name in definitions.values():
            is_empty_object = (
                macro_node.type == "preproc_def"
                and macro_node.child_by_field_name("value") is None
            )
            self.add(
                macro_node,
                (_CppMacroAction.DEFINE_EMPTY if is_empty_object else _CppMacroAction.DEFINE_OTHER),
                macro_name,
                definition=macro_node if is_empty_object else None,
            )

    def add_preproc_call(self, node: Node, src: str) -> None:
        directive, argument = _cpp_preproc_call_parts(_node_text(node, src))
        if directive in _CPP_INCLUDE_LIKE_DIRECTIVES:
            self.barriers.add(node.start_byte)
        elif directive == "undef":
            target = _cpp_known_macro_from_argument(argument, self.known_names_by_length)
            if target is not None:
                self.add(node, _CppMacroAction.UNDEFINE, target)
        elif directive == "pragma":
            self.add_stack_operation(node, argument, direct=True)

    def add_stack_operation(self, node: Node, text: str, *, direct: bool) -> bool:
        """Record a push/pop in *text*; False when *text* holds no stack operation."""
        operation, target = _cpp_macro_stack_operation(text)
        if operation is None:
            return False
        if target in self.known_names:
            action = _CPP_STACK_ACTIONS[operation] if direct else _CppMacroAction.UNKNOWN
            self.add(node, action, target)
        elif target is None:
            self.barriers.add(node.start_byte)
        return True

    def add_call_sites(self, call_sites: dict[int, tuple[Node, str]], src: str) -> None:
        for node, target_text in call_sites.values():
            if _is_pragma_operator(target_text):
                self.add_stack_operation(node, _node_text(node, src), direct=True)
        for node, target_text in call_sites.values():
            if not _is_pragma_operator(target_text):
                self.add_indirect_hazards(node, _node_text(node, src), target_text)

    def add_indirect_hazards(self, node: Node, text: str, target_text: str = "") -> None:
        """Mark the macros a wrapper invocation may push or pop as UNKNOWN."""
        if self.add_stack_operation(node, text, direct=False):
            return
        target_name = (
            _cpp_normalize_identifier(target_text)
            or _cpp_known_macro_from_argument(text, self.known_names_by_length)
            or ""
        )
        referenced_names = _cpp_known_macros_in_text(text, self.known_names)
        if target_name:
            referenced_names.add(target_name)
        affected_names = self.hazards.affected_by(referenced_names)
        for affected_name in affected_names:
            self.add(node, _CppMacroAction.UNKNOWN, affected_name)
        if affected_names or referenced_names & self.hazards.unknown:
            # The wrapper may push or pop even when its final macro value is
            # conservatively represented as UNKNOWN. Taint the stack too, so
            # a later direct pop cannot restore a stale local snapshot.
            self.barriers.add(node.start_byte)


def _is_pragma_operator(target_text: str) -> bool:
    return _cpp_normalize_identifier(target_text) in {"_Pragma", "__pragma"}


class _MacroReplay:
    """Replays position-sorted macro operations into per-macro state events."""

    def __init__(self, sorted_barriers: list[int]) -> None:
        self.events: dict[str, list[_CppMacroEvent]] = {}
        self._barriers = sorted_barriers
        self._barrier_index = 0
        self._current: dict[str, _CppMacroEvent] = {}
        self._stacks: dict[str, list[_CppMacroStackEntry]] = {}

    def apply(self, operation: _CppMacroOperation) -> None:
        if self._cross_barriers(operation.position):
            # Includes and opaque pragma wrappers may change the macro and its
            # stack; only a later local define or push re-establishes them.
            self._current.clear()
            self._stacks.clear()

        branch_path = _cpp_preproc_branch_path(operation.node)
        if operation.action is _CppMacroAction.PUSH:
            self._push(operation, branch_path)
        elif operation.action is _CppMacroAction.POP:
            self._record(operation, self._pop(operation, branch_path))
        else:
            self._record(
                operation,
                _CppMacroEvent(
                    position=operation.position,
                    state=_CPP_ACTION_STATES[operation.action],
                    definition=operation.definition,
                    branch_path=branch_path,
                ),
            )

    def _cross_barriers(self, position: int) -> bool:
        """Advance past every barrier before *position*; True when any was crossed."""
        start = self._barrier_index
        self._barrier_index = bisect_left(self._barriers, position, lo=start)
        return self._barrier_index > start

    def _record(self, operation: _CppMacroOperation, event: _CppMacroEvent) -> None:
        self.events.setdefault(operation.name, []).append(event)
        self._current[operation.name] = event

    def _push(self, operation: _CppMacroOperation, branch_path: tuple[int, ...]) -> None:
        snapshot = self._current.get(operation.name)
        if snapshot is not None and not _branch_within(branch_path, snapshot.branch_path):
            snapshot = None
        self._stacks.setdefault(operation.name, []).append(
            _CppMacroStackEntry(event=snapshot, branch_path=branch_path)
        )

    def _pop(self, operation: _CppMacroOperation, branch_path: tuple[int, ...]) -> _CppMacroEvent:
        stack = self._stacks.get(operation.name, [])
        entry = stack.pop() if stack else None
        if entry is None:
            restored = self._unpushed_state(operation, branch_path)
        elif entry.event is None or not _branch_within(branch_path, entry.branch_path):
            restored = None
            stack.clear()
        else:
            restored = entry.event
        if restored is None:
            return _CppMacroEvent(
                position=operation.position,
                state=_CppMacroState.UNKNOWN,
                branch_path=branch_path,
            )
        return _CppMacroEvent(
            position=operation.position,
            state=restored.state,
            definition=restored.definition,
            branch_path=branch_path,
        )

    def _unpushed_state(
        self, operation: _CppMacroOperation, branch_path: tuple[int, ...]
    ) -> _CppMacroEvent | None:
        """The state a pop with no local push keeps: the current one, before any barrier."""
        current_event = self._current.get(operation.name)
        if current_event is None or bisect_left(self._barriers, operation.position) != 0:
            return None
        if not _branch_within(branch_path, current_event.branch_path):
            return None
        return current_event


def _build_cpp_macro_facts(matches: list[dict], src: str) -> _CppMacroFacts:
    """Build the conservative, source-ordered macro facts used by C++ recovery."""
    captures = _MacroCaptures.from_matches(matches, src)
    known_names = captures.known_names
    hazards = _macro_hazards(captures.definitions, known_names, src)
    possible_invocations = _object_macro_invocations(captures.definitions, hazards, src)

    collected = _MacroOperations(known_names, hazards)
    collected.barriers.update(node.start_byte for node in captures.include_nodes.values())
    collected.add_definitions(captures.definitions)
    for node in captures.preproc_calls.values():
        collected.add_preproc_call(node, src)
    collected.add_call_sites(captures.call_sites, src)
    for node in possible_invocations.values():
        collected.add_indirect_hazards(node, _node_text(node, src))

    sorted_barriers = sorted(collected.barriers)
    replay = _MacroReplay(sorted_barriers)
    for operation in sorted(collected.operations, key=lambda operation: operation.position):
        replay.apply(operation)
    return _CppMacroFacts(
        events={name: tuple(name_events) for name, name_events in replay.events.items()},
        barriers=tuple(sorted_barriers),
    )
