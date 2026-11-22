"""Go uses of a name that the file's own imports pin to another package.

In Go, ``pkg.Name`` where ``pkg`` is a name the file imports can only be
package ``pkg``'s member. So in a file that imports several packages
exporting the same name (``authn.NewServer``, ``oidc.NewServer``), only the
uses qualified by the target's own package are its sites, and a method is
never named through an import at all (``mg.Deps(...)`` is not ``UI.Deps``).

Qualifiers are read from the file's import specs: an explicit alias, else the
package name the path implies (its last element, past a ``/vN`` or ``.vN``
major version and a ``go-`` prefix or ``-go`` suffix). The target's package is
matched by its import path, from the nearest ``go.mod`` above it. A guess
only ever leaves a site in, never takes one out wrongly: a qualifier the
file binds itself (a local variable or parameter) is not judged for a method.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

# One import spec: ``[alias] "path"``, on an ``import`` line or inside a block.
_SPEC = re.compile(r'^\s*(?:import\s*\(?\s*)?(?:([A-Za-z_]\w*|[._])\s+)?"([^"\s]+)"')
_PACKAGE = re.compile(r"^\s*package\s+([A-Za-z_]\w*)")
_MODULE = re.compile(r"^\s*module\s+(\S+)")
# The name right before ``.Name``, not itself after a dot or a call: ``pkg.`` in
# ``pkg.Name``, never ``x.pkg.`` or ``f().``.
_QUALIFIER = re.compile(r"(?<![\w.)\]])([A-Za-z_]\w*)\s*\.\s*$")
_MAJOR = re.compile(r"v\d+")


class GoTarget(NamedTuple):
    """The package a Go symbol lives in, as importers spell it."""

    import_path: str | None
    directory: str
    package: str | None


def go_target(root: Path, def_file: str, def_lines: list[str]) -> GoTarget:
    """The import path, directory and declared package of *def_file*'s package."""
    directory = PurePosixPath(def_file).parent.as_posix()
    directory = "" if directory == "." else directory
    package = next((m.group(1) for t in def_lines if (m := _PACKAGE.match(t))), None)
    return GoTarget(_import_path(root, directory), directory, package)


def _import_path(root: Path, directory: str) -> str | None:
    """``module`` of the nearest ``go.mod`` at or above *directory*, plus the rest."""
    parts = [p for p in directory.split("/") if p]
    for depth in range(len(parts), -1, -1):
        go_mod = root.joinpath(*parts[:depth], "go.mod")
        try:
            text = go_mod.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        module = next((m.group(1) for t in text.splitlines() if (m := _MODULE.match(t))), None)
        if module is None:
            return None
        return "/".join([module, *parts[depth:]])
    return None


def _implied_name(path: str) -> str | None:
    elements = path.split("/")
    name = elements[-1]
    if _MAJOR.fullmatch(name) and len(elements) > 1:
        name = elements[-2]
    name = re.sub(r"\.v\d+$", "", name)
    name = re.sub(r"^go-|-go$", "", name)
    return name if name.isidentifier() else None


class GoForeign:
    """Whether one use in a Go file is another package's member, by its qualifier."""

    def __init__(
        self, lines: list[str], imports: set[int], target: GoTarget, *, member: bool
    ) -> None:
        self._target = target
        self._member = member
        guessed: dict[str, str] = {}
        named: dict[str, str] = {}
        for n in sorted(imports):
            m = _SPEC.match(lines[n - 1])
            if m is None or m.group(1) in ("_", "."):
                continue
            alias, path = m.group(1), m.group(2)
            if alias:
                named[alias] = path
            elif self._is_target(path) and target.package:
                named[target.package] = path
            elif implied := _implied_name(path):
                guessed[implied] = path
        # A spelled alias or the target's own name beats a guess.
        self._paths = {**guessed, **named}
        self._lines = lines
        self._shadowed: dict[str, bool] = {}

    def _is_target(self, path: str) -> bool:
        directory = self._target.directory
        return path == self._target.import_path or bool(
            directory and (path == directory or path.endswith("/" + directory))
        )

    def _binds(self, alias: str) -> bool:
        """Whether the file declares *alias* itself: a variable, parameter or receiver."""
        if alias not in self._shadowed:
            word = re.escape(alias)
            binding = re.compile(
                rf"(?:\b{word}\s*(?:,\s*\w+\s*)*:?=(?!=)|,\s*{word}\s*(?:,\s*\w+\s*)*:=(?!=)"
                rf"|\bvar\s+{word}\b|[(,]\s*{word}\s+[\w*\[\].])"
            )
            self._shadowed[alias] = any(binding.search(t) for t in self._lines)
        return self._shadowed[alias]

    def __call__(self, text: str, m: re.Match[str]) -> bool:
        """True when the use at *m* names another package's member."""
        q = _QUALIFIER.search(text, 0, m.start())
        if q is None:
            return False
        path = self._paths.get(q.group(1))
        if path is None:
            return False
        if self._member:
            # A method is never named through a package; unless the name is a
            # local value's (``cfg := ...; cfg.Load()``), the use is not it.
            return not self._binds(q.group(1))
        return not self._is_target(path)


def go_judge(
    rel: str, lines: list[str], imports: set[int], target: Any, *, member: bool
) -> GoForeign | None:
    """The judge for one scanned file, when it is Go and the target's package is known."""
    if not isinstance(target, GoTarget) or PurePosixPath(rel).suffix != ".go":
        return None
    return GoForeign(lines, imports, target, member=member)
