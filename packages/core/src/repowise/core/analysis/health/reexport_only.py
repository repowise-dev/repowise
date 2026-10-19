"""Whether a file holds nothing but a docstring, imports and export lists."""

from __future__ import annotations

import re
from typing import Any

from .source_reader import SourceReader

_EXPORT_LIST = "__all__"
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
#: Bare docstring quotes left once its text is cut, or the directive no extractor reports.
_INERT_LINE = re.compile(r"""^([rRbBuU]?(\"\"\"|'''|"|')+|from __future__ import [\w ,()]+)$""")


def _is_inert(line: str) -> bool:
    text = line.strip().strip(";").strip()
    return not text or text.startswith(("#", "//")) or bool(_INERT_LINE.match(text))


def is_reexport_only(pf: Any, has_functions: bool, read_source: SourceReader) -> bool:
    """True when every code line of the file is the docstring, an import or ``__all__``.

    Each known statement is cut from the text; anything left that is not blank, a
    comment or bare docstring quotes is code (a call, a second statement after ``;``).
    """
    if has_functions or any(s.name != _EXPORT_LIST for s in pf.symbols):
        return False
    source = read_source(pf.file_info.abs_path)
    if source is None:
        return False
    lines = source.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    for sym in pf.symbols:
        lines[sym.start_line - 1 : sym.end_line] = [""] * (sym.end_line - sym.start_line + 1)
    text = "\n".join(lines)
    if pf.file_info.language != "python":
        text = _BLOCK_COMMENT.sub("", text)
    for cut in [imp.raw_statement for imp in pf.imports] + [pf.docstring or ""]:
        if cut:
            text = text.replace(cut.replace("\r\n", "\n"), "", 1)
    return all(_is_inert(line) for line in text.split("\n"))
