"""A Python module named by a module-path string is used.

Python loads code by dotted name as often as by ``import``: an entry-point
table (``"repowise.cli.main:cli"`` in ``pyproject.toml``), a Celery or Django
setting, a lazy command table that imports ``"uninstall_cmd:uninstall_command"``
only when the command runs, a package ``__getattr__`` mapping names to
``"result_panels"``. None of these leaves an import edge, so the module reads
as unreachable and the attribute after the colon as an unused export.

Two spellings count:

* a dotted name of two or more segments that is the tail of the module's own
  dotted path (``cli.commands.uninstall_cmd``), written in any indexed file;
* a bare module name, only inside a file that imports by name at runtime
  (an ``import_module`` caller), and only for a module in that file's
  directory or below it, which is where such a loader looks.

``module:attr`` additionally uses ``attr`` of that module. A Django or plugin
table names a member with a dot instead (``"hc.accounts.backends.EmailBackend"``
in ``AUTHENTICATION_BACKENDS``): when the whole string names no module, its
parent does, and the last segment is a top-level name that module defines, the
string uses both. Each match drops the finding: a string that resolves to the
module is how the module is loaded.

Only strings that run count: a comment or a Python docstring that mentions a
dotted name loads nothing. A test file counts only whole module paths (a
fixture app it loads by name), never a member path (a ``mock.patch`` target).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

from ...ingestion.languages.python_modules import module_parts
from ...ingestion.languages.python_strings import defines_top_level, is_python, live_text
from ...test_paths import is_test_related_path
from .models import DeadCodeFindingData, DeadCodeKind

#: A quoted module path, optionally followed by ``:attr``.
_MODULE_STRING_RE = re.compile(
    rb"""['"]([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?::([A-Za-z_]\w*))?['"]"""
)


class _ModuleIndex:
    """The candidate modules, keyed by every dotted tail and by bare name."""

    def __init__(self, paths: Iterable[str]) -> None:
        self.by_tail: dict[str, set[str]] = {}
        self.by_name: dict[str, set[str]] = {}
        for path in paths:
            parts = module_parts(path)
            if not parts:
                continue
            for start in range(len(parts) - 1):
                self.by_tail.setdefault(".".join(parts[start:]), set()).add(path)
            self.by_name.setdefault(parts[-1], set()).add(path)

    def resolve(self, module: str, loader_dir: str | None) -> set[str]:
        """The candidate files *module* names, as written in a file in *loader_dir*."""
        found = set(self.by_tail.get(module, ()))
        if loader_dir is not None and "." not in module:
            prefix = f"{loader_dir}/" if loader_dir else ""
            found.update(p for p in self.by_name.get(module, ()) if p.startswith(prefix))
        return found


def _directory(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def drop_named_modules(
    findings: list[DeadCodeFindingData],
    source_map: dict[str, bytes],
    runtime_importers: Iterable[str],
) -> list[DeadCodeFindingData]:
    """Drop Python file and export findings a module-path string names.

    *runtime_importers* are the files that import by name at runtime, the
    only place a bare module name is read as one. Returns a new list.
    """
    candidates = {f.file_path for f in findings if _is_candidate(f)}
    if not candidates or not source_map:
        return findings
    loaders = {path: _directory(path) for path in runtime_importers}
    named, attrs = _named_by_strings(source_map, _ModuleIndex(candidates), loaders)
    return [f for f in findings if not _is_named(f, named, attrs)]


def _is_candidate(finding: DeadCodeFindingData) -> bool:
    kinds = (DeadCodeKind.UNREACHABLE_FILE, DeadCodeKind.UNUSED_EXPORT)
    return finding.kind in kinds and is_python(finding.file_path)


def _is_named(
    finding: DeadCodeFindingData, named: set[str], attrs: set[tuple[str, str]]
) -> bool:
    if finding.kind is DeadCodeKind.UNREACHABLE_FILE:
        return finding.file_path in named
    return (
        finding.kind is DeadCodeKind.UNUSED_EXPORT
        and (finding.file_path, finding.symbol_name) in attrs
    )


def _named_by_strings(
    source_map: dict[str, bytes], index: _ModuleIndex, loaders: dict[str, str]
) -> tuple[set[str], set[tuple[str, str]]]:
    """Modules a string names, and the ``(module, attr)`` pairs ``module:attr`` names."""
    named: set[str] = set()
    attrs: set[tuple[str, str]] = set()
    for path, blob in source_map.items():
        # A test that loads a fixture module by name (``"t.unit.proj.app"``)
        # uses it; a member path in a test is a ``mock.patch`` target, not a load.
        members = None if is_test_related_path(path) else source_map
        reader = _Reader(path, index, loaders.get(path), members)
        # Tokenize only a file whose raw text names a candidate at all.
        if next(reader.uses(blob), None) is None:
            continue
        for target, attr in reader.uses(live_text(path, blob)):
            named.add(target)
            if attr:
                attrs.add((target, attr))
    return named, attrs


@dataclass(frozen=True)
class _Reader:
    """Resolves the module-path strings written in one file.

    *members* is the source to look a ``"pkg.module.Name"`` member up in, or
    None to read only whole module paths.
    """

    path: str
    index: _ModuleIndex
    loader_dir: str | None
    members: dict[str, bytes] | None

    def uses(self, text: bytes) -> Iterator[tuple[str, str | None]]:
        """``(module, attr)`` for each module-path string in *text*."""
        for match in _MODULE_STRING_RE.finditer(text):
            module = match.group(1).decode("ascii")
            attr = match.group(2).decode("ascii") if match.group(2) else None
            targets = self._resolve(module)
            if not (targets or attr) and self.members is not None:
                targets, attr = self._member_owners(module, self.members)
            yield from ((target, attr) for target in targets)

    def _resolve(self, module: str) -> set[str]:
        return self.index.resolve(module, self.loader_dir) - {self.path}

    def _member_owners(self, module: str, members: dict[str, bytes]) -> tuple[set[str], str]:
        """For ``"pkg.module.Name"``: the modules ``pkg.module`` names that define ``Name``."""
        parent, _, name = module.rpartition(".")
        owners = self._resolve(parent) if parent else set()
        return {t for t in owners if defines_top_level(members.get(t, b""), name)}, name
