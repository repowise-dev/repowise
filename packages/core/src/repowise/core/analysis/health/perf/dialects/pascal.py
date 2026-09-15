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

No import-based gating: Pascal's ``uses`` clause lists bare unit names with no
stdlib/third-party distinction the shared ``io_kind`` table recognises (unlike
Python's ``import os`` or Go's ``"database/sql"``), so ``io_names`` /
``has_db_import`` are always empty here. Every sink below is therefore an
unambiguous, distinctively-named RTL/VCL/FPC call that does not collide with
an unrelated API — the same precision-first posture the "no signal" default
already takes, just drawn from names instead of imports. DB and network calls
(``TDataSet.Open`` / ``THTTPClient.Get``) are intentionally NOT covered: their
verbs (``Open`` / ``Get`` / ``Post``) collide with ordinary collection and
stream methods everywhere in this codebase family, and there is no import
evidence available to disambiguate them.

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
        return None


DIALECT = PascalPerfDialect()
