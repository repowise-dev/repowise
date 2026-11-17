"""BMS map source: the mapset (``DFHMSD``) and its maps (``DFHMDI``) as symbols."""

from __future__ import annotations

import re

from ..models import FileInfo, ParsedFile, Symbol

# A labelled macro in column 1: ``COSGN00 DFHMSD TYPE=&&SYSPARM,...``.
_MACRO = re.compile(r"^(?P<label>[A-Z0-9#@$]{1,8})\s+(?P<macro>DFHMSD|DFHMDI)\b", re.I)
_FINAL = re.compile(r"\bDFHMSD\s+TYPE=FINAL\b", re.I)


def parse_bms_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    lines = source.decode("utf-8", errors="replace").splitlines()
    end = len(lines)
    for number, line in enumerate(lines, 1):
        if _FINAL.search(line):
            end = number
            break
    symbols: list[Symbol] = []
    for number, line in enumerate(lines[:end], 1):
        m = _MACRO.match(line)
        if m is None:
            continue
        name, macro = m.group("label").upper(), m.group("macro").upper()
        kind = "module" if macro == "DFHMSD" else "class"
        if kind == "class" and symbols and symbols[-1].kind == "class":
            symbols[-1].end_line = number - 1
        symbols.append(
            Symbol(
                id=f"{file_info.path}::{name}",
                name=name,
                qualified_name=name,
                kind=kind,
                signature=f"{name} {macro}",
                start_line=number,
                end_line=end,
                docstring=None,
                language="bms",
            )
        )
    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=[],
        exports=[s.name for s in symbols],
        docstring=None,
        parse_errors=[],
    )


def mapset_names(parsed: ParsedFile) -> list[str]:
    return [s.name for s in parsed.symbols if s.kind == "module"]
