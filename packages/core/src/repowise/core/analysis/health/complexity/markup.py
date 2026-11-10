"""Lines that hold only markup or literal data: no logic.

In a JSX file, tags, attributes and text are markup; so are the views of a
SwiftUI ``body``, a Compose ``@Composable`` and a Flutter ``build()``. An attribute value of plain
data (``style={{ color: "red" }}``, ``title={label}``) is markup too, and so is a
component's ``const`` data literal used only there. In every language, the
continuation lines of a multi-line string (a text block, heredoc, raw or
template string) are data, and so is a switch arm that only returns or assigns
a literal, and the continuation rows of a builder chain whose calls mostly take
literal arguments. A call, an operator, a function or control flow keeps a line code.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING

from .nloc import _source_lines

if TYPE_CHECKING:
    from tree_sitter import Node

#: Grammars that parse JSX; a ``.ts`` file reads with ``typescript`` and has none.
_JSX_GRAMMARS = frozenset({"tsx", "javascript"})
#: The ``{...}`` slot inside markup; what it holds is code again unless it is data.
_EXPRESSION_SLOT = "jsx_expression"
#: Nodes a plain data value is built from: literals, references, objects, arrays.
_DATA_KINDS = frozenset(
    {
        "string",
        "string_fragment",
        "escape_sequence",
        "template_string",
        "template_substitution",
        "number",
        "true",
        "false",
        "null",
        "undefined",
        "identifier",
        "property_identifier",
        "shorthand_property_identifier",
        "member_expression",
        "object",
        "pair",
        "array",
        "spread_element",
        "parenthesized_expression",
    }
)
#: ``x as const`` / ``x satisfies T``: only the value is data, the type is skipped.
_TYPED_VALUE = frozenset({"as_expression", "satisfies_expression"})


def _is_data(node: Node) -> bool:
    """True when *node* holds no call, operator, function or control flow.

    Nested JSX counts as data here: its own ``{...}`` slots are judged on their own.
    """
    stack = list(node.named_children)
    while stack:
        child = stack.pop()
        kind = child.type
        if kind.startswith("jsx_") or "comment" in kind:
            continue
        if kind in _TYPED_VALUE:
            stack.extend(child.named_children[:1])
        elif kind in _DATA_KINDS or (
            kind == "unary_expression" and child.children[0].type == "-"
        ):
            stack.extend(child.named_children)
        else:
            return False
    return True


def _is_data_slot(node: Node) -> bool:
    """An attribute's ``{...}`` value that is plain data."""
    parent = node.parent
    return parent is not None and parent.type == "jsx_attribute" and _is_data(node)


def _in_data_slot(node: Node, scope: Node) -> bool:
    """*node* sits in a data attribute value below *scope*."""
    slot = node.parent
    while slot is not None and slot != scope and slot.type != _EXPRESSION_SLOT:
        slot = slot.parent
    return slot is not None and slot != scope and _is_data_slot(slot)


def _used_only_in_data_slots(name: Node, scope: Node) -> bool:
    """Every other mention of *name* in *scope* sits in a data attribute value."""
    text = name.text
    uses = 0
    stack = [scope]
    while stack:
        node = stack.pop()
        if node.type not in ("identifier", "shorthand_property_identifier"):
            stack.extend(node.children)
        elif node != name and node.text == text:
            if not _in_data_slot(node, scope):
                return False
            uses += 1
    return uses > 0


def _data_literal_name(node: Node) -> Node | None:
    """The name of ``const X = {...}`` / ``[...]`` of plain data in a block, else ``None``."""
    declarators = node.named_children
    if node.children[0].type != "const" or len(declarators) != 1:
        return None
    name = declarators[0].child_by_field_name("name")
    value = declarators[0].child_by_field_name("value")
    if name is None or value is None or name.type != "identifier":
        return None
    literal = value.named_children[0] if value.type in _TYPED_VALUE else value
    return name if literal.type in ("object", "array") and _is_data(literal) else None


