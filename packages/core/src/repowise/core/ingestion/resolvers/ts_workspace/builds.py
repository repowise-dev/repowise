"""Build output named by ``package.json`` and the sources it was compiled from.

Only the unambiguous ``X.js``/``X.d.ts`` from ``src/X.ts`` shape is mapped, under
the conventional build dirs rather than tsconfig ``outDir`` (that needs a JSONC
``extends`` walk). A committed build is the file itself and is never remapped.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Container
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .exports import _DECLARATION_SUFFIXES, _build_exports_map, _ordered_export_targets
from .paths import _normalize_repo_rel, _probe_path
from .scan import _parsed_package_jsons

if TYPE_CHECKING:
    from ..context import ResolverContext


_BUILD_OUTPUT_JS = re.compile(r"^(?:dist|build|lib|out)/(.+?)(?:\.[mc]?js|\.d\.[mc]?ts)$")
_BUILD_SOURCE_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".mts", ".js")


def _manifest_targets(pkg_data: dict) -> tuple[list[str], tuple[str, ...]]:
    """``(bin/main/module targets, exports["."] targets best first, declarations skipped)``."""
    bin_ = pkg_data.get("bin")
    bins = bin_.values() if isinstance(bin_, dict) else (bin_,)
    starts = [
        t for t in (*bins, pkg_data.get("main"), pkg_data.get("module")) if isinstance(t, str)
    ]
    exports = pkg_data.get("exports")
    # A conditions-only object (``{"import": ..., "require": ...}``) is ``"."``.
    if isinstance(exports, dict) and any(str(k).startswith(".") for k in exports):
        exports = exports.get(".")
    ordered = _ordered_export_targets(exports) if exports is not None else ()
    return starts, tuple(t for t in ordered if not t.endswith(_DECLARATION_SUFFIXES))


def _built_output(pkg_dir: str, target: str) -> tuple[str, re.Match[str]] | None:
    """``(repo-relative path, match)`` when *target* names build output inside the repo."""
    rel = target.removeprefix("./")
    built = _BUILD_OUTPUT_JS.match(rel)
    if built is None:
        return None
    path = _normalize_repo_rel(f"{pkg_dir}/{rel}")
    # ``..`` in a manifest must not send the on-disk check outside the repo.
    if path == ".." or path.startswith("../"):
        return None
    return path, built


def _named_build_outputs(manifests: list[tuple[str, dict]]) -> set[str]:
    """Every in-repo build-output path the manifests' targets name, subpath exports included."""
    named: set[str] = set()
    for pkg_dir, data in manifests:
        starts, exports = _manifest_targets(data)
        subpaths = (t for targets in _build_exports_map(data).values() for t in targets)
        for target in (*starts, *exports, *subpaths):
            built = _built_output(pkg_dir, target)
            if built is not None:
                named.add(built[0])
    return named


def checked_in_builds(
    repo_path: Path | None, manifests: list[tuple[str, dict]], path_set: set[str]
) -> frozenset[str]:
    """Build outputs the manifests name that exist on disk and are not gitignored.

    Outside a git work tree every existing file counts as checked in.
    """
    if repo_path is None:
        return frozenset()
    return _git_checked_in(repo_path, _named_build_outputs(manifests) - path_set)


def _git_checked_in(repo_path: Path, paths: set[str]) -> frozenset[str]:
    """The *paths* that exist on disk and are not gitignored, in one git call."""
    on_disk = {path for path in paths if (repo_path / path).is_file()}
    if not on_disk:
        return frozenset()
    try:
        verdict = subprocess.run(
            ["git", "-C", str(repo_path), "check-ignore", "-z", "--stdin"],
            # Bytes: a text-mode pipe on Windows writes CRLF into each path.
            input="\0".join(sorted(on_disk)).encode("utf-8") + b"\0",
            capture_output=True,
            timeout=30,
        )
        # Exit 1 = none ignored; 128 = not a work tree (nothing is ignored).
        out = verdict.stdout.decode("utf-8", errors="replace") if verdict.returncode == 0 else ""
        ignored = set(filter(None, out.split("\0")))
    except (OSError, subprocess.SubprocessError):
        ignored = set()
    return frozenset(on_disk - ignored)


@dataclass
class _CheckedInBuilds:
    """``path in self``: is this build output checked in? Unseeded paths are asked on first use."""

    repo_path: Path | None
    verdict: dict[str, bool]

    def __contains__(self, path: object) -> bool:
        if not isinstance(path, str):
            return False
        if path not in self.verdict:
            self.verdict[path] = self.repo_path is not None and bool(
                _git_checked_in(self.repo_path, {path})
            )
        return self.verdict[path]


def get_checked_in_builds(ctx: ResolverContext) -> _CheckedInBuilds:
    """Memoized per resolver context, shared by the entry index and the resolver."""
    cached = getattr(ctx, "_ts_checked_in_builds", None)
    if cached is not None:
        return cached
    checked = checked_in_builds(ctx.repo_path, _parsed_package_jsons(ctx), ctx.path_set)
    builds = _CheckedInBuilds(ctx.repo_path, dict.fromkeys(checked, True))
    ctx._ts_checked_in_builds = builds  # type: ignore[attr-defined]
    return builds


def _built_source(
    pkg_dir: str, target: str, path_set: set[str], checked_in: Container[str]
) -> str | None:
    """The source a build-output *target* was compiled from, when that output is not checked in."""
    built = _built_output(pkg_dir, target)
    if built is None:
        return None
    base = _normalize_repo_rel(f"{pkg_dir}/src/{built[1].group(1)}")
    hit = next((base + ext for ext in _BUILD_SOURCE_EXTENSIONS if base + ext in path_set), None)
    return hit if hit is not None and built[0] not in checked_in else None


def probe_manifest_target(
    pkg_dir: str, target: str, path_set: set[str], checked_in: Container[str]
) -> str | None:
    """The indexed file a ``package.json`` target names, reading built output as its source."""
    if target.endswith(_DECLARATION_SUFFIXES):
        return None  # a declaration never starts a package, committed or not
    hit = _probe_path(f"{pkg_dir}/{target.removeprefix('./')}", path_set)
    return hit if hit is not None else _built_source(pkg_dir, target, path_set, checked_in)


def manifest_entry_paths(
    pkg_dir: str, pkg_data: dict, path_set: set[str], checked_in: Container[str] = frozenset()
) -> set[str]:
    """Files one ``package.json`` starts at: bin/main/module and the first resolving ``exports["."]``."""
    starts, exports = _manifest_targets(pkg_data)
    found = {
        hit
        for t in starts
        if (hit := probe_manifest_target(pkg_dir, t, path_set, checked_in)) is not None
    }
    for target in exports:
        hit = probe_manifest_target(pkg_dir, target, path_set, checked_in)
        if hit is not None:
            found.add(hit)
            break
    return found
