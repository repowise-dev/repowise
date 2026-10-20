"""Call-site extraction: what each call names, on which receiver, from which symbol."""

from __future__ import annotations

import re
from dataclasses import replace

from tree_sitter import Node

from ..extractors import node_text
from ..language_configs import LanguageConfig
from ..models import CallReceiver, CallSite, FileInfo, Symbol
from ..parser_helpers import (
    _argument_literal_types,
    _argument_names,
    _count_arguments,
    _elixir_call_is_definitional,
    _find_enclosing_symbol,
    _objc_call_is_block_variable,
    _objc_message_selector,
    _overload_arguments,
)
from ..python_tasks import is_fire_and_forget

_node_text = node_text

def _call_receiver_from_node(node: Node, src: str) -> CallReceiver | None:
    """Describe an inner call captured as another call's receiver.

    The queries identify the complete inner AST node.  This helper reads only
    named tree-sitter fields, so nested arguments and formatting cannot change
    which call is carried into resolution.
    """

    if node.type == "identifier":
        return _dart_call_receiver(node, src)
    if (
        node.type == "call_expression"
        and node.child_by_field_name("function") is None
        and node.child_by_field_name("arguments") is None
    ):
        return _kotlin_call_receiver(node, src)
    arguments = node.child_by_field_name("arguments")
    argument_count = _count_arguments(arguments) if arguments is not None else None

    target = node.child_by_field_name("name")
    receiver = node.child_by_field_name("object")
    if target is None:
        function = node.child_by_field_name("function")
        if function is None:
            return None
        if function.type == "attribute":
            return _python_attribute_receiver(function, src, argument_count)
        target, receiver = _function_target(function)

    if target is None:
        return None
    target_name = _node_text(target, src).strip()
    if not target_name:
        return None

    receiver_name = None
    if receiver is not None and receiver.type in ("identifier", "this"):
        receiver_name = _node_text(receiver, src).strip() or None
    return CallReceiver(target_name, receiver_name, argument_count)


def _kotlin_call_receiver(call: Node, src: str) -> CallReceiver | None:
    """Describe a Kotlin call captured as another call's receiver."""
    named = call.named_children
    callee = named[0] if named else None
    arguments = next(
        (child for child in named if child.type == "value_arguments"), None
    )
    if callee is None or arguments is None:
        return None
    count = _count_arguments(arguments)

    if callee.type == "identifier":
        return CallReceiver(_node_text(callee, src).strip(), None, count)
    if callee.type != "navigation_expression" or len(callee.named_children) != 2:
        return None

    receiver, target = callee.named_children
    if target.type != "identifier":
        return None

    if receiver.type == "call_expression":
        receiver_name = None
    elif receiver.type in ("identifier", "this_expression") or (
        receiver.type == "navigation_expression"
        and [child.type for child in receiver.named_children]
        == ["this_expression", "identifier"]
    ):
        receiver_name = _receiver_text("kotlin", receiver, src)
    else:
        return None

    if receiver.type != "call_expression" and receiver_name is None:
        return None
    return CallReceiver(_node_text(target, src).strip(), receiver_name, count)


def _function_target(function: Node) -> tuple[Node | None, Node | None]:
    """``(target, receiver)`` named by an inner call's ``function`` field."""
    if function.type in ("identifier", "property_identifier", "field_identifier"):
        return function, None
    if function.type == "generic_name":
        target = function.child_by_field_name("name") or next(
            (child for child in function.named_children if child.type == "identifier"), None
        )
        return target, None
    target = (
        function.child_by_field_name("name")
        or function.child_by_field_name("property")
        or function.child_by_field_name("field")
    )
    receiver = (
        function.child_by_field_name("expression")
        or function.child_by_field_name("object")
        or function.child_by_field_name("argument")
        or function.child_by_field_name("operand")
    )
    return target, receiver


def _python_attribute_receiver(
    function: Node, src: str, argument_count: int | None
) -> CallReceiver | None:
    """Python ``obj.m()``: the receiver is kept as a plain dotted path, and a
    call on anything else is not carried at all.
    """
    target = function.child_by_field_name("attribute")
    obj = function.child_by_field_name("object")
    path = (
        _receiver_text("python", obj, src)
        if obj is not None and obj.type in ("identifier", "attribute")
        else None
    )
    if target is None or path is None:
        return None
    return CallReceiver(_node_text(target, src).strip(), path, argument_count)


def _dart_call_receiver(name: Node, src: str) -> CallReceiver | None:
    """The Dart call whose *name* the query captured: ``a.b(..)``, ``B(..)``.

    A Dart call is a flat selector run with no call node, so the name's
    siblings are read: the selector after it holds the arguments, and the
    node before a ``.b`` selector is its receiver.
    """
    if name.parent.type == "unconditional_assignable_selector":
        head = name.parent.parent
        prev = head.prev_named_sibling
        if prev is not None and any(c.type == "argument_part" for c in prev.named_children):
            receiver = None
        else:
            receiver = _node_text(prev, src).strip() if prev is not None else None
    else:
        head, receiver = name, None
    return CallReceiver(
        _node_text(name, src).strip(), receiver, _dart_argument_count(head.next_named_sibling)
    )