def _data_declarations(root: Node) -> set[tuple[int, int]]:
    """Byte spans of ``const X = {...}`` in a function body, X used only as attribute data."""
    spans: set[tuple[int, int]] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        scope = node.parent
        if node.type != "lexical_declaration" or scope is None or scope.type != "statement_block":
            continue
        name = _data_literal_name(node)
        if name is not None and _used_only_in_data_slots(name, scope):
            spans.add((node.start_byte, node.end_byte))
    return spans


def _opens_markup(node: Node, in_markup: bool, data_decls: set[tuple[int, int]]) -> bool:
    """Whether *node*'s tokens are markup, given its parent's state."""
    kind = node.type
    if kind == _EXPRESSION_SLOT:
        return in_markup and _is_data_slot(node)
    if kind.startswith("jsx_"):
        return True
    if kind == "lexical_declaration" and (node.start_byte, node.end_byte) in data_decls:
        return True
    return in_markup


def _token_rows(root: Node) -> tuple[set[int], set[int]]:
    """Rows holding a markup token and rows holding a code token, comments skipped."""
    markup: set[int] = set()
    code: set[int] = set()
    data_decls = _data_declarations(root)
    stack: list[tuple[Node, bool]] = [(root, False)]
    while stack:
        node, in_markup = stack.pop()
        if "comment" in node.type:
            continue
        in_markup = _opens_markup(node, in_markup, data_decls)
        if node.children:
            stack.extend((child, in_markup) for child in node.children)
        elif node.start_byte < node.end_byte:
            rows = range(node.start_point[0], node.end_point[0] + 1)
            (markup if in_markup else code).update(rows)
    return markup, code


#: Node kinds of a literal across grammars: plain, raw, verbatim, template and
#: interpolated strings, text blocks and heredocs; then initializer lists, arrays,
#: maps, sets, tuples and struct literals. ``file_scan`` collects the multi-line
#: ones outside docstrings.
LITERAL_KINDS = frozenset(
    {
        "string",
        "raw_string",
        "string_literal",
        "template_string",
        "concatenated_string",
        "raw_string_literal",
        "verbatim_string_literal",
        "multiline_string_literal",
        "multi_line_string_literal",
        "interpolated_string_expression",
        "interpolated_string",
        "encapsed_string",
        "heredoc",
        "heredoc_body",
        "nowdoc",
        "sigil",
        "initializer_list",
        "array_initializer",
        "initializer_expression",
        "collection_expression",
        "literal_value",
        "array_expression",
        "tuple_expression",
        "struct_expression",
        "list",
        "dictionary",
        "set",
        "tuple",
        "array",
        "object",
        "hash",
        "array_literal",
        "dictionary_literal",
        "array_creation_expression",
        "list_literal",
        "set_or_map_literal",
    }
)
#: Name fragments of the nodes literal data is built from: text, names, numbers.
_DATA_PARTS = (
    "string",
    "str_text",
    "heredoc",
    "nowdoc",
    "content",
    "fragment",
    "escape",
    "identifier",
    "interpolat",
    "literal",
    "number",
    "integer",
    "float",
    "expansion",
    "symbol",
    "character",
)
#: A function literal is code whatever its name says.
_CODE_PARTS = ("func", "lambda", "closure")
#: Data nodes whose names carry none of the fragments: names, constants, member access.
_DATA_NAMES = frozenset(
    {
        "name",
        "variable_name",
        "true",
        "false",
        "null",
        "nil",
        "none",
        "undefined",
        "member_expression",
        "field_expression",
        "attribute",
        "navigation_expression",
        "selector_expression",
        "field_access",
        "member_access_expression",
        "parenthesized_expression",
        "boolean",
        # The pieces of a table: elements, keys, designators and element types.
        "initializer_list",
        "initializer_pair",
        "field_designator",
        "subscript_designator",
        "array_initializer",
        "initializer_expression",
        "implicit_array_creation_expression",
        "collection_expression",
        "collection_element",
        "expression_element",
        "literal_value",
        "literal_element",
        "keyed_element",
        "array_expression",
        "tuple_expression",
        "struct_expression",
        "field_initializer_list",
        "field_initializer",
        "list",
        "dictionary",
        "set",
        "tuple",
        "pair",
        "array",
        "object",
        "hash",
        "array_creation_expression",
        "array_element_initializer",
        "generic_name",
        "type_argument_list",
        "type_arguments",
        "template_argument_list",
        "type_descriptor",
        "dimensions",
        "array_rank_specifier",
        "minus_operator",  # Dart's `-` in `-1`, under a `prefix_operator`
        "template_substitution",  # the `${...}` slot; what it holds is judged on its own
    }
)
#: A sign on a number (``-1``) or Swift's implicit member (``.leading``) keeps
#: a prefix expression data; any other operator is code.
_SIGNS = frozenset({b"-", b"+", b"."})


