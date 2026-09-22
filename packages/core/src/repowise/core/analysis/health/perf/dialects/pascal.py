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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

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


class PascalPerfDialect(BasePerfDialect):
    language = "pascal"
    markers = frozenset({"io_in_loop", "hot_path_sync_io"})

    # -- callee extraction (Pascal's own field names) -------------------------

    def callee_root_name(self, call_node: Node) -> str | None:
        entity = call_node.child_by_field_name("entity")
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
        entity = call_node.child_by_field_name("entity")
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
        entity = call_node.child_by_field_name("entity")
        return entity is not None and entity.type == "exprDot"

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


DIALECT = PascalPerfDialect()
