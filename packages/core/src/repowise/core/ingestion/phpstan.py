"""PHPStan type-test corpora: PHP files only the analyser runs.

A library proves its generic types with files of ``assertType()`` calls that
PHPStan analyses (laravel/framework's ``types/``, listed in
``phpstan.types.neon.dist``). Nothing imports them and no autoloader loads
them, so dead code would read the whole corpus as unreachable.

A folder is a corpus when a ``phpstan*.neon`` beside a ``composer.json`` lists
it under ``paths``, it lies outside every autoload root (shipped code is
autoloaded; ``src`` analysed by the same tool is not a corpus), and a file in
it imports ``PHPStan\\Testing\\assertType``.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from .composer import ComposerManifest

#: PHPStan's own config names: ``phpstan.neon``, ``phpstan.neon.dist``,
#: ``phpstan.types.neon.dist``.
_CONFIG_GLOBS = ("phpstan*.neon", "phpstan*.neon.dist")
#: The ``paths:`` key and the more indented lines under it.
_PATHS_BLOCK_RE = re.compile(
    r"^(?P<indent>[ \t]*)paths:[ \t]*\n(?P<body>(?:(?P=indent)[ \t]+\S.*\n?|[ \t]*\n)*)",
    re.MULTILINE,
)
_LIST_ITEM_RE = re.compile(r"""^[ \t]*-[ \t]*['"]?(?P<value>[^'"\s#]+)""", re.MULTILINE)
_TYPE_TEST_MARKER = "PHPStan\\Testing\\assertType"


def analysed_paths(neon: str) -> list[str]:
    """The ``paths`` list of a PHPStan config, as written.

    A pattern rather than a NEON parser: ``paths`` is always a plain list of
    strings. Entries built from a parameter (``%rootDir%``) are skipped.
    """
    block = _PATHS_BLOCK_RE.search(neon)
    if block is None:
        return []
    values = _LIST_ITEM_RE.findall(block.group("body"))
    return [value for value in values if "%" not in value]


def _autoload_roots(manifest: ComposerManifest) -> list[str]:
    dirs = [d for _, ds in (*manifest.psr4, *manifest.psr0) for d in ds]
    return [*dirs, *manifest.classmap, *manifest.files]


def _overlaps(a: str, b: str) -> bool:
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def type_test_files(
    repo_root: Path,
    manifests: Iterable[ComposerManifest],
    php_paths: Iterable[str],
    text_of: Callable[[str], str | None],
) -> set[str]:
    """Every PHP file in a PHPStan type-test corpus of *repo_root*."""
    paths = sorted(php_paths)
    out: set[str] = set()
    for manifest in manifests:
        roots = _autoload_roots(manifest)
        for folder in _corpus_candidates(repo_root, manifest.rel_dir):
            if any(_overlaps(folder, root) for root in roots):
                continue
            inside = [p for p in paths if p.startswith(folder + "/")]
            if any(_TYPE_TEST_MARKER in (text_of(p) or "") for p in inside):
                out.update(inside)
    return out


def _corpus_candidates(repo_root: Path, rel_dir: str) -> set[str]:
    """Repo-relative folders the PHPStan configs in *rel_dir* analyse."""
    base = repo_root / rel_dir if rel_dir else repo_root
    entries = [
        entry
        for pattern in _CONFIG_GLOBS
        for config in base.glob(pattern)
        for entry in analysed_paths(_read(config))
    ]
    folders = {posixpath.normpath(posixpath.join(rel_dir, e.rstrip("/"))) for e in entries}
    return {f for f in folders if not f.startswith("..")}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