def _rows(node: Node) -> range:
    return range(node.start_point[0], node.end_point[0] + 1)


def _is_data_node(node: Node) -> bool:
    """A literal's text, a name, a number, a table or member access: no call, operator or function."""
    kind = node.type
    if kind in _DATA_NAMES or kind.endswith("_type"):
        return True
    if "unary" in kind or "prefix" in kind:
        return node.children[0].text in _SIGNS
    if any(part in kind for part in _CODE_PARTS):
        return False
    return any(part in kind for part in _DATA_PARTS)


def _literal_token_rows(literal: Node, data: set[int], code: set[int]) -> None:
    """Sort *literal*'s token rows into *data* and *code*.

    An embedded piece that is not data (``${call()}``, ``#{a + b}``) makes its rows code.
    """
    stack = [literal]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            continue
        if node.is_named and not _is_data_node(node):
            code.update(_rows(node))
        elif node.children:
            stack.extend(node.children)
        elif node.start_byte < node.end_byte:
            data.update(_rows(node))


def _edge_rows_with_code(
    literal: Node, source: bytes, end: int | None = None, last: int | None = None
) -> list[int]:
    """The literal's first and last rows when a token outside it shares them.

    A trailing ``,`` or ``;`` closes the literal and leaves its last row data.
    *end* and *last* override the node's end byte and row.
    """
    start = literal.start_byte
    end = literal.end_byte if end is None else end
    line_end = source.find(b"\n", end)
    rows = []
    if source[source.rfind(b"\n", 0, start) + 1 : start].strip():
        rows.append(literal.start_point[0])
    if source[end : line_end if line_end >= 0 else len(source)].strip(b" \t\r,;"):
        rows.append(literal.end_point[0] if last is None else last)
    return rows


def _string_rows(literals: list[Node], source: bytes) -> set[int]:
    """Rows inside the multi-line *literals* that hold nothing but literal data.

    The row that opens a literal stays code when code precedes it there
    (``q = \"\"\"``), so a long text block costs its statement one line.
    """
    data: set[int] = set()
    code: set[int] = set()
    covered_to = -1
    for literal in sorted(literals, key=lambda node: (node.start_byte, -node.end_byte)):
        if literal.start_byte < covered_to:
            continue
        covered_to = literal.end_byte
        _literal_token_rows(literal, data, code)
        code.update(_edge_rows_with_code(literal, source))
    return data - code


#: Arms of a switch / when / match across grammars. ``file_scan`` collects them.
ARM_KINDS = frozenset(
    {
        "switch_case",
        "switch_default",
        "when_entry",
        "match_arm",
        "expression_case",
        "default_case",
        "switch_block_statement_group",
        "switch_rule",
        "switch_section",
        "switch_expression_arm",
        "case_clause",
        "switch_entry",
        "case_statement",
        "default_statement",
        "match_conditional_expression",
        "match_default_expression",
        "when",
        "switch_statement_case",
        "switch_statement_default",
        "switch_expression_case",
    }
)
#: Arms whose body is an expression (``1 => "one"``); a statement arm with no
#: return or assignment falls through to the next arm and holds no data.
_EXPRESSION_ARMS = frozenset(
    {
        "when_entry",
        "match_arm",
        "switch_rule",
        "switch_expression_arm",
        "case_clause",
        "match_conditional_expression",
        "match_default_expression",
        "when",
        "switch_expression_case",
    }
)
#: An arm's plumbing: its label or pattern and the blocks around its body.
_ARM_STRUCTURE = frozenset(
    {
        "switch_label",
        "constant_pattern",
        "case_pattern",
        "match_pattern",
        "switch_pattern",
        "pattern",
        "match_condition_list",
        "expression_list",
        "discard",
        "wildcard",
        "default_keyword",
        "dotted_name",
        "qualified",
        "qualified_identifier",
        "class_constant_access_expression",
        "case_builtin",
        "break_builtin",
        "break_statement",
        "statement_list",
        "statements",
        "block",
        "statement_block",
        "compound_statement",
        "control_structure_body",
        "then",
        "expression_statement",
        "directly_assignable_expression",
        "assignable_expression",
    }
)
#: The statement that carries an arm's value; an arm holds at most one.
_ARM_VALUE = frozenset(
    {
        "return_statement",
        "return_expression",
        "control_transfer_statement",
        "yield_statement",
        "assignment_expression",
        "assignment",
        "assignment_statement",
    }
)


