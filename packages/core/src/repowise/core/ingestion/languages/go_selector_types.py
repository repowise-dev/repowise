"""The type a Go selector chain through package variables and struct fields holds.

Go code often reaches a method through a package-level variable and a run of
struct fields, never naming a type at the call::

    var skillsApi = v1.ApiGroupApp.SystemApiGroup.SkillsApi   // router/system/enter.go
    skillsRouter.GET("getTools", skillsApi.GetTools)           // router/system/sys_skills.go
    router.RouterGroupApp.System.InitSkillsRouter(group)       // initialize/router.go

Each name in such a chain has one static type the declarations spell: a
variable's declared type, its ``new(T)`` / ``T{}`` / ``&T{}`` initializer, or
the chain it is initialized from; a field's declared type in its struct; a
field or method an embedded struct promotes. :class:`GoSelectorTypes` follows
them hop by hop. Every hop must be unambiguous: a variable declared once in its
package, a type declared once in its directory, a field or method found at one
embedding depth only. Anything else (a call initializer, a slice or map type,
a type from another module, two promotions at one depth) refuses the chain.

The class reads source through four callbacks, so the call resolver (over
parsed files) and the route table (over the stored index) share one walk.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from tree_sitter import Node

#: How many variable and field hops one chain may take, counting the hops a
#: variable's own initializer chain adds. Bounds a cycle of initializers too.
_MAX_HOPS = 12
#: How deep embedding is searched for a promoted field or method.
_MAX_EMBED_DEPTH = 4
#: How many local ``x := y.z`` bindings one receiver may be followed through.
_MAX_LOCAL_HOPS = 4
#: Files whose function bindings stay read at once; calls resolve file by file.
_MAX_BINDING_FILES = 64

T = TypeVar("T")

#: ``(qualifier or None, type name)`` as a file writes a type.
WrittenType = tuple[str | None, str]
#: ``(package directory, type name)``: a type the repository declares.
GoType = tuple[str, str]


@dataclass(frozen=True)
class VarSpec:
    """What a variable declaration says its type is: a written type or a chain."""

    written: WrittenType | None = None
    chain: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Field:
    name: str
    written: WrittenType | None  # None: a type no hop can follow (slice, map, func)
    embedded: bool


@dataclass(frozen=True)
class GoFileFacts:
    """The package-level declarations of one Go file a selector walk reads."""

    imports: dict[str, str]  # local name -> import path
    #: name -> spec; None for a variable declared with no followable type, or twice.
    variables: dict[str, VarSpec | None]
    structs: dict[str, tuple[Field, ...]]  # struct type name -> fields
    types: frozenset[str]  # every type name the file declares


_NO_FACTS = GoFileFacts({}, {}, {}, frozenset())


def go_file_facts(text: str) -> GoFileFacts:
    """Read *text*'s imports, package variables and struct types."""
    from .go_interface_satisfaction import _parse_go

    src = text.encode("utf-8")
    root = _parse_go(src)
    if root is None:
        return _NO_FACTS
    imports: dict[str, str] = {}
    variables: dict[str, VarSpec | None] = {}
    structs: dict[str, tuple[Field, ...]] = {}
    types: set[str] = set()
    for node in root.children:
        if node.type == "import_declaration":
            for spec in _specs(node, "import_spec", "import_spec_list"):
                _add_import(spec, imports)
        elif node.type == "var_declaration":
            for spec in _specs(node, "var_spec", "var_spec_list"):
                for name, value in var_spec_values(spec):
                    variables[name] = None if name in variables else value
        elif node.type == "type_declaration":
            for spec in node.children:
                if spec.type not in ("type_spec", "type_alias"):
                    continue
                name_node = spec.child_by_field_name("name")
                if name_node is None:
                    continue
                name = _text(name_node)
                types.add(name)
                body = spec.child_by_field_name("type")
                if spec.type == "type_spec" and body is not None and body.type == "struct_type":
                    structs[name] = _struct_fields(body)
    return GoFileFacts(imports, variables, structs, frozenset(types))


def var_spec_values(spec: Node) -> list[tuple[str, VarSpec | None]]:
    """``(name, spec)`` for each name a ``var_spec`` or ``short_var_declaration`` binds.

    A declared type types every name. Otherwise one name with one value is
    typed from it; several names (``a, b := f()``) are bound but untyped.
    """
    if spec.type == "short_var_declaration":
        left, right, type_node = (
            spec.child_by_field_name("left"),
            spec.child_by_field_name("right"),
            None,
        )
        names = [_text(n) for n in (left.named_children if left else ()) if n.type == "identifier"]
    else:
        names = [_text(n) for n in spec.children_by_field_name("name")]
        type_node = spec.child_by_field_name("type")
        right = spec.child_by_field_name("value")
    if type_node is not None:
        written = written_type(type_node)
        value = VarSpec(written=written) if written else None
        return [(name, value) for name in names]
    values = list(right.named_children) if right is not None else []
    if len(names) != 1 or len(values) != 1:
        return [(name, None) for name in names]
    return [(names[0], _value_spec(values[0]))]


