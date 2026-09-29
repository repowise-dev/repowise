"""Which directories a repo's root workspace manifests declare as members.

A package manager's own member list is the authority on what the repo's
packages are. A manifest found on disk is only a candidate: fixture
``package.json`` files, scaffolding templates and docs sites all carry one and
none of them is a package the workspace builds.

Keyed by the manifest filename a member carries, so the caller can tell which
ecosystem declared a workspace:

- ``package.json``: ``pnpm-workspace.yaml``, else ``package.json``'s
  ``workspaces`` (npm, yarn); read by the TypeScript resolver's reader.
- ``Cargo.toml``: ``[workspace] members`` minus ``exclude``.
- ``pyproject.toml``: ``[tool.uv.workspace] members`` minus ``exclude``.
- ``go.mod``: ``go.work``'s ``use`` directives.

Only the repo root's manifests are read. Ceiling: a workspace declared in a
subdirectory (a JS workspace nested in a Python repo) is not seen, so its
members stay undeclared; reading nested declarations is the upgrade path.
"""

from __future__ import annotations

import json
import posixpath
import re
import tomllib
from pathlib import Path

from .resolvers.go import _read_module_directive
from .resolvers.ts_workspace import (
    _expand_member_dirs,
    _read_workspace_declaration,
    _WorkspaceDeclaration,
)


def _globbed(repo_root: Path, includes: list, excludes: list) -> set[str]:
    """Member globs expanded with the JS workspace globber, as repo-relative dirs."""
    declared = _WorkspaceDeclaration(
        tuple(p for p in includes if isinstance(p, str)),
        tuple(p for p in excludes if isinstance(p, str)),
        include_root=False,
    )
    out: set[str] = set()
    for d in _expand_member_dirs(repo_root, declared):
        try:
            rel = d.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if rel != ".":
            out.add(rel)
    return out


def _toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _toml_workspace(repo_root: Path, manifest: str, *table: str) -> set[str] | None:
    """``members``/``exclude`` of a TOML workspace table (Cargo and uv share the shape).

    None without a ``members`` list: Cargo then makes the root's path
    dependencies members implicitly, which this does not read.
    """
    data: object = _toml(repo_root / manifest)
    for key in table:
        data = data.get(key) if isinstance(data, dict) else None
    if not isinstance(data, dict):
        return None
    members, exclude = data.get("members"), data.get("exclude")
    if not isinstance(members, list):
        return None
    return _globbed(repo_root, members, exclude if isinstance(exclude, list) else [])


_GO_WORK_USE = re.compile(r"^\s*use\s*(?:\(([^)]*)\)|(\S+))", re.MULTILINE)


def _go_work_members(repo_root: Path) -> set[str] | None:
    """Directories ``go.work`` pulls in with ``use`` (single or block form)."""
    try:
        text = (repo_root / "go.work").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    text = re.sub(r"//[^\n]*", "", text)
    out: set[str] = set()
    for block, single in _GO_WORK_USE.findall(text):
        for entry in block.split() if block else [single]:
            rel = posixpath.normpath(entry.strip('"`'))
            if rel != "." and not rel.startswith("..") and (repo_root / rel).is_dir():
                out.add(rel)
    return out


def declared_workspace_members(repo_root: Path) -> dict[str, set[str]]:
    """``{manifest filename: member dirs}`` for every root declaration.

    A declaration whose members expand to nothing (a root-only pnpm workspace,
    ``members = []``) is kept with an empty set: it still says no other
    manifest of its kind is a member.
    """
    js = _read_workspace_declaration(repo_root)
    found = {
        "package.json": (
            None if js.is_empty else _globbed(repo_root, list(js.includes), list(js.excludes))
        ),
        "Cargo.toml": _toml_workspace(repo_root, "Cargo.toml", "workspace"),
        "pyproject.toml": _toml_workspace(repo_root, "pyproject.toml", "tool", "uv", "workspace"),
        "go.mod": _go_work_members(repo_root),
    }
    return {name: dirs for name, dirs in found.items() if dirs is not None}


def manifest_package_name(manifest: Path) -> str | None:
    """The package name *manifest* declares, or None when it names none."""
    name: object = None
    if manifest.name == "package.json":
        try:
            data = json.loads(manifest.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            return None
        name = data.get("name") if isinstance(data, dict) else None
    elif manifest.name in ("pyproject.toml", "Cargo.toml"):
        data = _toml(manifest)
        section = data.get("project") or data.get("package")
        if not isinstance(section, dict) or not section.get("name"):
            tool = data.get("tool")
            section = tool.get("poetry") if isinstance(tool, dict) else None
        name = section.get("name") if isinstance(section, dict) else None
    elif manifest.name == "go.mod":
        name = _read_module_directive(manifest)
    return name.strip() if isinstance(name, str) and name.strip() else None
