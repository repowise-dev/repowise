"""``COPY`` and ``EXEC SQL INCLUDE`` statements as graph imports."""

from __future__ import annotations

from ..models import Import
from .source import code_view
from .statements import copy_statements


def copy_imports(source: bytes) -> list[Import]:
    """One import per copied member, read from the unmasked source."""
    imports: list[Import] = []
    seen: set[str] = set()
    for statement in copy_statements(code_view(source)):
        if statement.member in seen:
            continue
        seen.add(statement.member)
        verb = "EXEC SQL INCLUDE" if statement.include else "COPY"
        library = f" OF {statement.library}" if statement.library else ""
        # ``LIB/MEMBER`` lets the resolver prefer a library directory.
        module = (
            f"{statement.library}/{statement.member}" if statement.library else statement.member
        )
        imports.append(
            Import(
                raw_statement=f"{verb} {statement.member}{library}",
                module_path=module,
                # A copybook is textually included: every name in it is in scope.
                imported_names=["*"],
                is_relative=False,
                resolved_file=None,
            )
        )
    return imports