#: Nodes whose identifiers a ``:=`` binds, with the field holding them.
_BINDING_LEFT = {
    "range_clause": "left",
    "receive_statement": "left",
    "type_switch_statement": "alias",
}

#: ``(first line, last line, {name: [spec per binding]})`` of one top-level function.
FunctionBindings = tuple[int, int, dict[str, list[VarSpec | None]]]


def function_bindings(text: str) -> tuple[FunctionBindings, ...]:
    """Every name each top-level function of *text* binds, with each binding's spec.

    Parameters, results and receivers (of the function and of any function
    literal in it), ``:=``, ``var``, ``const``, ``range``, ``select`` and
    type-switch bindings all count, in any block: a name bound anywhere in a
    body is treated as maybe the one a selector in it names.
    """
    from .go_interface_satisfaction import _parse_go

    root = _parse_go(text.encode("utf-8"))
    if root is None:
        return ()
    return tuple(
        (n.start_point[0] + 1, n.end_point[0] + 1, _bindings_in(n))
        for n in root.children
        if n.type in ("function_declaration", "method_declaration")
    )


def _bindings_in(function: Node) -> dict[str, list[VarSpec | None]]:
    bound: dict[str, list[VarSpec | None]] = {}
    stack = [function]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        kind = node.type
        if kind in ("var_spec", "short_var_declaration"):
            for name, spec in var_spec_values(node):
                bound.setdefault(name, []).append(spec)
        elif kind in ("parameter_declaration", "variadic_parameter_declaration", "const_spec"):
            for name_node in node.children_by_field_name("name"):
                bound.setdefault(_text(name_node), []).append(None)
        elif kind in _BINDING_LEFT and any(c.type == ":=" for c in node.children):
            left = node.child_by_field_name(_BINDING_LEFT[kind])
            if left is None:
                continue
            idents = [left] if left.type == "identifier" else left.named_children
            for ident in idents:
                if ident.type == "identifier":
                    bound.setdefault(_text(ident), []).append(None)
    return bound


def written_type(node: Node) -> WrittenType | None:
    """``T``, ``*T``, ``pkg.T`` or ``*pkg.T`` as ``(qualifier, name)``; else None."""
    if node.type == "pointer_type" and node.named_children:
        node = node.named_children[0]
    if node.type == "type_identifier":
        return None, _text(node)
    if node.type == "qualified_type":
        package, name = node.child_by_field_name("package"), node.child_by_field_name("name")
        if package is not None and name is not None:
            return _text(package), _text(name)
    return None


def selector_chain(node: Node) -> tuple[str, ...] | None:
    """``a.b.c`` as ``("a", "b", "c")`` when every link is a plain name."""
    parts: list[str] = []
    while node.type == "selector_expression":
        field = node.child_by_field_name("field")
        operand = node.child_by_field_name("operand")
        if field is None or operand is None:
            return None
        parts.append(_text(field))
        node = operand
    if node.type != "identifier":
        return None
    parts.append(_text(node))
    return tuple(reversed(parts))


def _value_spec(value: Node) -> VarSpec | None:
    """The spec an initializer gives: ``new(T)``, ``T{}``, ``&T{}`` or a chain."""
    if value.type == "unary_expression" and _text(value).startswith("&"):
        operand = value.child_by_field_name("operand")
        value = operand if operand is not None else value
    if value.type == "composite_literal":
        type_node = value.child_by_field_name("type")
        written = written_type(type_node) if type_node is not None else None
        return VarSpec(written=written) if written else None
    if value.type == "call_expression":
        function = value.child_by_field_name("function")
        arguments = value.child_by_field_name("arguments")
        if function is None or _text(function) != "new" or arguments is None:
            return None
        args = arguments.named_children
        written = written_type(args[0]) if len(args) == 1 else None
        return VarSpec(written=written) if written else None
    if value.type in ("selector_expression", "identifier"):
        chain = selector_chain(value)
        return VarSpec(chain=chain) if chain else None
    return None