def _arm_is_data(arm: Node) -> bool:
    """An arm that only returns, yields or assigns literal data (``case "a": return `x ${y}`;``).

    A call, an operator, control flow, a ``throw`` or a second effect keeps it code.
    """
    values = 0
    stack = list(arm.named_children)
    while stack:
        node = stack.pop()
        kind = node.type
        if "comment" in kind:
            continue
        if kind in _ARM_VALUE:
            if node.children[0].type == "throw":
                return False
            values += bool(node.named_children)
            if values > 1:
                return False
        elif kind not in _ARM_STRUCTURE and not _is_data_node(node):
            return False
        stack.extend(node.named_children)
    return values == 1 or (arm.type in _EXPRESSION_ARMS and _ends_in_a_value(arm))


def _ends_in_a_value(arm: Node) -> bool:
    """Whether an expression arm ends in a value (``1 => "one"``), not a no-op (``{}``)."""
    last = arm.named_children[-1] if arm.named_children else None
    while last is not None and last.type in _ARM_STRUCTURE and last.named_children:
        last = last.named_children[-1]
    return last is not None and _is_data_node(last)


def _is_condition_arm(arm: Node) -> bool:
    """A Kotlin ``when`` with no subject: its arms are an ``if`` chain, not a table."""
    if arm.type != "when_entry" or arm.parent is None:
        return False
    # The subject, when there is one, comes first.
    first = arm.parent.named_child(0)
    return first is None or first.type != "when_subject"


def _arm_rows(arms: list[Node], source: bytes) -> set[int]:
    """Rows of the *arms* that only return or assign literal data: a message table."""
    data: set[int] = set()
    code: set[int] = set()
    for arm in arms:
        # Ruby's ``when`` keyword shares its arm's node kind.
        if not arm.is_named or _is_condition_arm(arm) or not _arm_is_data(arm):
            continue
        # Go and Swift arms end after the whitespace that leads to the next arm.
        text = arm.text
        trailing = len(text) - len(text.rstrip())
        last = arm.end_point[0] - text.count(b"\n", len(text) - trailing)
        data.update(range(arm.start_point[0], last + 1))
        code.update(_edge_rows_with_code(arm, source, arm.end_byte - trailing, last))
    return data - code


#: A call node across grammars. ``file_scan`` collects the multi-line ones that
#: end a method chain (their parent is no member access or call).
CALL_KINDS = frozenset(
    {
        "call_expression",
        "method_invocation",
        "call",
        "invocation_expression",
        "member_call_expression",
    }
)
#: The member access between two links of a chain (``a.b``), across grammars.
CHAIN_MEMBER_KINDS = frozenset(
    {
        "field_expression",
        "member_expression",
        "navigation_expression",
        "selector_expression",
        "attribute",
        "member_access_expression",
    }
)
#: A call's argument list, across grammars.
_ARGUMENT_LISTS = frozenset({"arguments", "argument_list", "value_arguments", "call_suffix"})
#: A chain's plumbing besides calls and member access: arguments and their labels.
_CHAIN_PARTS = _ARGUMENT_LISTS | {
    "argument",
    "value_argument",
    "keyword_argument",
    "named_argument",
    "constant",
}
#: Fragments of a literal argument's node kind: strings, numbers, booleans.
_LITERAL_PARTS = ("string", "literal", "number", "integer", "float", "true", "false", "boolean")
#: Ruby's block arguments (``do |x| ... end``), a callback by another name.
_CALLBACK_KINDS = frozenset({"block", "do_block"})
#: A builder chain has at least this many calls, half or more with a literal argument.
_MIN_CHAIN_LINKS = 4


