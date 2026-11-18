"""Parse Go ``go.mod`` manifests.

Single-line ``require module v1.2.3`` and ``require ( ... )`` blocks are
both handled. ``// indirect`` comments mark transitive deps — we flag those
as dev-deps so the C4 view can de-clutter L1 by hiding them by default.
``replace`` directives are honored: any replacement that points at a local
path drops the module from the result set (it's a container, not external).
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import ExternalSystemRecord
from .classifier import classify, display_name_for
from .io_kind import classify_io_kind

filenames: tuple[str, ...] = ("go.mod",)
ecosystem: str = "go"

_REQ_LINE = re.compile(r"^\s*([^\s]+)\s+([^\s]+)(?:\s*//\s*(indirect))?\s*$")
_REPLACE_LINE = re.compile(r"^\s*([^\s]+)(?:\s+[^\s]+)?\s*=>\s*(\S+)")


def _single_line_form(
    line: str, requires: list[tuple[str, str, bool]], replaced_by_local: set[str]
) -> bool:
    """Record a one-line ``require x v`` or ``replace x => y``; False for any other line."""
    if "(" in line:
        return False
    if line.startswith("require "):
        m = _REQ_LINE.match(line[len("require ") :].strip())
        if m:
            requires.append((m.group(1), m.group(2), bool(m.group(3))))
        return True
    if line.startswith("replace "):
        _handle_replace(line[len("replace ") :].strip(), replaced_by_local)
        return True
    return False


def scan_go_mod(text: str) -> tuple[list[tuple[str, str, bool]], set[str]]:
    """``(requires, replaced_by_local)`` of a ``go.mod`` text.

    *requires* is ``(module, version, is_indirect)`` per ``require`` entry in
    file order; *replaced_by_local* names the modules a ``replace`` points at a
    local directory.
    """
    replaced_by_local: set[str] = set()
    requires: list[tuple[str, str, bool]] = []

    in_require = False
    in_replace = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if _single_line_form(line, requires, replaced_by_local):
            continue
        # Block forms
        if line.startswith("require ("):
            in_require = True
            continue
        if line.startswith("replace ("):
            in_replace = True
            continue
        if line == ")":
            in_require = in_replace = False
            continue
        if in_require:
            m = _REQ_LINE.match(line)
            if m:
                requires.append((m.group(1), m.group(2), bool(m.group(3))))
        elif in_replace:
            _handle_replace(line, replaced_by_local)
    return requires, replaced_by_local


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return []

    declared_in = manifest_path.relative_to(repo_root).as_posix()
    requires, replaced_by_local = scan_go_mod(text)

    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()
    for name, version, indirect in requires:
        if name in replaced_by_local or name in seen:
            continue
        seen.add(name)
        records.append(
            ExternalSystemRecord(
                name=name,
                ecosystem=ecosystem,
                declared_in=declared_in,
                version=version,
                display_name=display_name_for(name.split("/")[-1]),
                category=classify(name.split("/")[-1]),
                io_kind=classify_io_kind(name.split("/")[-1]),
                is_dev_dep=indirect,
            )
        )
    return records


def _handle_replace(entry: str, replaced_by_local: set[str]) -> None:
    m = _REPLACE_LINE.match(entry)
    if not m:
        return
    name, target = m.group(1), m.group(2)
    # A local path replacement looks like ``./foo`` or ``../bar`` or an
    # absolute filesystem path. Remote module replacements look like
    # ``github.com/x/y``.
    if target.startswith(".") or target.startswith("/") or (len(target) > 1 and target[1] == ":"):
        replaced_by_local.add(name)
