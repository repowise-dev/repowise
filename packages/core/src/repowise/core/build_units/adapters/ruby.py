"""Ruby gems, one per ``Gemfile`` or ``.gemspec`` root, linked by ``path:`` deps.

The name is the ``.gemspec``'s ``spec.name`` (its file stem when the name is
not a literal), else the directory name. A root whose ``Gemfile`` or
``.gemspec`` cannot be read is left to the manifest fallback, as is a root
whose name an earlier root already took. No Ruby reader exists, so the
``path:`` deps are a regex over the Gemfile: each literal ``path:`` that
resolves to an in-repo gem root is a ``ruby:<name>`` dep, one pointing
outside the repo links nothing. A repo without Ruby files costs one cheap
check over ``ctx.path_set``.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

import structlog

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext

log = structlog.get_logger(__name__)

# Gemfile:  gem "lib", path: "../lib"  (single quotes and `:path =>` likewise)
_GEMFILE_PATH_DEP_RE = re.compile(
    r"""^\s*gem\s*\(?\s*["'](?:[A-Za-z0-9_.\-]+)["']\s*,[^\n]*?"""
    r"""(?:path\s*:|:path\s*=>)\s*["']([^"']+)["']""",
    re.MULTILINE,
)
# gemspec name:  spec.name = "mygem" / s.name = 'mygem' / spec.name = %q{mygem}, or the
# first argument of ``Gem::Specification.new "mygem", "1.0" do |s|``.
_GEMSPEC_NAME_RE = re.compile(
    r"""^\s*\w+\.name\s*=\s*(?:%q[{(]|["'])([A-Za-z0-9_.\-]+)""", re.MULTILINE
)
_GEMSPEC_NEW_NAME_RE = re.compile(r"""Specification\.new\s*\(?\s*["']([A-Za-z0-9_.\-]+)["']""")


def _read_text(path: Path) -> str | None:
    """*path*'s text, or None when it cannot be read: the caller falls back."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("build_units_ruby_unreadable", manifest=str(path), error=str(exc))
        return None


def _gem_roots(ctx: ResolverContext) -> dict[str, dict[str, str]]:
    """Each ``Gemfile``/``.gemspec`` root to its manifest paths, from ``path_set``.

    A root holds at most one ``Gemfile`` and one ``.gemspec``: the first
    gemspec in sorted order names the gem.
    """
    roots: dict[str, dict[str, str]] = {}
    for path in sorted(ctx.path_set):
        if path == "Gemfile" or path.endswith("/Gemfile"):
            root = path[: -len("/Gemfile")] if "/" in path else ""
            roots.setdefault(root, {})["gemfile"] = path
        elif path.endswith(".gemspec"):
            root = path.rsplit("/", 1)[0] if "/" in path else ""
            roots.setdefault(root, {}).setdefault("gemspec", path)
    return roots


def _gemspec_name(text: str, gemspec: str) -> str:
    """The gemspec's declared name, else its file stem, as RubyGems expects."""
    match = _GEMSPEC_NAME_RE.search(text) or _GEMSPEC_NEW_NAME_RE.search(text)
    if match:
        return match.group(1)
    return gemspec.rsplit("/", 1)[-1][: -len(".gemspec")]


def _path_dep_roots(text: str, root: str) -> set[str]:
    """The repo-relative roots each literal ``path:`` gem in *text* points at."""
    roots: set[str] = set()
    for match in _GEMFILE_PATH_DEP_RE.finditer(text):
        rel = posixpath.normpath(posixpath.join(root, match.group(1)))
        roots.add("" if rel == "." else rel)
    return roots


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None or not any(
        path == "Gemfile" or path.endswith("/Gemfile") or path.endswith(".gemspec")
        for path in ctx.path_set
    ):
        return Found([])
    repo = ctx.repo_path
    # root -> (name, manifest, source, path-dep roots)
    read: dict[str, tuple[str, str, str, set[str]]] = {}
    for root, files in sorted(_gem_roots(ctx).items()):
        gemspec, gemfile = files.get("gemspec"), files.get("gemfile")
        if gemspec is not None:
            text = _read_text(repo / gemspec)
            if text is None:
                continue  # unreadable gemspec: the manifest fallback claims the root
            name, manifest, source = _gemspec_name(text, gemspec), gemspec, "gemspec"
        else:
            name = root.rsplit("/", 1)[-1] or "."
            manifest, source = gemfile, "gemfile"
        paths: set[str] = set()
        if gemfile is not None:
            text = _read_text(repo / gemfile)
            if text is None:
                continue  # unreadable Gemfile: the manifest fallback claims the root
            paths = _path_dep_roots(text, root)
        read[root] = (name, manifest, source, paths)
    # A name taken twice keeps its first root in sorted order; the later
    # root is no unit here, so the fallback claims it, and a path dep
    # pointing at it links nothing rather than the other gem.
    by_root: dict[str, str] = {}
    taken: set[str] = set()
    for root, (name, _manifest, _source, _paths) in read.items():
        uid = f"ruby:{name}"
        if uid in taken:
            log.warning("build_units_duplicate_id", unit_id=uid, root=root)
            continue
        taken.add(uid)
        by_root[root] = uid

    claims: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for root, uid in by_root.items():
        name, manifest, source, paths = read[root]
        claims.append(
            (
                BuildUnit(
                    id=uid,
                    ecosystem="ruby",
                    name=name,
                    root=root,
                    manifest=manifest,
                    source=source,
                    deps=tuple(sorted(by_root[path] for path in paths if path in by_root)),
                ),
                (),
            )
        )
    return Found(claims)