def _call_arguments(call: Node) -> Node | None:
    """*call*'s argument list when it holds an argument, else ``None``."""
    last = call.named_children[-1] if call.named_children else None
    if last is None or last.type not in _ARGUMENT_LISTS or not last.named_children:
        return None
    return last


def _chain_links(top: Node) -> list[Node]:
    """The calls of the method chain *top* ends: ``c``, ``b`` and ``a`` for ``a(1).b(2).c(3)``."""
    links = []
    node: Node | None = top
    while node is not None and node.type in CALL_KINDS:
        links.append(node)
        callee = node.named_children[0] if node.named_children else None
        if callee is not None and callee.type in CHAIN_MEMBER_KINDS:
            callee = callee.named_children[0] if callee.named_children else None
        node = callee
    return links


def _has_literal_argument(call: Node) -> bool:
    arguments = _call_arguments(call)
    stack = [arguments] if arguments is not None else []
    while stack:
        node = stack.pop()
        kind = node.type
        if any(part in kind for part in _CODE_PARTS):
            continue
        if any(part in kind for part in _LITERAL_PARTS):
            return True
        stack.extend(node.named_children)
    return False


def _takes_callback(call: Node) -> bool:
    """Whether *call* is handed a function (``.map(|x| ...)``, ``.also { ... }``) itself.

    Arguments of a nested call are that call's business.
    """
    stack = list(call.named_children[1:])
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in CALL_KINDS:
            continue
        if kind in _CALLBACK_KINDS or any(part in kind for part in _CODE_PARTS):
            return True
        stack.extend(node.named_children)
    return False


def _is_builder(links: list[Node], min_links: int) -> bool:
    """At least *min_links* calls, half or more of them with a literal argument.

    A chain of four or more that hands a link a callback transforms a value
    (``pattern.substringAfter(":").removePrefix("/").also { ... }``); it is no builder.
    """
    if len(links) < min_links or 2 * sum(map(_has_literal_argument, links)) < len(links):
        return False
    return min_links < _MIN_CHAIN_LINKS or not any(map(_takes_callback, links))


def _chain_node_is_code(node: Node, spine: set[int], data: set[int], code: set[int]) -> bool:
    """Judge one named node of a builder chain; ``True`` when its rows are code.

    A call off the chain's spine (an argument) is judged as a chain of its own,
    so ``Arg::new("x").long("x")`` is data and ``compute(level)`` is code.
    """
    kind = node.type
    if kind in CALL_KINDS:
        if node.id not in spine:
            return not _chain_rows(node, data, code, 1)
        if _call_arguments(node) is None:
            # ``.build()``: an action, not a setting.
            code.add(node.end_point[0])
        return False
    return kind not in _CHAIN_PARTS and not _is_data_node(node)


def _chain_rows(top: Node, data: set[int], code: set[int], min_links: int) -> bool:
    """Sort a builder chain's token rows into *data* and *code*; ``False`` when *top* is none."""
    links = _chain_links(top)
    if not _is_builder(links, min_links):
        return False
    spine = {link.id for link in links}
    stack = [top]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            continue
        if node.is_named and _chain_node_is_code(node, spine, data, code):
            code.update(_rows(node))
        elif node.children:
            stack.extend(node.children)
        elif node.start_byte < node.end_byte:
            data.update(_rows(node))
    return True


def _builder_rows(chains: list[Node], source: bytes) -> set[int]:
    """Continuation rows of the builder *chains*: setting after setting with literal values.

    ``Command::new("app").arg(Arg::new("x").long("x").help("..."))...`` declares a
    command line; its first row stays code, as a long string's does.
    """
    data: set[int] = set()
    code: set[int] = set()
    covered_to = -1
    for top in sorted(chains, key=lambda node: (node.start_byte, -node.end_byte)):
        if top.start_byte < covered_to or not _chain_rows(top, data, code, _MIN_CHAIN_LINKS):
            continue
        covered_to = top.end_byte
        code.add(top.start_point[0])
        code.update(_edge_rows_with_code(top, source))
    return data - code


