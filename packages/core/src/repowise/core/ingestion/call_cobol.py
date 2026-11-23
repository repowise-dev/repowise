"""COBOL call binding mixed into ``CallResolver``.

COBOL names are scoped differently from every other language here: a
``PERFORM`` reaches only a paragraph of its own program, including the
paragraphs a copybook it copies brings in, while ``CALL``, ``LINK`` and
``XCTL`` name a program by its PROGRAM-ID. A data item is never a target, and
neither is another program's paragraph.
"""

from __future__ import annotations

from .models import CallSite
from .resolved_call import ResolvedCall

_PROCEDURE_KINDS = frozenset({"function"})
_PROGRAM_KINDS = frozenset({"module"})


class CobolCallsMixin:
    def _resolve_cobol_call(
        self, file_path: str, call: CallSite, caller_id: str
    ) -> ResolvedCall | None:
        name = call.target_name
        if call.program_call:
            return self._cobol_program(file_path, name, call, caller_id)
        own = self._cobol_symbol(file_path, name, _PROCEDURE_KINDS)
        if own is not None:
            return (
                None
                if own == caller_id
                else ResolvedCall(caller_id, own, 0.95, call.line, "same_file")
            )
        for copied in sorted(self._import_targets.get(file_path, ())):  # type: ignore[attr-defined]
            hit = self._cobol_symbol(copied, name, _PROCEDURE_KINDS)
            if hit is not None:
                return ResolvedCall(caller_id, hit, 0.90, call.line, "import_scoped")
        return None

    def _cobol_program(
        self, file_path: str, name: str, call: CallSite, caller_id: str
    ) -> ResolvedCall | None:
        programs = [
            sym_id
            for sym_id in self._global_symbols.get(name, ())  # type: ignore[attr-defined]
            if self._symbols_by_id[sym_id].kind in _PROGRAM_KINDS  # type: ignore[attr-defined]
            and self._symbols_by_id[sym_id].language == "cobol"  # type: ignore[attr-defined]
        ]
        # A program naming itself (a bookkeeping constant) is not a transfer.
        if len(programs) != 1 or self._symbol_paths_by_id.get(programs[0]) == file_path:  # type: ignore[attr-defined]
            return None
        return ResolvedCall(caller_id, programs[0], 0.50, call.line, "global_unique")

    def _cobol_symbol(self, file_path: str, name: str, kinds: frozenset[str]) -> str | None:
        sym_id = self._file_symbols.get(file_path, {}).get(name)  # type: ignore[attr-defined]
        if sym_id is None or self._symbols_by_id[sym_id].kind not in kinds:  # type: ignore[attr-defined]
            return None
        return sym_id
