"""C/C++ registration-macro symbol synthesis.

A swathe of C++ idioms wire functions and types into a runtime registry
at static-initialisation time. The compiler emits no call edge, so the
referenced symbols look orphaned to a static analyzer that only sees
the tree-sitter AST:

  - ``PYBIND11_MODULE(name, m) { m.def("foo", &foo); }`` — Python
    binding entry point.
  - ``BOOST_PYTHON_MODULE(name) { ... }`` — Boost.Python equivalent.
  - ``DEFINE_string(NAME, DEFAULT, HELP)`` / ``ABSL_FLAG(T, NAME, ...)``
    — gflags / Abseil flag definitions which materialise a
    ``FLAGS_NAME`` global at static-init time.

This module emits the *symbols* those macros materialise so the rest of
the graph treats them like the parser had seen them in source. File-
level reachability (e.g. marking the whole TU as an entry point because
``PYBIND11_MODULE`` is present) lives in :mod:`graph_warmups` — it can
mutate graph node attributes; the synthesis pass can only add Symbol
records the parser appends to its symbol list.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node


# Regex catalog — every match emits one synthetic symbol.
#
# Each entry: (pattern, kind, name_group, parent, signature_template).
# We use plain regex instead of walking the AST: most of these macros
# look like function calls to tree-sitter (``call_expression`` whose
# function is an ``identifier``), and traversing the AST adds nothing
# over a precompiled regex on the source text.

_PYBIND11_MODULE_RE = re.compile(
    r"\bPYBIND11(?:_EMBEDDED)?_MODULE\s*\(\s*([A-Za-z_]\w*)\s*,",
)
_BOOST_PY_MODULE_RE = re.compile(
    r"\bBOOST_PYTHON_MODULE(?:_INIT)?\s*\(\s*([A-Za-z_]\w*)\s*\)",
)
_NAPI_MODULE_RE = re.compile(
    r"\bNAPI_MODULE\s*\(\s*([A-Za-z_]\w*)\s*,",
)

# gflags: DEFINE_bool / DEFINE_string / DEFINE_int32 / DEFINE_double / …
_GFLAGS_DEFINE_RE = re.compile(
    r"\bDEFINE_(?:bool|string|int32|int64|uint32|uint64|double|float)\s*"
    r"\(\s*([A-Za-z_]\w*)\s*,",
)
# Abseil: ABSL_FLAG(type, name, default, help)
_ABSL_FLAG_RE = re.compile(
    r"\bABSL_FLAG\s*\(\s*[^,]+,\s*([A-Za-z_]\w*)\s*,",
)


# Tokens that, if absent from the source, mean none of the patterns
# above can match — cheap reject path before running every regex.
_FAST_REJECT_TOKENS = (
    "PYBIND11_MODULE",
    "BOOST_PYTHON_MODULE",
    "NAPI_MODULE",
    "DEFINE_",
    "ABSL_FLAG",
)


def _line_of(src: str, offset: int) -> int:
    """Return 1-based line number for a byte offset within ``src``."""
    return src.count("\n", 0, offset) + 1


def cpp_macro_synthetic_symbols(
    root: "Node", src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit synthetic symbols for static-init registration macros."""
    # Cheap reject — vast majority of C/C++ TUs don't use any of these.
    if not any(tok in src for tok in _FAST_REJECT_TOKENS):
        return []

    out: list[Symbol] = []

    for m in _PYBIND11_MODULE_RE.finditer(src):
        name = m.group(1)
        line = _line_of(src, m.start())
        out.append(
            build_synthetic_symbol(
                name=name,
                kind="module",
                signature=f"PYBIND11_MODULE({name}, ...)",
                start_line=line,
                end_line=line,
                file_info=file_info,
                parent_name=None,
            )
        )

    for m in _BOOST_PY_MODULE_RE.finditer(src):
        name = m.group(1)
        line = _line_of(src, m.start())
        out.append(
            build_synthetic_symbol(
                name=name,
                kind="module",
                signature=f"BOOST_PYTHON_MODULE({name})",
                start_line=line,
                end_line=line,
                file_info=file_info,
                parent_name=None,
            )
        )

    for m in _NAPI_MODULE_RE.finditer(src):
        name = m.group(1)
        line = _line_of(src, m.start())
        out.append(
            build_synthetic_symbol(
                name=name,
                kind="module",
                signature=f"NAPI_MODULE({name}, ...)",
                start_line=line,
                end_line=line,
                file_info=file_info,
                parent_name=None,
            )
        )

    for m in _GFLAGS_DEFINE_RE.finditer(src):
        flag_name = m.group(1)
        line = _line_of(src, m.start())
        out.append(
            build_synthetic_symbol(
                name=f"FLAGS_{flag_name}",
                kind="variable",
                signature=f"DEFINE_*({flag_name}, …)",
                start_line=line,
                end_line=line,
                file_info=file_info,
                parent_name=None,
            )
        )

    for m in _ABSL_FLAG_RE.finditer(src):
        flag_name = m.group(1)
        line = _line_of(src, m.start())
        out.append(
            build_synthetic_symbol(
                name=f"FLAGS_{flag_name}",
                kind="variable",
                signature=f"ABSL_FLAG(…, {flag_name}, …)",
                start_line=line,
                end_line=line,
                file_info=file_info,
                parent_name=None,
            )
        )

    return out
