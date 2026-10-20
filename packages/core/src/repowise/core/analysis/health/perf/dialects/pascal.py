"""Pascal (Delphi/FPC) ``PerfDialect``.

Flagship: a filesystem or process-spawn round-trip inside a loop
(``io_in_loop``) — ``CopyFile`` / ``TFileStream.SaveToFile`` / ``WinExec``
called once per iteration instead of once, the classic batch-file-ops
anti-pattern in a file manager / build tool codebase.

Pascal's one call-expression node (``exprCall``, see ``LanguageNodeMap``'s
``call_kinds``) spells its callee under the ``entity`` field rather than the
generic base extraction's ``function`` field, and a qualified call
(``Obj.Method(x)``) is an ``exprDot`` (``lhs``/``operator``/``rhs``) rather
than a ``member_expression`` — so callee extraction is overridden outright
rather than reusing ``BasePerfDialect``'s field-name probing.

A call to a zero-argument procedure may omit its parentheses entirely
(``Q.Open;`` is as idiomatic as ``Q.Open();``), which produces no ``exprCall``
node at all — just a bare ``identifier`` / ``exprDot`` sitting directly under
a ``statement`` wrapper. :meth:`PascalPerfDialect.bare_statement_call` (via
``LanguageNodeMap.bare_call_wrapper_kinds``) tells that shape apart from the
statement wrapper's other tenants (``Exit;`` / ``inherited;``), and
:meth:`_entity_of` treats the resulting bare node as its own entity, so every
sink lexicon below applies identically regardless of parentheses.

Filesystem and subprocess sinks below are unambiguous, distinctively-named
RTL/VCL/FPC calls that never collide with an unrelated API, so they fire on
the name alone. DB and network verbs (``Open`` / ``ExecSQL`` / ``Get`` /
``Post``) are NOT distinctive -- they collide with ordinary collection and
stream methods everywhere in this codebase family -- so they additionally
require ``uses``-clause evidence (``io_boundaries.collect_io_names``'s
Pascal branch, keyed off the shared ``io_kind`` table: ``FireDAC`` / ``ADODB``
-> db, ``IdHTTP`` / ``System.Net.HttpClient`` -> network) before they fire.
That evidence is file-wide, not receiver-scoped like Python's ``client.get()``
-> ``client`` bound to ``requests`` -- a variable named ``FDConnection`` has
no textual link back to the ``FireDAC`` unit it came from -- so a file that
imports FireDAC AND separately calls an unrelated ``.Open`` on something else
can still false-fire. Precision trades against the alternative, which is no
DB/network signal for Pascal at all.

No ``async``/``await`` in the language, so ``blocking_sync_in_async`` is not
in :attr:`markers`. String accumulation is idiomatically ``s := s + x``, not
a ``+=`` compound assignment (``{$COPERATORS ON}`` is off by default), which
the generic ``is_string_concat`` predicate cannot express without risking
false positives on ordinary reassignment -- ``string_concat_in_loop`` is
therefore also left out rather than guessed.

What a loop walks comes from the shared reader in ``iterables.py``. Two shapes
are Pascal's own. A counted loop has no ``i < n`` condition: its inclusive
bound is the ``end`` value (``High(xs)``, ``xs.Count - 1``), or the start
value under ``downto``. A data set is opened in place rather than bound to a
query's result, so ``while not Q.Eof`` walks what the ``Q.Open`` before the
loop read, and the statement text assigned to ``Q.SQL`` before it is what
tells one row from all of them. Identifiers are case-insensitive, so names and
method vocabularies are compared lower-cased.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .iterables import IterableSources, text_of

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import SinkProbe
    from .iterables import Source

# Filesystem round-trips. Bare RTL functions (SysUtils / System.IOUtils /
# WinAPI) with no receiver, so they fire regardless of ``is_attribute``.
_FS_BARE: frozenset[str] = frozenset(
    {
        "DeleteFile",
        "RenameFile",
        "CopyFile",
        "MoveFile",
        "MoveFileEx",
        "ForceDirectories",
        "RemoveDir",
        "RmDir",
        "CreateDir",
        "MkDir",
        "FileOpen",
        "FileCreate",
        "FileRead",
        "FileWrite",
        "FileClose",
        "FileSeek",
        "FileGetDate",
        "FileSetDate",
        "FileExists",
        "DirectoryExists",
        "FindFirst",
        "FindNext",
        "FindClose",
        "TDirectory.GetFiles",
        "TDirectory.GetDirectories",
        "TFile.Copy",
        "TFile.Move",
        "TFile.Delete",
        "TFile.ReadAllText",
        "TFile.WriteAllText",
        "TFile.ReadAllBytes",
        "TFile.WriteAllBytes",
    }
)
# Stream / string-list I/O methods. Always called on a receiver
# (``Stream.LoadFromFile`` / ``List.SaveToFile``), so gated on ``is_attribute``
# -- a bare helper of the same name elsewhere stays silent.
_FS_METHODS: frozenset[str] = frozenset(
    {
        "LoadFromFile",
        "SaveToFile",
        "LoadFromStream",
        "SaveToStream",
    }
)
# Process-spawn round-trips. ``Execute`` collides with ordinary business logic
# (any ``TCommand.Execute``), so it is excluded; the rest are distinctive
# enough by name alone.
_SUBPROCESS_BARE: frozenset[str] = frozenset(
    {
        "ExecuteProcess",
        "ShellExecute",
        "ShellExecuteEx",
        "WinExec",
        "CreateProcess",
        "CreateProcessW",
        "CreateProcessA",
    }
)
# TDataSet-family round-trips (FireDAC / ADO / dbGo / ZeosLib all share this
# vocabulary). Gated on ``uses``-clause DB evidence -- see the module
# docstring -- since ``Open`` / ``Post`` collide with unrelated stream and
# collection methods on their own.
_DB_METHODS: frozenset[str] = frozenset({"Open", "ExecSQL", "Post"})
# Indy / ``System.Net.HttpClient`` / FPC ``fphttpclient`` verb calls. Gated on
# ``uses``-clause network evidence for the same reason.
_NETWORK_METHODS: frozenset[str] = frozenset({"Get", "Post", "Put", "Patch", "Delete", "Head"})

# Keys a data set's ``Open`` and its statement text are filed under in the
# scope's bindings. No identifier holds a NUL, so they never meet a real name.
_OPEN_KEY = b"\x00open"
_SQL_KEY = b"\x00sql"
# ``Inc(n)`` / ``SetLength(xs, n)``: procedures that step or resize their first argument.
_STEPPERS: frozenset[str] = frozenset({"inc", "dec", "setlength", "include", "exclude"})
# ``TColor``: a type, by the convention every Delphi and FPC unit follows.
_TYPE_NAME = re.compile(r"T[A-Z]\w*\Z")


def _lower(node: Node | None) -> bytes:
    return (node.text or b"").lower() if node is not None else b""


class _Names(set[bytes]):
    """Names matched without case."""

    def __contains__(self, name: object) -> bool:
        return isinstance(name, bytes) and super().__contains__(name.lower())


class PascalPerfDialect(IterableSources):
    language = "pascal"
    markers = frozenset({"io_in_loop", "hot_path_sync_io"})

    # -- callee extraction (Pascal's own field names) -------------------------

    @staticmethod
    def _entity_of(call_node: Node) -> Node | None:
        """The callee expression a call site names.

        An ``exprCall`` (parenthesized call) carries it in its ``entity``
        field. A parenless call has no wrapping ``exprCall`` at all --
        :meth:`bare_statement_call` hands back the bare ``identifier`` /
        ``exprDot`` itself as *call_node*, which already IS its own entity.
        """
        if call_node.type == "exprCall":
            return call_node.child_by_field_name("entity")
        if call_node.type in ("identifier", "exprDot"):
            return call_node
        return None

    def callee_root_name(self, call_node: Node) -> str | None:
        entity = self._entity_of(call_node)
        if entity is None:
            return None
        node = entity
        for _ in range(8):
            if node.type != "exprDot":
                break
            lhs = node.child_by_field_name("lhs")
            if lhs is None:
                break
            node = lhs
        if node.text is None:
            return None
        return node.text.decode("utf-8", "replace").split(".")[-1]

    def callee_method_name(self, call_node: Node) -> str | None:
        entity = self._entity_of(call_node)
        if entity is None:
            return None
        if entity.type == "exprDot":
            rhs = entity.child_by_field_name("rhs")
            if rhs is not None and rhs.text is not None:
                return rhs.text.decode("utf-8", "replace")
            return None
        if entity.text is None:
            return None
        return entity.text.decode("utf-8", "replace")

    def callee_is_attribute(self, call_node: Node) -> bool:
        entity = self._entity_of(call_node)
        return entity is not None and entity.type == "exprDot"

    def bare_statement_call(self, node: Node) -> Node | None:
        """*node* is a ``statement`` wrapper; return its sole named child if
        that child is a bare ``identifier`` / ``exprDot`` -- a parenless call
        (``Q.Open;`` / ``FindClose;``). The same wrapper also carries
        non-call statements (``Exit;`` / ``inherited;`` -- also a bare
        ``identifier`` in the grammar's case of ``Exit``!), which this cannot
        structurally rule out; sink lexicon membership is what keeps a bare
        ``Exit`` harmless (it names no sink). A statement already headed by
        its own ``exprCall`` (the parenthesized form) returns ``None`` here --
        that call site is reached separately, through ``call_kinds``, so
        returning it again would double-count the hit.
        """
        named = node.named_children
        if len(named) != 1:
            return None
        child = named[0]
        return child if child.type in ("identifier", "exprDot") else None

    # -- sink classification (the lexicon) ------------------------------------

    def sink_kind(
        self,
        root: str,
        method: str,
        *,
        awaited: bool,
        is_attribute: bool,
        io_names: dict[str, str],
        has_db_import: bool,
    ) -> str | None:
        qualified = f"{root}.{method}"
        if method in _FS_BARE or qualified in _FS_BARE:
            return "filesystem"
        if is_attribute and method in _FS_METHODS:
            return "filesystem"
        if method in _SUBPROCESS_BARE:
            return "subprocess"
        if is_attribute and has_db_import and method in _DB_METHODS:
            return "db"
        if is_attribute and method in _NETWORK_METHODS and "network" in io_names.values():
            return "network"
        return None

    # -- what a loop walks (perf/dialects/iterables.py) ---------------------------

    function_kinds = frozenset({"defProc"})
    change_kinds = frozenset({"assignment"})
    grow_methods = frozenset(
        {"add", "addobject", "addrange", "addstrings", "insert", "append", "push", "enqueue"}
    )
    conditional_kinds = frozenset(
        {
            "if", "ifElse", "case", "caseCase", "for", "foreach", "while", "repeat",
            "try", "exceptionHandler",
        }
    )  # fmt: skip

    literal_kinds = frozenset({"exprBrackets"})
    int_kinds = frozenset({"literalNumber"})
    wrapper_kinds = frozenset({"exprParens"})
    member_kinds = frozenset({"exprDot"})
    pass_through = frozenset({"keys", "values", "toarray"})
    size_names = frozenset({"count", "length", "high"})
    listings = frozenset({"getfiles", "getdirectories", "getfilesystementries"})
    recursive_marks = (b"soAllDirectories",)

    def loop_header(self, loop: Node) -> tuple[str, Node | None]:
        if loop.type == "foreach":
            return "each", loop.child_by_field_name("iterable")
        if loop.type == "for":
            if any(child.type == "kDownto" for child in loop.children):
                start = loop.child_by_field_name("start")
                return "counted", start.child_by_field_name("rhs") if start is not None else None
            return "counted", loop.child_by_field_name("end")
        dataset = self._eof_dataset(loop)
        if dataset is None:
            return "each", None
        # Unopened here, the data set is whatever its receiver is (a parameter).
        return "each", self.reaching_value(loop, _lower(dataset) + _OPEN_KEY) or dataset

    def _eof_dataset(self, loop: Node) -> Node | None:
        """``Q`` of ``while not Q.Eof`` / ``repeat ... until Q.Eof``."""
        cond = loop.child_by_field_name("condition")
        if cond is None:
            return None
        cond = self._unwrap(cond)
        if loop.type == "while":
            operator = cond.child_by_field_name("operator")
            operand = cond.child_by_field_name("operand")
            if cond.type != "exprUnary" or operator is None or operator.type != "kNot":
                return None
            if operand is None:
                return None
            cond = self._unwrap(operand)
        if cond.type == "exprDot" and _lower(cond.child_by_field_name("rhs")) == b"eof":
            return cond.child_by_field_name("lhs")
        return None

    def call_parts(self, node: Node) -> tuple[str, Node | None, list[Node]] | None:
        if node.type == "exprCall":
            args = node.child_by_field_name("args")
            arg_nodes = (
                [a for a in args.named_children if a.type != "comment"] if args is not None else []
            )
        elif (
            node.parent is not None
            and node.parent.type == "statement"
            and self.bare_statement_call(node.parent) == node
        ):
            arg_nodes = []
        else:
            return None
        entity = self._entity_of(node)
        if entity is None:
            return None
        if entity.type == "exprDot":
            receiver = entity.child_by_field_name("lhs")
            name = entity.child_by_field_name("rhs")
            return (
                (_lower(name).decode("utf-8", "replace"), receiver, arg_nodes)
                if name is not None
                else None
            )
        if entity.type != "identifier":
            return None
        return _lower(entity).decode("utf-8", "replace"), None, arg_nodes

    def member_parts(self, node: Node) -> tuple[str, Node] | None:
        if node.type != "exprDot":
            return None
        obj, name = node.child_by_field_name("lhs"), node.child_by_field_name("rhs")
        if obj is None or name is None:
            return None
        return _lower(name).decode("utf-8", "replace"), obj

    @staticmethod
    def _sql_owner(target: Node | None) -> Node | None:
        """``Q`` of ``Q.SQL.Text`` / ``Q.CommandText``: the data set a statement text is for."""
        if target is None or target.type != "exprDot":
            return None
        owner, name = target.child_by_field_name("lhs"), _lower(target.child_by_field_name("rhs"))
        if name == b"commandtext":
            return owner
        if name != b"text" or owner is None or owner.type != "exprDot":
            return None
        if _lower(owner.child_by_field_name("rhs")) != b"sql":
            return None
        return owner.child_by_field_name("lhs")

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        if node.type == "assignment":
            lhs, rhs = node.child_by_field_name("lhs"), node.child_by_field_name("rhs")
            if lhs is None or rhs is None:
                return []
            if lhs.type == "identifier":
                return [(_lower(lhs), rhs)]
            dataset = self._sql_owner(lhs)
            return [(_lower(dataset) + _SQL_KEY, rhs)] if dataset is not None else []
        parts = self.call_parts(node)
        if parts is None:
            return []
        method, receiver, args = parts
        if receiver is None:
            return []
        if method == "open":
            return [(_lower(receiver) + _OPEN_KEY, node)]
        # ``Q.SQL.Add('...')``: one line of the text; several leave it unread.
        if method != "add" or len(args) != 1 or receiver.type != "exprDot":
            return []
        if _lower(receiver.child_by_field_name("rhs")) != b"sql":
            return []
        return [(_lower(receiver.child_by_field_name("lhs")) + _SQL_KEY, args[0])]

    def mutated_name(self, node: Node) -> bytes | None:
        if node.type in self.change_kinds:
            return _lower(node.child_by_field_name("lhs")).split(b"[", 1)[0] or None
        parts = self.call_parts(node)
        if parts is None:
            return None
        method, receiver, args = parts
        if receiver is not None:
            return _lower(receiver) if method in self.grow_methods else None
        return _lower(args[0]) if method in _STEPPERS and args else None

    def bindings_before(self, anchor: Node, name: bytes) -> list[tuple[Node, Node]]:
        return super().bindings_before(anchor, name.lower())

    def _changed_before(self, value: Node, loop: Node, name: bytes) -> bool:
        return super()._changed_before(value, loop, name.lower())

    def param_names(self, loop: Node) -> set[bytes]:
        scope = self._scope(loop)
        header = scope.child_by_field_name("header") if scope is not None else None
        args = header.child_by_field_name("args") if header is not None else None
        names = _Names()
        for arg in args.named_children if args is not None else ():
            if arg.type == "declArg":
                names.update(
                    _lower(n) for n in arg.children_by_field_name("name") if n.type == "identifier"
                )
        return names

    def special_call(
        self,
        call: Node,
        parts: tuple[str, Node | None, list[Node]],
        loop: Node,
        probe: SinkProbe,
    ) -> Source | None:
        method, receiver, args = parts
        if method != "open" or receiver is None or probe(call) != "db":
            return None
        sql = args[0] if args else self.reaching_value(loop, _lower(receiver) + _SQL_KEY)
        if sql is not None and sql.type == "identifier":
            sql = self.reaching_value(loop, _lower(sql))
        # A text set in a form file or built up line by line is not in view,
        # and a read whose text is unseen may be one row as easily as all of them.
        if sql is None or not any(n.type == "literalString" for n in self._walk(sql)):
            return "query", None
        return self._query(sql, loop)

    def _name(self, name: Node, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        found = super()._name(name, loop, probe, hop=hop)
        # ``for E in TColor`` / ``High(TColor)``: an ordinal type has a fixed set of values.
        if found == ("unknown", None) and _TYPE_NAME.match(text_of(name)):
            return "constant", "bounded"
        return found

    def _counted(self, stop: Node, loop: Node, probe: SinkProbe, *, hop: bool = True) -> Source:
        return super()._counted(self._size_of_last_index(stop), loop, probe, hop=hop)

    def _size_of_last_index(self, node: Node) -> Node:
        """``Length(xs)`` of ``Length(xs) - 1`` / ``Pred(Length(xs))``: an inclusive
        bound is written one short of the size it walks."""
        node = self._unwrap(node)
        if node.type == "exprBinary":
            operator = node.child_by_field_name("operator")
            lhs, rhs = node.child_by_field_name("lhs"), node.child_by_field_name("rhs")
            if operator is None or operator.type != "kSub" or lhs is None or rhs is None:
                return node
            if self._unwrap(rhs).type in self.int_kinds:
                return self._size_of_last_index(lhs)
            return node
        parts = self.call_parts(node)
        if parts is not None and parts[0] == "pred" and parts[1] is None and len(parts[2]) == 1:
            return self._size_of_last_index(parts[2][0])
        return node


DIALECT = PascalPerfDialect()