#: Grammars with a UI tree builder, and bytes a file holds when it may have one:
#: SwiftUI ``some View`` / ``@ViewBuilder``, Compose ``@Composable``, Flutter ``Widget``.
_UI_MARKERS = {
    "swift": (b"some View", b"@ViewBuilder"),
    "kotlin": (b"@Composable",),
    "dart": (b"Widget",),
}
#: A builder's plumbing, which takes the state of the call it sits in: argument
#: lists and labels, trailing closures and their parameters, chained modifiers.
_UI_STRUCTURE = frozenset(
    {
        "statements",
        "call_suffix",
        "value_arguments",
        "value_argument",
        "value_argument_label",
        "navigation_suffix",
        "lambda_literal",
        "annotated_lambda",
        "lambda_function_type",
        "lambda_function_type_parameters",
        "lambda_parameter",
        "lambda_parameters",
        "arguments",
        "argument",
        "argument_part",
        "named_argument",
        "label",
        "selector",
        "unconditional_assignable_selector",
        "conditional_assignable_selector",
        "const_builtin",
    }
)


def _returns_widget(body: Node) -> bool:
    """A Dart function *body* whose signature (its previous sibling) returns ``Widget``."""
    signature = body.prev_named_sibling
    if signature is None:
        return False
    if signature.type == "method_signature" and signature.named_children:
        signature = signature.named_children[0]
    returns = signature.named_children[0] if signature.named_children else None
    return returns is not None and returns.text == b"Widget"


#: Parameter lists, skipped when reading a function's header for a builder marker.
_PARAMETER_KINDS = frozenset({"parameter", "function_value_parameters"})
#: Where a member call is part of a view: an argument value
#: (``modifier = Modifier.padding(8.dp)``) or a link of a chain.
_CHAIN_PARENTS = frozenset(
    {"value_argument", "argument", "named_argument", "call_expression", "navigation_expression"}
)


def _ui_builder_body(node: Node, grammar_tag: str, source: bytes) -> Node | None:
    """The body of *node* when it builds a UI tree, else ``None``.

    A SwiftUI ``var body: some View`` (or a ``@ViewBuilder`` / ``some View``
    function), a Compose ``@Composable`` function, a Flutter method returning
    ``Widget`` (``build``).
    """
    kind = node.type
    if grammar_tag == "dart":
        return node if kind == "function_body" and _returns_widget(node) else None
    if kind not in ("function_declaration", "property_declaration"):
        return None
    header: list[bytes] = []
    for child in node.children:
        if child.type in ("function_body", "computed_property"):
            # Parameters are left out: ``content: @Composable () -> Unit`` is not a builder.
            text = b" ".join(header)
            return child if any(marker in text for marker in _UI_MARKERS[grammar_tag]) else None
        if child.type not in _PARAMETER_KINDS:
            header.append(child.text)
    return None


def _call_root(node: Node) -> tuple[Node, bool] | None:
    """The name a call chain starts from, and whether its first call is on a member.

    ``Text`` and ``False`` for ``Text("a").font(.title)``; ``Log`` and ``True``
    for ``Log.d("a")``. Swift and Kotlin nest the chain in ``call_expression`` /
    ``navigation_expression``; Dart lays it flat, a name followed by ``selector``
    siblings, and spells a ``const`` constructor as ``const_object_expression``.
    """
    kind = node.type
    if kind == "call_expression":
        root = node
        on_member = False
        while root.type in ("call_expression", "navigation_expression") and root.named_children:
            if root.type == "call_expression":
                on_member = root.named_children[0].type == "navigation_expression"
            root = root.named_children[0]
        return root, on_member
    if kind == "selector" and any(c.type == "argument_part" for c in node.children):
        root = node.prev_named_sibling
        while root is not None and root.type == "selector":
            root = root.prev_named_sibling
        if root is None:
            return None
        # ``Theme.of(context)``: a member is named before the first argument list.
        first = root.next_named_sibling
        named_first = first is not None and first.type == "selector"
        return root, named_first and not any(c.type == "argument_part" for c in first.children)
    if kind in ("const_object_expression", "new_expression"):
        root = next((c for c in node.named_children if c.type == "type_identifier"), None)
        return (root, False) if root is not None else None
    return None