def _dart_argument_count(selector: Node | None) -> int | None:
    """The arguments a Dart ``(selector (argument_part (arguments)))`` passes."""
    parts = selector.named_children if selector is not None else ()
    found = [arg for part in parts for arg in part.named_children if arg.type == "arguments"]
    return _count_arguments(found[0]) if found else None



# The receiver captures that are themselves a member access, by language (the
# queries widen only these two), and what a plain dotted path of names looks
# like once whitespace is gone.
_PATH_RECEIVERS = frozenset({("typescript", "member_expression"), ("python", "attribute")})
_DOTTED_PATH = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+")


def _receiver_text(language: str, node: Node, src: str) -> str | None:
    """The receiver's text, or None to drop the site.

    A dotted receiver (``this.a.b.m()``) is kept only as a plain path of
    names: a call, subscript or optional hop inside it types nothing, and
    dropping the site keeps it off the bare-call tiers.
    """
    sibling = node.next_named_sibling
    if language == "java" and node.type != "super" and getattr(sibling, "type", None) == "super":
        # ``S.super.m()``: ``S`` qualifies the ``super`` its own pattern captures.
        return None
    text = _node_text(node, src).strip()
    if (language, node.type) not in _PATH_RECEIVERS:
        return text
    path = "".join(text.split())
    return path if _DOTTED_PATH.fullmatch(path) else None


def _normalize_php_receiver(text: str) -> str:
    """Spell PHP's receiver the way the resolver's strategies expect.

    `self::` and `static::` are the same dispatch as `$this`, and the self/this
    strategy tests `in ("self", "this")` — so without this the whole implicit-
    receiver population misses. `parent::` needs the heritage walk and is left
    to miss rather than guessed at.
    """
    name = text.lstrip("$")
    return "this" if name in ("self", "static") else name


def _objc_call_target(
    site_node: Node, target_node: Node, target_name: str, has_receiver: bool, src: str
) -> str | None:
    """Objective-C call target name, or None to drop the match."""
    # A message send binds one `method:` child per keyword, so
    # `[view setTitle:t forState:s]` matches the query twice. Join the whole
    # selector on the first match and drop the rest.
    if site_node.type == "message_expression":
        return _objc_message_selector(site_node, target_node, src)
    # A block variable invoked with C call syntax (`completionBlock(hit)`) is
    # not a call to a same-named function or property.
    if not has_receiver and _objc_call_is_block_variable(site_node, target_name, src):
        return None
    return target_name


def _jsx_supplied_props(site_node: Node, src: str) -> frozenset[str] | None:
    """Props written on a JSX element, or None when not JSX or a spread hides them."""
    if site_node.type not in ("jsx_self_closing_element", "jsx_opening_element"):
        return None
    props_set: set[str] = set()
    for child in site_node.children:
        if child.type == "jsx_attribute":
            name = next(
                (sub for sub in child.children if sub.type in ("property_identifier", "identifier")),
                None,
            )
            if name is not None:
                props_set.add(_node_text(name, src))
        elif child.type == "jsx_expression" and any(
            sub.type == "spread_element" for sub in child.children
        ):
            return None
    return frozenset(props_set)


def _fold_receiverless_twins(calls: list[tuple[tuple[int, int, str], CallSite]]) -> list[CallSite]:
    """Fold the receiver-less copy of a member call into the copy with a receiver.

    A grammar's bare-call pattern that leaves the object unconstrained (Java's
    ``method_invocation``, Ruby's ``call``) matches ``obj.m()`` too. Resolved on
    its own, that copy binds ``m`` by bare name beside whatever ``obj``'s type
    answers, so it survives only as the member call's ``bare_name_fallback``.
    Keyed on the call node, not the line, so a bare ``m(obj.m())`` keeps its own
    site.
    """
    member_sites = {site for site, call in calls if not _is_bare(call)}
    bare_sites = {site for site, call in calls if site in member_sites and _is_bare(call)}
    folded: list[CallSite] = []
    for site, call in calls:
        if _is_bare(call):
            if site not in member_sites:
                folded.append(call)
        elif call.receiver_name and site in bare_sites:
            folded.append(replace(call, bare_name_fallback=True))
        else:
            folded.append(call)
    return folded


def _is_bare(call: CallSite) -> bool:
    return not (call.receiver_name or call.receiver_call)


def _dedupe_calls(calls: list[CallSite]) -> list[CallSite]:
    """One call per ``(line, target, receiver)``, keeping the richest record.

    Two scoped-call patterns can match the same two-part call (one keeps the
    qualifier), so the richer record wins whichever order they arrive in.
    """
    deduplicated: dict[tuple[int, str, str | None], CallSite] = {}
    for call in calls:
        key = (call.line, call.target_name, call.receiver_name)
        existing = deduplicated.get(key)
        if (
            existing is None
            or (existing.receiver_call is None and call.receiver_call is not None)
            or (existing.scope_name is None and call.scope_name is not None)
        ):
            deduplicated[key] = call
    return list(deduplicated.values())


