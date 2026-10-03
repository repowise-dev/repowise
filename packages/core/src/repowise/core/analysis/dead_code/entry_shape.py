"""Unreachable files shaped like something a runtime loads by path.

"Nothing imports this" is weak evidence for a file whose shape says it is
never imported in the first place:

* a TS/JS module whose only export is ``default`` is what a framework's file
  router or plugin loader picks up by path;
* a file that does work when run (top-level statements, a ``__main__`` guard,
  a shebang) is started by a command, not by an import;
* three or more unreachable files in one directory exporting the same names
  are a convention-loaded set (pages, handlers, migrations) whose loader the
  graph does not see;
* a file exporting everything such a set exports, plus more (a tool that
  also exports its input type), belongs to the same set.

Each caps the finding to the review tier, except a program: a file whose
first line is a shebang or that runs under a ``__main__`` guard is an entry
point, so :func:`drop_program_entries` removes it. The cap also stops
"no commits in 90 days" lifting such a file to the high tier, since an
untouched loaded file is not an unused one. Shape is not proof of use, so the
file stays listed as a candidate for a person to check.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePosixPath

from ...ingestion.extractors.visibility import _TS_BLOCK_COMMENT, _TS_CJS_EXPORTS_RE
from ...ingestion.languages.registry import REGISTRY
from .models import DeadCodeFindingData, DeadCodeKind
from .risk_factors import RISK_CAP_CONFIDENCE

#: Siblings sharing one export shape before the set reads as a convention.
MIN_COHORT_SIZE = 3

_JS_FAMILY = frozenset({"typescript", "javascript"})

# ``export default <name>`` / ``export default function|class <name>``: the
# named declaration is the default export, so it is not a second export.
_DEFAULT_EXPORT_RE = re.compile(
    r"^[ \t]*export[ \t]+default\b[ \t]*(?:async[ \t]+)?"
    r"(?:(?:abstract[ \t]+)?class|function\*?)?[ \t]*([A-Za-z_$][\w$]*)?",
    re.MULTILINE,
)
# CommonJS: a whole-module ``module.exports = x`` (not an object of names) is
# the default; any ``exports.name`` / ``module.exports.name`` is a named export.
_CJS_DEFAULT_RE = re.compile(r"^[ \t]*module\.exports[ \t]*=(?![=>])(?![ \t]*\{)", re.MULTILINE)
_CJS_NAMED_RE = re.compile(r"\b(?:module\.)?exports[ \t]*(?:\.[ \t]*[A-Za-z_$]|\[)")

_LOOPS_AND_BLOCKS = (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)


def export_shape(path: str, public_names: frozenset[str], blob: bytes | None) -> frozenset[str]:
    """The names *path* exports, with a TS/JS default export spelled ``default``."""
    if blob is None or REGISTRY.from_extension(PurePosixPath(path).suffix) not in _JS_FAMILY:
        return public_names
    text = _TS_BLOCK_COMMENT.sub("", blob.decode("utf-8", "ignore"))
    if _TS_CJS_EXPORTS_RE.search(text):
        # Ingestion keeps every top-level name public in a CommonJS file, so
        # only a lone whole-module assignment says anything about its shape.
        lone = _CJS_DEFAULT_RE.search(text) and not _CJS_NAMED_RE.search(text)
        return frozenset({"default"}) if lone else public_names
    defaults = list(_DEFAULT_EXPORT_RE.finditer(text))
    if not defaults:
        return public_names
    named = {m.group(1) for m in defaults if m.group(1)}
    return (public_names - named) | {"default"}


def _is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def _parse_python(path: str, blob: bytes) -> ast.Module | None:
    """*blob* parsed, when it is Python source that parses; else None."""
    if REGISTRY.from_extension(PurePosixPath(path).suffix) != "python" or path.endswith(".pyi"):
        return None
    try:
        return ast.parse(blob)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _program_reason(path: str, blob: bytes) -> str | None:
    """Why *blob* says it is a program: a shebang or a Python main guard."""
    # ``#![`` opens a Rust inner attribute (``#![doc = ...]``), not a shebang.
    if blob.startswith(b"#!") and not blob.startswith(b"#!["):
        return "Starts with a shebang, so it is run directly rather than imported"
    tree = _parse_python(path, blob) if b"__main__" in blob else None
    if tree is not None and any(
        isinstance(stmt, ast.If) and _is_main_guard(stmt.test) for stmt in tree.body
    ):
        return 'Has an `if __name__ == "__main__"` block, so it is run as a script'
    return None


def is_program(path: str, blob: bytes) -> bool:
    """Whether *blob* says it is a program: a shebang, or a Python main guard.

    Either is the author stating the file is started by a command. Statements
    at module level are weaker (an imported module can run code on load), so
    they only cap a finding, through :func:`_script_reason`.
    """
    return _program_reason(path, blob) is not None


def drop_program_entries(
    findings: list[DeadCodeFindingData], source_map: dict[str, bytes]
) -> list[DeadCodeFindingData]:
    """Drop unreachable files that are programs: nothing imports an entry point.

    "No importer" is the wrong claim for a file whose first line is a shebang
    or that runs under ``if __name__ == "__main__"``. Returns a new list.
    """
    return [
        f
        for f in findings
        if f.kind is not DeadCodeKind.UNREACHABLE_FILE
        or not is_program(f.file_path, source_map.get(f.file_path, b""))
    ]


def _script_reason(path: str, blob: bytes) -> str | None:
    """Why *blob* reads as something run rather than imported, or None."""
    if reason := _program_reason(path, blob):
        return reason
    tree = _parse_python(path, blob)
    return _module_statement_reason(tree) if tree is not None else None


def _module_statement_reason(tree: ast.Module) -> str | None:
    for stmt in tree.body:
        # A bare constant is a docstring or ``...``; anything else executes on load.
        if isinstance(stmt, _LOOPS_AND_BLOCKS) or (
            isinstance(stmt, ast.Expr) and not isinstance(stmt.value, ast.Constant)
        ):
            return f"Runs statements at module level (line {stmt.lineno}), so it is run as a script"
    return None


def _cohorts(shapes: Mapping[str, frozenset[str]]) -> dict[str, int]:
    """For each file in a same-directory, same-shape set of MIN_COHORT_SIZE+, the set size."""
    groups: dict[tuple[str, frozenset[str]], list[str]] = defaultdict(list)
    for path, shape in shapes.items():
        if shape:  # no exports is no shape to share
            groups[(str(PurePosixPath(path).parent), shape)].append(path)
    return {p: len(ps) for ps in groups.values() if len(ps) >= MIN_COHORT_SIZE for p in ps}


def _cohort_shapes(
    shapes: Mapping[str, frozenset[str]], cohort_size: Mapping[str, int]
) -> dict[str, set[frozenset[str]]]:
    """Per directory, the export shapes of its cohorts.

    A cohort, not one default-only file: a lone route stub beside an ordinary
    component says nothing about how the component is loaded.
    """
    out: dict[str, set[frozenset[str]]] = defaultdict(set)
    for path in cohort_size:
        out[str(PurePosixPath(path).parent)].add(shapes[path])
    return out


def clamp_entry_shaped(
    findings: list[DeadCodeFindingData],
    source_map: dict[str, bytes],
    public_names: Mapping[str, frozenset[str]],
) -> list[DeadCodeFindingData]:
    """Cap unreachable files whose shape says a runtime loads them by path.

    *public_names* maps each unreachable file to its top-level public symbol
    names. The cohort counts every unreachable sibling, capped or not, since
    a sibling already in review is still part of the set. Mutates in place
    and returns the same list; never raises a confidence or drops a finding.
    """
    unreachable = [f for f in findings if f.kind is DeadCodeKind.UNREACHABLE_FILE]
    if not unreachable:
        return findings
    shapes = {
        f.file_path: export_shape(
            f.file_path, public_names.get(f.file_path, frozenset()), source_map.get(f.file_path)
        )
        for f in unreachable
    }
    cohort_size = _cohorts(shapes)
    cohort_shapes = _cohort_shapes(shapes, cohort_size)

    for finding in unreachable:
        if finding.confidence <= RISK_CAP_CONFIDENCE:
            continue
        path = finding.file_path
        reason = _entry_reason(
            path,
            shapes[path],
            source_map.get(path),
            cohort_size.get(path),
            cohort_shapes.get(str(PurePosixPath(path).parent), set()),
        )
        if reason is None:
            continue
        finding.confidence = min(finding.confidence, RISK_CAP_CONFIDENCE)
        finding.safe_to_delete = False
        finding.evidence.append(reason)
    return findings


def _entry_reason(
    path: str,
    shape: frozenset[str],
    blob: bytes | None,
    cohort_size: int | None,
    cohort_shapes: set[frozenset[str]],
) -> str | None:
    """The first entry shape *path* has, as an evidence line, or None."""
    if shape == {"default"}:
        return "Its only export is `default`, the shape a framework loads by path"
    if blob is not None and (script := _script_reason(path, blob)):
        return script
    if cohort_size:
        names = ", ".join(sorted(shape)[:3])
        return (
            f"One of {cohort_size} unreachable files in its directory exporting "
            f"the same names ({names}), the shape of a convention-loaded set"
        )
    if fits := [s for s in cohort_shapes if s < shape]:
        # min() keeps the evidence line stable when two cohort shapes fit.
        names = ", ".join(sorted(min(fits, key=sorted))[:3])
        return (
            f"Exports what a convention-loaded set in its directory exports ({names}) "
            "and more, so it belongs to that set; its age is not evidence it is unused"
        )
    return None
