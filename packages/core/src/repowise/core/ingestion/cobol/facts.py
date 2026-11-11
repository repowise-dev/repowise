"""Graph facts read from a COBOL file's unmasked source: imports and call sites."""

from __future__ import annotations

from ..models import CallSite, Import, Symbol
from ..parser_helpers import _find_enclosing_symbol
from .cics import DYNAMIC_PREFIX, program_transfers
from .source import code_view
from .statements import copy_statements


def cobol_imports(source: bytes) -> list[Import]:
    """One import per copied member, plus one per dynamic program transfer variable."""
    code = code_view(source)
    imports: list[Import] = []
    seen: set[str] = set()
    for statement in copy_statements(code):
        # ``LIB/MEMBER`` lets the resolver prefer a library directory.
        module = (
            f"{statement.library}/{statement.member}" if statement.library else statement.member
        )
        if module in seen:
            continue
        seen.add(module)
        verb = "EXEC SQL INCLUDE" if statement.include else "COPY"
        library = f" OF {statement.library}" if statement.library else ""
        imports.append(_import(f"{verb} {statement.member}{library}", module))
    for transfer in program_transfers(code):
        if transfer.dynamic and DYNAMIC_PREFIX + transfer.dynamic not in seen:
            seen.add(DYNAMIC_PREFIX + transfer.dynamic)
            raw = f"EXEC CICS {transfer.verb} PROGRAM({transfer.dynamic})"
            imports.append(_import(raw, DYNAMIC_PREFIX + transfer.dynamic))
    return imports


def cobol_call_sites(source: bytes, symbols: list[Symbol]) -> list[CallSite]:
    """A call site per static LINK/XCTL target, from the paragraph holding it."""
    ranges = sorted(
        ((s.start_line, s.end_line, s.id) for s in symbols), key=lambda t: (t[0], -t[1])
    )
    return [
        CallSite(
            target_name=target,
            receiver_name=None,
            caller_symbol_id=_find_enclosing_symbol(transfer.line, ranges),
            line=transfer.line,
            argument_count=None,
            program_call=True,
        )
        for transfer in program_transfers(code_view(source))
        for target in transfer.targets
    ]


def _import(raw: str, module: str) -> Import:
    # A copybook is textually included: every name in it is in scope.
    return Import(
        raw_statement=raw,
        module_path=module,
        imported_names=["*"],
        is_relative=False,
        resolved_file=None,
    )
