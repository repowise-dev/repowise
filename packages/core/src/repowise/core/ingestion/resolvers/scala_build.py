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

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.fs_walk import iter_glob

if TYPE_CHECKING:
    from .context import ResolverContext


_SBT_PROJECT_RE = re.compile(
    r'lazy\s+val\s+(\w+)\s*=\s*(?:\([^)]*\)\s*=>\s*)?'
    r'(?:project|Project)\b[^\n]*?\.in\(\s*file\(\s*"([^"]+)"\s*\)\s*\)',
    re.DOTALL,
)
# Match the start of a .dependsOn( call; the argument list is read balanced.
_DEPENDS_ON_RE = re.compile(r'\.dependsOn\s*\(')
# Strings first so a "//" inside one is not taken for a comment.
_STRING_OR_COMMENT_RE = re.compile(
    r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/'
)
# A top-level val declaration ends the previous project's text.
_VAL_DECL_RE = re.compile(r'^(?:lazy\s+)?val\s', re.MULTILINE)
_SEQ_ARG_RE = re.compile(r'(?:Seq|List|Vector)\s*\((.*)\)\s*$', re.DOTALL)
_MILL_OBJECT_RE = re.compile(
    r'object\s+(\w+)\s+extends\s+([\w.]+(?:\s*with\s+[\w.]+)*)',
)
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)


@dataclass
class ScalaProjectIndex:
    build_tool: str = "none"  # "sbt" | "mill" | "none"
    projects: dict[str, str] = field(default_factory=dict)  # name -> dir_posix
    package_to_files: dict[str, list[str]] = field(default_factory=dict)
    depends_on: dict[str, tuple[str, ...]] = field(default_factory=dict)  # name -> dependency project names

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


def _strip_comments(text: str) -> str:
    """*text* without ``//`` and ``/* */`` comments (nested block comments are not tracked)."""
    return _STRING_OR_COMMENT_RE.sub(
        lambda m: m.group(0) if m.group(0).startswith('"') else " ", text
    )


def _balanced_args(text: str, start: int) -> str:
    """The text after the ``(`` before *start* up to its matching ``)``; parens inside strings are not skipped."""
    depth = 1
    for i in range(start, len(text)):
        depth += (text[i] == "(") - (text[i] == ")")
        if depth == 0:
            return text[start:i]
    return text[start:]


def _split_top_level(args_text: str) -> list[str]:
    """Split *args_text* on commas outside parentheses."""
    pieces: list[str] = []
    depth = 0
    last = 0
    for i, ch in enumerate(args_text):
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            pieces.append(args_text[last:i])
            last = i + 1
    pieces.append(args_text[last:])
    return pieces


def _parse_depends_on_args(args_text: str) -> list[str]:
    """Parse dependsOn arguments, extracting project names by leading identifier.

    ``Seq(a, b): _*`` is unwrapped; ``a % "test->test"`` yields ``a``.
    """
    result: list[str] = []
    for piece in _split_top_level(args_text):
        piece = re.sub(r":\s*_\*\s*$", "", piece.strip())
        if seq := _SEQ_ARG_RE.match(piece):
            result.extend(_parse_depends_on_args(seq.group(1)))
        elif match := re.match(r"\w+", piece):
            result.append(match.group(0))
    return result


def _project_deps(project_text: str) -> tuple[str, ...]:
    """The project names every ``dependsOn`` call in *project_text* names, deduped in order."""
    deps: list[str] = []
    for call in _DEPENDS_ON_RE.finditer(project_text):
        deps.extend(_parse_depends_on_args(_balanced_args(project_text, call.end())))
    return tuple(dict.fromkeys(deps))


def _parse_build_sbt(repo_path: Path) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Parse build.sbt, returning (projects dict, depends_on dict).

    The depends_on dict maps project names to tuples of dependency project names.
    """
    build = repo_path / "build.sbt"
    if not build.is_file():
        return {}, {}
    try:
        text = _strip_comments(build.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}, {}
    projects: dict[str, str] = {}
    depends_on: dict[str, tuple[str, ...]] = {}
    decl_starts = [m.start() for m in _VAL_DECL_RE.finditer(text)]
    for match in _SBT_PROJECT_RE.finditer(text):
        name = match.group(1)
        project_dir = posixpath.normpath(match.group(2).replace("\\", "/"))
        if project_dir == ".." or project_dir.startswith(("../", "/")):
            continue  # a project outside the repo owns nothing in it
        projects[name] = "" if project_dir == "." else project_dir
        # The project's text ends at the next val declaration, matched or not.
        end = next((pos for pos in decl_starts if pos > match.start()), len(text))
        depends_on[name] = _project_deps(text[match.end() : end])
    return projects, depends_on


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


def _scan_packages(
    repo_path: Path, project_dir: str, build_tool: str, *, prune_nested_git: bool = True
) -> dict[str, list[str]]:
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
        for src in iter_glob(root, "*.scala", prune_nested_git=prune_nested_git):
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


def build_scala_index(
    repo_path: Path | None, *, prune_nested_git: bool = True
) -> ScalaProjectIndex:
    if repo_path is None or not repo_path.is_dir():
        return ScalaProjectIndex()
    sbt_projects, sbt_depends_on = _parse_build_sbt(repo_path)
    mill_projects = _parse_build_sc(repo_path)
    if sbt_projects:
        index = ScalaProjectIndex(build_tool="sbt", projects=sbt_projects, depends_on=sbt_depends_on)
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
        scanned = _scan_packages(
            repo_path, project_dir, index.build_tool, prune_nested_git=prune_nested_git
        )
        for pkg, files in scanned.items():
            index.package_to_files.setdefault(pkg, []).extend(files)
    return index


def get_or_build_scala_index(ctx: ResolverContext) -> ScalaProjectIndex:
    cached = getattr(ctx, "_scala_index", None)
    if cached is not None:
        return cached
    index = build_scala_index(ctx.repo_path, prune_nested_git=ctx.prune_nested_git)
    ctx._scala_index = index  # type: ignore[attr-defined]
    return index


def resolve_via_scala_index(module_path: str, ctx: ResolverContext) -> str | None:
    index = get_or_build_scala_index(ctx)
    matches = index.lookup_class(module_path)
    if matches:
        return matches[0]
    return None