def _extract_calls(
    matches: list[dict],
    config: LanguageConfig,
    file_info: FileInfo,
    src: str,
    symbols: list[Symbol],
) -> list[CallSite]:
    """Extract function/method call sites from the AST."""
    from ..language_data import get_builtin_calls

    call_builtins = get_builtin_calls(file_info.language)

    symbol_ranges = sorted(
        [(s.start_line, s.end_line, s.id) for s in symbols],
        key=lambda t: (t[0], -t[1]),
    )

    # Each call as ``(site, call)``; see ``_fold_receiverless_twins``.
    calls: list[tuple[tuple[int, int, str], CallSite]] = []
    for capture_dict in matches:
        found = _call_from_match(
            capture_dict, config, file_info.language, src, call_builtins, symbol_ranges
        )
        if found is not None:
            calls.append(found)
    return _dedupe_calls(_fold_receiverless_twins(calls))


def _call_from_match(
    capture_dict: dict,
    config: LanguageConfig,
    language: str,
    src: str,
    call_builtins: frozenset[str],
    symbol_ranges: list[tuple[int, int, str]],
) -> tuple[tuple[int, int, str], CallSite] | None:
    """One call match as ``(site key, CallSite)``, or None to drop it."""
    site_nodes = capture_dict.get("call.site", [])
    target_nodes = capture_dict.get("call.target", [])
    if not site_nodes or not target_nodes:
        return None

    site_node = site_nodes[0]
    names = _call_names(
        capture_dict, site_node, target_nodes[0], config, language, src, call_builtins
    )
    if names is None:
        return None
    target_name, receiver_name = names
    # F#: a dotted static path (``Path.Combine(a, b)``) is one identifier node,
    # so it splits on the text, after the builtin check above.
    if language == "fsharp" and receiver_name is None and "." in target_name:
        receiver_part, _, target_name = target_name.rpartition(".")
        receiver_name = receiver_part.strip()
        target_name = target_name.strip()
        if not target_name:
            return None

    arg_nodes = capture_dict.get("call.arguments", [])
    receiver_call_nodes = capture_dict.get("call.receiver_call", [])
    receiver_call = (
        _call_receiver_from_node(receiver_call_nodes[0], src)
        if receiver_call_nodes
        else None
    )
    if receiver_call_nodes and receiver_call is None and language == "kotlin":
        # Avoid resolving an unsupported chained call by its bare name.
        return None
    scope_nodes = capture_dict.get("call.scope", [])
    line = site_node.start_point[0] + 1
    arguments = _overload_arguments(language, arg_nodes)
    call = CallSite(
        target_name=target_name,
        receiver_name=receiver_name,
        caller_symbol_id=_find_enclosing_symbol(line, symbol_ranges),
        line=line,
        argument_count=_count_arguments(arg_nodes[0]) if arg_nodes else None,
        receiver_call=receiver_call,
        argument_types=_argument_literal_types(arguments, src),
        argument_names=_argument_names(arguments, src),
        scope_name=_node_text(scope_nodes[0], src).strip() if scope_nodes else None,
        edge_type=(
            "references" if site_node.type in config.reference_call_node_types else "calls"
        ),
        supplied_props=_jsx_supplied_props(site_node, src),
        # Ceiling: schedulers matched by name, the task followed within its
        # own function only (see ``python_tasks``).
        spawned=language == "python" and is_fire_and_forget(site_node, src),
    )
    return (site_node.start_byte, site_node.end_byte, target_name), call


def _call_names(
    capture_dict: dict,
    site_node: Node,
    target_node: Node,
    config: LanguageConfig,
    language: str,
    src: str,
    call_builtins: frozenset[str],
) -> tuple[str, str | None] | None:
    """``(target_name, receiver_name)`` as written, or None to drop the site."""
    target_name = config.call_target_name_fn(_node_text(target_node, src).strip(), target_node.type)
    if not target_name:
        return None
    receiver_nodes = capture_dict.get("call.receiver", [])
    if language == "objectivec":
        target_name = _objc_call_target(
            site_node, target_node, target_name, bool(receiver_nodes), src
        )
        if target_name is None:
            return None
    if target_name in call_builtins:
        return None
    # Elixir: a definition head (`add(a, b)` in `def add(a, b)`) and a module
    # attribute (`@doc "..."`) are both ``call`` nodes; left in, every function
    # would call itself.
    if language == "elixir" and _elixir_call_is_definitional(site_node, src):
        return None
    receiver_name = None
    if receiver_nodes:
        receiver_name = _receiver_text(language, receiver_nodes[0], src)
        if receiver_name is None:
            return None
    if receiver_name and language == "php":
        receiver_name = _normalize_php_receiver(receiver_name)
    return target_name, receiver_name