def _struct_fields(body: Node) -> tuple[Field, ...]:
    fields: list[Field] = []
    for decl_list in body.named_children:
        if decl_list.type != "field_declaration_list":
            continue
        for decl in decl_list.named_children:
            if decl.type != "field_declaration":
                continue
            type_node = decl.child_by_field_name("type")
            names = [_text(n) for n in decl.children_by_field_name("name")]
            written = written_type(type_node) if type_node is not None else None
            if names:
                fields.extend(Field(name, written, False) for name in names)
            elif written is not None:
                # An embedded ``Base``, ``*Base`` or ``pkg.Base`` is a field named ``Base``.
                fields.append(Field(written[1], written, True))
    return tuple(fields)


def _specs(node: Node, spec_type: str, list_type: str) -> list[Node]:
    out: list[Node] = []
    for child in node.children:
        if child.type == spec_type:
            out.append(child)
        elif child.type == list_type:
            out.extend(c for c in child.children if c.type == spec_type)
    return out


def _add_import(spec: Node, imports: dict[str, str]) -> None:
    path_node = spec.child_by_field_name("path")
    if path_node is None:
        return
    path = _text(path_node).strip('"`')
    name_node = spec.child_by_field_name("name")
    if name_node is not None:
        local = _text(name_node)
        if local in ("_", "."):
            return
    else:
        # The package name, which by convention is the path's last element.
        # The ceiling: a package declaring another name (``gopkg.in/yaml.v3``
        # as ``yaml``) is not read here; its chains stay unresolved.
        local = path.rsplit("/", 1)[-1]
    imports[local] = path


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace") if node.text else ""


class _Untyped:
    """A field found whose type no hop can follow."""


_UNTYPED = _Untyped()


