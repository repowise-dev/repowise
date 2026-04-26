"""SBT / Mill multi-project index for Scala import resolution.

SBT's ``build.sbt`` declares subprojects as
``lazy val core = project.in(file("core"))``. Mill's ``build.sc`` uses
``object Foo extends ScalaModule { ... }`` (or ``CrossScalaModule``,
``SbtModule``, etc.). This module parses both with conservative regex,
discovers their source roots (defaults: ``src/main/scala`` for SBT,
``<modulename>/src`` for Mill), and walks each root for ``package``
declarations to build a ``{fully.qualified.Name → [path, ...]}`` map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext


_SBT_PROJECT_RE = re.compile(
    r'lazy\s+val\s+(\w+)\s*=\s*(?:\([^)]*\)\s*=>\s*)?'
    r'(?:project|Project)\b[^\n]*?\.in\(\s*file\(\s*"([^"]+)"\s*\)\s*\)',
    re.DOTALL,
)
_MILL_OBJECT_RE = re.compile(
    r'object\s+(\w+)\s+extends\s+([\w.]+(?:\s*with\s+[\w.]+)*)',
)
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)


@dataclass
class ScalaProjectIndex:
    build_tool: str = "none"  # "sbt" | "mill" | "none"
    projects: dict[str, str] = field(default_factory=dict)  # name -> dir_posix
    package_to_files: dict[str, list[str]] = field(default_factory=dict)

    def lookup_class(self, fqn: str) -> list[str]:
        if "." not in fqn:
            return self.package_to_files.get(fqn, [])
        package, local = fqn.rsplit(".", 1)
        candidates = self.package_to_files.get(package, [])
        local_lower = local.lower()
        return [
            p for p in candidates
            if p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() == local_lower
        ]


def _parse_build_sbt(repo_path: Path) -> dict[str, str]:
    build = repo_path / "build.sbt"
    if not build.is_file():
        return {}
    try:
        text = build.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    result: dict[str, str] = {}
    for match in _SBT_PROJECT_RE.finditer(text):
        result[match.group(1)] = match.group(2).strip("./")
    return result


def _parse_build_sc(repo_path: Path) -> dict[str, str]:
    build = repo_path / "build.sc"
    if not build.is_file():
        return {}
    try:
        text = build.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    result: dict[str, str] = {}
    for match in _MILL_OBJECT_RE.finditer(text):
        name = match.group(1)
        parents = match.group(2)
        if "Module" not in parents:
            continue
        # Mill convention: directory is the module name relative to repo root.
        result[name] = name
    return result


def _scan_packages(repo_path: Path, project_dir: str, build_tool: str) -> dict[str, list[str]]:
    """Walk a project's source tree, recording package declarations."""
    found: dict[str, list[str]] = {}
    proj_path = (repo_path / project_dir).resolve() if project_dir else repo_path.resolve()
    if not proj_path.is_dir():
        return found

    # Likely source root layouts per build tool.
    candidate_roots: list[Path] = []
    if build_tool == "sbt":
        candidate_roots.extend(
            proj_path / sub for sub in ("src/main/scala", "src/main/java")
        )
    elif build_tool == "mill":
        # Mill: files live directly under <module>/src or <module>/src/main/scala.
        candidate_roots.append(proj_path / "src")
        candidate_roots.append(proj_path / "src" / "main" / "scala")
    # Always try the project root itself as a last-resort source root.
    candidate_roots.append(proj_path)

    seen: set[Path] = set()
    for root in candidate_roots:
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        for src in root.rglob("*.scala"):
            try:
                rel = src.relative_to(repo_path.resolve()).as_posix()
            except ValueError:
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            pkg_match = _PACKAGE_RE.search(text)
            if not pkg_match:
                continue
            found.setdefault(pkg_match.group(1), []).append(rel)
    return found


def build_scala_index(repo_path: Path | None) -> ScalaProjectIndex:
    if repo_path is None or not repo_path.is_dir():
        return ScalaProjectIndex()
    sbt_projects = _parse_build_sbt(repo_path)
    mill_projects = _parse_build_sc(repo_path)
    if sbt_projects:
        index = ScalaProjectIndex(build_tool="sbt", projects=sbt_projects)
    elif mill_projects:
        index = ScalaProjectIndex(build_tool="mill", projects=mill_projects)
    else:
        # No multi-project config: still scan repo root for package decls so
        # standalone ``src/main/scala`` layouts are indexed.
        if (repo_path / "build.sbt").is_file():
            index = ScalaProjectIndex(build_tool="sbt", projects={"<root>": ""})
        elif (repo_path / "build.sc").is_file():
            index = ScalaProjectIndex(build_tool="mill", projects={"<root>": ""})
        else:
            return ScalaProjectIndex()

    for _name, project_dir in index.projects.items():
        for pkg, files in _scan_packages(repo_path, project_dir, index.build_tool).items():
            index.package_to_files.setdefault(pkg, []).extend(files)
    return index


def get_or_build_scala_index(ctx: "ResolverContext") -> ScalaProjectIndex:
    cached = getattr(ctx, "_scala_index", None)
    if cached is not None:
        return cached
    index = build_scala_index(ctx.repo_path)
    ctx._scala_index = index  # type: ignore[attr-defined]
    return index


def resolve_via_scala_index(module_path: str, ctx: "ResolverContext") -> str | None:
    index = get_or_build_scala_index(ctx)
    matches = index.lookup_class(module_path)
    if matches:
        return matches[0]
    return None
