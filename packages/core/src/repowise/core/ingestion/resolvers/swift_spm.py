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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext


_TARGET_RE = re.compile(
    r"\.(target|executableTarget|testTarget|systemLibrary|binaryTarget|plugin)\s*\("
    r"(?P<body>[^()]*(?:\([^()]*\)[^()]*)*)\)",
    re.DOTALL,
)
_NAME_RE = re.compile(r'name\s*:\s*"([^"]+)"')
_PATH_RE = re.compile(r'path\s*:\s*"([^"]+)"')


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


def build_swift_targets(repo_path: Path | None) -> dict[str, str]:
    """Walk the repo for every ``Package.swift``, merge their target maps.

    Each target's source dir is prefixed with the package's directory
    relative to the repo root so the resolver can match against
    repo-relative paths in ``ctx.path_set``.
    """
    if repo_path is None or not repo_path.is_dir():
        return {}
    merged: dict[str, str] = {}
    for pkg_swift in repo_path.rglob("Package.swift"):
        try:
            pkg_dir = pkg_swift.parent.relative_to(repo_path).as_posix()
        except ValueError:
            continue
        for name, target_dir in parse_package_swift(pkg_swift).items():
            full = f"{pkg_dir}/{target_dir}".lstrip("/") if pkg_dir != "." else target_dir
            merged.setdefault(name, full)
    return merged


def get_or_build_swift_targets(ctx: "ResolverContext") -> dict[str, str]:
    cached = getattr(ctx, "_swift_targets", None)
    if cached is not None:
        return cached
    mapping = build_swift_targets(ctx.repo_path)
    ctx._swift_targets = mapping  # type: ignore[attr-defined]
    return mapping


def resolve_via_swift_targets(module_path: str, ctx: "ResolverContext") -> str | None:
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
    matches = [p for p in ctx.path_set if p.endswith(".swift") and p.startswith(target_prefix)]
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