class GoSelectorTypes(Generic[T]):
    """Follow Go selector chains to the type they hold, and that type's methods.

    *files_in(directory)* lists a package's Go files, *text(path)* reads one,
    *imported_dir(path, local_name)* is the repository directory the file
    imports under that name (None for a package outside the repository), and
    *method_of(directory, type, method)* is the caller's handle for the method
    the type declares itself, or None.
    """

    def __init__(
        self,
        files_in: Callable[[str], Sequence[str]],
        text: Callable[[str], str],
        imported_dir: Callable[[str, str], str | None],
        method_of: Callable[[str, str, str], T | None],
    ) -> None:
        self._files_in = files_in
        self._read = text
        self._imported_dir = imported_dir
        self._method_of = method_of
        # Unbounded: a file's facts are a few small tables, and the files a
        # chain reaches (another package's ``enter.go``) are asked again and again.
        self._facts: dict[str, GoFileFacts] = {}
        self._functions: dict[str, tuple[FunctionBindings, ...]] = {}

    def facts(self, path: str) -> GoFileFacts:
        found = self._facts.get(path)
        if found is None:
            found = go_file_facts(self._read(path))
            self._facts[path] = found
        return found

    def method_at(self, path: str, line: int, expression: Sequence[str]) -> T | None:
        """The method *expression* (``v.M``, ``v.f.M``, or a local bound to one) names on *line*."""
        if len(expression) == 1:
            local = self._local(path, line, expression[0])
            chain = None if local is None else local.chain
            if chain is None or len(chain) < 2:
                return None
            expression = chain
        owner = self.receiver_type(path, line, expression[:-1])
        return None if owner is None else self.promoted_method(owner, expression[-1])

    def receiver_type(
        self, path: str, line: int, chain: Sequence[str], budget: int = _MAX_LOCAL_HOPS
    ) -> GoType | None:
        """The type *chain* holds on *line* of *path*.

        Its head is a local of the function holding the line when that
        function binds it exactly once, from a followable spec; a name bound
        any other way there refuses, since it may shadow a package name. A
        head the function never binds is a package variable or an import.
        """
        if not chain:
            return None
        bindings = self._function_at(path, line)
        if bindings is None:
            return None  # a package-level initializer: no function scope is read
        if chain[0] not in bindings:
            return self.chain_type(path, chain)
        local = self._local(path, line, chain[0])
        if local is None or budget <= 0:
            return None
        if local.written is not None:
            owner = self.resolve_written(path, local.written)
        else:
            owner = self.receiver_type(path, line, local.chain or (), budget - 1)
        return self.walk_fields(owner, chain[1:])

    def _local(self, path: str, line: int, name: str) -> VarSpec | None:
        """The spec of *name*'s one binding in the function holding *line*, or None."""
        specs = (self._function_at(path, line) or {}).get(name, ())
        return specs[0] if len(specs) == 1 else None

    def _function_at(self, path: str, line: int) -> dict[str, list[VarSpec | None]] | None:
        functions = self._functions.get(path)
        if functions is None:
            if len(self._functions) >= _MAX_BINDING_FILES:
                self._functions.clear()
            functions = function_bindings(self._read(path))
            self._functions[path] = functions
        for first, last, bindings in functions:
            if first <= line <= last:
                return bindings
        return None

    def chain_type(self, path: str, chain: Sequence[str], budget: int = _MAX_HOPS) -> GoType | None:
        """The type ``a.b.c`` written in *path* holds, with ``a`` a package variable
        of *path*'s package or an import of it (then ``b`` is that package's variable).
        """
        if not chain or budget <= 0:
            return None
        head, rest = chain[0], list(chain[1:])
        if head in self.facts(path).imports:
            package = self._imported_dir(path, head)
            if package is None or not rest:
                return None
            head, rest = rest[0], rest[1:]
        else:
            package = posixpath.dirname(path)
        return self.walk_fields(self.variable_type(package, head, budget - 1), rest)

    def walk_fields(self, owner: GoType | None, fields: Sequence[str]) -> GoType | None:
        """The type reached from *owner* through *fields*, one field per hop."""
        for field in fields:
            if owner is None:
                return None
            owner = self.field_type(owner, field)
        return owner

    def variable_type(self, package: str, name: str, budget: int = _MAX_HOPS) -> GoType | None:
        """The type of the package variable *name*, declared once in *package*."""
        declared = [
            (path, facts.variables[name])
            for path in self._files_in(package)
            if name in (facts := self.facts(path)).variables
        ]
        if len(declared) != 1 or declared[0][1] is None:
            return None
        path, spec = declared[0]
        return self.spec_type(path, spec, budget)

    def spec_type(self, path: str, spec: VarSpec | None, budget: int = _MAX_HOPS) -> GoType | None:
        """The type *spec*, written in *path*, gives its variable."""
        if spec is None:
            return None
        if spec.written is not None:
            return self.resolve_written(path, spec.written)
        return self.chain_type(path, spec.chain or (), budget)

    def resolve_written(self, path: str, written: WrittenType) -> GoType | None:
        """The repository type *written* names in *path*: one declaration in its package."""
        qualifier, name = written
        package = (
            posixpath.dirname(path) if qualifier is None else self._imported_dir(path, qualifier)
        )
        if package is None or self._declaring_file(package, name) is None:
            return None
        return package, name

    def field_type(self, owner: GoType, field: str) -> GoType | None:
        """The type of *owner*'s field *field*, direct or promoted from one embedding."""
        hit = self._promoted(owner, lambda t: self._own_field(t, field))
        return hit if isinstance(hit, tuple) else None

    def promoted_method(self, owner: GoType, method: str) -> T | None:
        """*owner*'s method *method*, its own or promoted from one embedding."""
        return self._promoted(owner, lambda t: self._method_of(t[0], t[1], method))

    def _own_field(self, owner: GoType, field: str) -> Any:
        path = self._declaring_file(*owner)
        for f in self.facts(path).structs.get(owner[1], ()) if path else ():
            if f.name == field:
                resolved = None if f.written is None else self.resolve_written(path, f.written)
                return _UNTYPED if resolved is None else resolved
        return None

    def _promoted(self, owner: GoType, lookup: Callable[[GoType], Any]) -> Any:
        """Go's selector rule: the shallowest depth with a match, and one match there.

        An embedded type outside the repository may hold the member at its
        depth, so a level with one stops the search going deeper.
        """
        level, seen = [owner], {owner}
        for _ in range(_MAX_EMBED_DEPTH + 1):
            hits = [hit for t in level if (hit := lookup(t)) is not None]
            if hits:
                return hits[0] if len(hits) == 1 and hits[0] is not _UNTYPED else None
            deeper: list[GoType] = []
            for t in level:
                path = self._declaring_file(*t)
                for f in self.facts(path).structs.get(t[1], ()) if path else ():
                    if not f.embedded:
                        continue
                    embedded = None if f.written is None else self.resolve_written(path, f.written)
                    if embedded is None:
                        return None
                    if embedded not in seen:
                        seen.add(embedded)
                        deeper.append(embedded)
            if not deeper:
                return None
            level = deeper
        return None

    def _declaring_file(self, package: str, type_name: str) -> str | None:
        """The one file of *package* declaring *type_name*, or None."""
        found = [p for p in self._files_in(package) if type_name in self.facts(p).types]
        return found[0] if len(found) == 1 else None
