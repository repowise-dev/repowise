"""Swift Package Manager target → directory parsing.

``Package.swift`` is itself Swift source, but real packages overwhelmingly
use a small set of conventional declarations:

    .target(name: "Foo")
    .target(name: "Foo", path: "Custom/Path")
    .executableTarget(name: "Bar")
    .testTarget(name: "FooTests")

Regex covers >95% of real-world packages without requiring tree-sitter-swift
parsing. Targets that omit ``path:`` default to ``Sources/<name>`` (or
``Tests/<name>`` for test targets).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from repowise.core.fs_walk import WalkSnapshot, glob_via

if TYPE_CHECKING:
    from .context import ResolverContext


_TARGET_RE = re.compile(
    r"\.(target|executableTarget|testTarget|systemLibrary|binaryTarget|plugin)\s*\("
    r"(?P<body>[^()]*(?:\([^()]*\)[^()]*)*)\)",
    re.DOTALL,
)
_NAME_RE = re.compile(r'name\s*:\s*"([^"]+)"')
_PATH_RE = re.compile(r'path\s*:\s*"([^"]+)"')

# Matches the first argument of Package(name: "..."), ignoring other args.
_PACKAGE_NAME_RE = re.compile(r'\bPackage\s*\(\s*name\s*:\s*"([^"]+)"')

# Matches ``.package(`` calls with a body that tolerates one level of nested
# parentheses and brackets (sufficient for version specs like
# ``.upToNextMajor(from: "1.0.0")``).
_PACKAGE_DEP_RE = re.compile(
    r"\.package\s*\("
    r"(?P<body>(?:[^()\[\]]|\([^()]*\)|\[[^\[\]]*\])*?)\)",
    re.DOTALL,
)
_URL_RE = re.compile(r'url\s*:\s*"[^"]*"')
# Strings first, so a "//" inside one is not taken for a comment.
_STRING_OR_COMMENT_RE = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*[\s\S]*?\*/')


class SwiftPackage(NamedTuple):
    """A parsed Swift package manifest: its name and raw path dependencies."""

    name: str
    path_deps: tuple[str, ...]


def _strip_comments(text: str) -> str:
    """Remove ``//`` line comments and ``/* */`` blocks outside string literals.

    This keeps commented-out ``.package(path: ...)`` calls from being picked
    up while a ``//`` inside a string (``"https://..."``) survives. Strings are
    ``"..."`` with backslash escapes and triple-quoted multi-line literals.
    Nested block comments and raw ``#"..."#`` strings are not special-cased.
    """
    return _STRING_OR_COMMENT_RE.sub(
        lambda m: m.group(0) if m.group(0).startswith('"') else " ", text
    )


def parse_package_manifest(path: Path) -> SwiftPackage | None:
    """Parse a ``Package.swift`` for its package name and local path dependencies.

    Returns ``None`` if the file cannot be read, does not declare a package
    name, or declares no package at all.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    text = _strip_comments(text)

    # Package name must be the FIRST argument of Package(.
    name_match = _PACKAGE_NAME_RE.search(text)
    if not name_match:
        return None
    name = name_match.group(1)

    path_deps: list[str] = []
    for dep_match in _PACKAGE_DEP_RE.finditer(text):
        dep_body = dep_match.group("body")
        if _URL_RE.search(dep_body):
            continue
        path_match = _PATH_RE.search(dep_body)
        if path_match:
            path_deps.append(path_match.group(1))

    return SwiftPackage(name=name, path_deps=tuple(path_deps))


def parse_package_swift(path: Path) -> dict[str, str]:
    """Return ``{module_name: source_dir_posix}`` extracted from a single
    ``Package.swift`` file. Paths are relative to the file's directory.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}

    result: dict[str, str] = {}
    for match in _TARGET_RE.finditer(text):
        kind = match.group(1)
        body = match.group("body")
        name_match = _NAME_RE.search(body)
        if not name_match:
            continue
        name = name_match.group(1)
        path_match = _PATH_RE.search(body)
        if path_match:
            target_dir = path_match.group(1).strip("./")
        else:
            base = "Tests" if kind == "testTarget" else "Sources"
            target_dir = f"{base}/{name}"
        result[name] = target_dir
    return result


def build_swift_targets(
    repo_path: Path | None,
    *,
    prune_nested_git: bool = True,
    snapshot: WalkSnapshot | None = None,
) -> dict[str, str]:
    """Walk the repo for every ``Package.swift``, merge their target maps.

    Each target's source dir is prefixed with the package's directory
    relative to the repo root so the resolver can match against
    repo-relative paths in ``ctx.path_set``.
    """
    if repo_path is None or not repo_path.is_dir():
        return {}
    merged: dict[str, str] = {}
    for pkg_swift in glob_via(
        snapshot, repo_path, "Package.swift", prune_nested_git=prune_nested_git
    ):
        try:
            pkg_dir = pkg_swift.parent.relative_to(repo_path).as_posix()
        except ValueError:
            continue
        for name, target_dir in parse_package_swift(pkg_swift).items():
            full = f"{pkg_dir}/{target_dir}".lstrip("/") if pkg_dir != "." else target_dir
            merged.setdefault(name, full)
    return merged


def get_or_build_swift_targets(ctx: ResolverContext) -> dict[str, str]:
    cached = getattr(ctx, "_swift_targets", None)
    if cached is not None:
        return cached
    mapping = build_swift_targets(
        ctx.repo_path, prune_nested_git=ctx.prune_nested_git, snapshot=ctx.walk_snapshot
    )
    ctx._swift_targets = mapping  # type: ignore[attr-defined]
    return mapping


def resolve_via_swift_targets(module_path: str, ctx: ResolverContext) -> str | None:
    """Pick a ``.swift`` file under the SPM target's source dir matching the
    last identifier of *module_path*. Returns repo-relative path or None.
    """
    mapping = get_or_build_swift_targets(ctx)
    if not mapping:
        return None
    target_name = module_path.split(".")[0]
    target_dir = mapping.get(target_name)
    if not target_dir:
        return None
    # Pick any .swift file under that dir; prefer one whose stem matches.
    target_prefix = target_dir.rstrip("/") + "/"
    matches = [p for p in ctx.sorted_paths if p.endswith(".swift") and p.startswith(target_prefix)]
    if not matches:
        # Module imported by name but no source files indexed yet (target may
        # be excluded). Treat as unresolved; caller falls back further.
        return None
    last_id = module_path.split(".")[-1]
    for p in matches:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem.lower() == last_id.lower():
            return p
    # No stem hit — return the first file in the target so call/heritage
    # resolution at least lands inside the right module.
    return matches[0]