def _is_view_call(node: Node, root: Node, on_member: bool, in_markup: bool) -> bool:
    """A call to a view, composable or widget: its name starts upper case (``Text(...)``).

    A call on an upper-case member (``Modifier.padding(8.dp)``) counts only as
    an argument of a view or a link of its chain; as a statement
    (``Log.d(...)``) it is code.
    """
    first = root.text[:1]
    if "identifier" not in root.type or not (first.isalpha() and first.isupper()):
        return False
    if not on_member:
        return True
    parent = node.parent
    return in_markup and parent is not None and parent.type in _CHAIN_PARENTS


def _ui_opens(node: Node, in_markup: bool) -> bool:
    """Whether *node*'s own tokens are markup, given its parent's state.

    A view call and its modifiers are markup; any other call is code.
    """
    call = _call_root(node)
    if call is not None:
        return _is_view_call(node, *call, in_markup)
    if node.type in _UI_STRUCTURE or _is_data_node(node):
        return in_markup
    return False


def _ui_token_rows(body: Node, markup: set[int], code: set[int]) -> None:
    """Sort a UI builder *body*'s token rows into *markup* and *code*.

    Views and their data arguments are markup; control flow, operators, a
    lower-case call (``model.save()``) and a callback's statements are code.
    A view nested anywhere is markup again, as JSX inside ``{cond && ...}`` is.
    """
    stack: list[tuple[Node, bool]] = [(body, False)]
    while stack:
        node, in_markup = stack.pop()
        if "comment" in node.type:
            continue
        if node.is_named:
            in_markup = _ui_opens(node, in_markup)
        if node.children:
            stack.extend((child, in_markup) for child in node.children)
        elif node.start_byte < node.end_byte:
            (markup if in_markup else code).update(_rows(node))


def _ui_rows(root: Node, grammar_tag: str, source: bytes) -> set[int]:
    """Rows of the file's UI builders whose every token is markup."""
    markers = _UI_MARKERS.get(grammar_tag)
    if markers is None or not any(marker in source for marker in markers):
        return set()
    markup: set[int] = set()
    code: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        body = _ui_builder_body(node, grammar_tag, source)
        if body is not None:
            _ui_token_rows(body, markup, code)
        else:
            stack.extend(node.named_children)
    return markup - code


def markup_rows(
    root: Node,
    grammar_tag: str,
    source: bytes,
    literals: list[Node],
    arms: list[Node],
    chains: list[Node],
) -> list[int]:
    """Sorted 0-indexed non-blank rows whose every token is markup or literal data.

    *literals* are the file's multi-line literals (strings, tables) outside docstrings;
    *arms* its switch / when / match arms; *chains* the calls ending its multi-line
    method chains.
    """
    rows = (
        _string_rows(literals, source)
        | _ui_rows(root, grammar_tag, source)
        | _arm_rows(arms, source)
        | _builder_rows(chains, source)
    )
    if grammar_tag in _JSX_GRAMMARS and (b"/>" in source or b"</" in source):
        markup, code = _token_rows(root)
        rows |= markup - code
    lines = _source_lines(source)
    return sorted(row for row in rows if row < len(lines) and lines[row].strip())


def rows_within(rows: list[int], node: Node) -> int:
    """How many of the sorted *rows* fall inside *node*'s line span."""
    return bisect_right(rows, node.end_point[0]) - bisect_left(rows, node.start_point[0])


def rows_in_ranges(rows: list[int], ranges: list[tuple[int, int]]) -> int:
    """How many of the sorted *rows* fall inside the inclusive, disjoint *ranges*."""
    return sum(bisect_right(rows, last) - bisect_left(rows, first) for first, last in ranges)
