"""Entry points named by tool configs: MDX imports, vitest globs, Grafana plugins, npm scripts.

The static TS/JS graph never sees these consumers, so without them the files
they reach would read as unreachable.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from .scan import _get_repo_scan, _parsed_package_jsons

if TYPE_CHECKING:
    from ..context import ResolverContext


_MDX_IMPORT_RE = re.compile(
    r"""import\s+
        (?:type\s+)?
        (?:\{[^}]*\}|\*\s+as\s+\w+|\w+(?:\s*,\s*\{[^}]*\})?)
        \s+from\s+['"]([^'"]+)['"]""",
    re.VERBOSE,
)

_VITEST_INCLUDE_RE = re.compile(
    r"""include\s*:\s*\[\s*((?:['"][^'"]+['"]\s*,?\s*)+)\]""",
    re.MULTILINE,
)
_VITEST_STRING_RE = re.compile(r"""['"]([^'"]+)['"]""")


def find_mdx_import_targets(ctx: ResolverContext) -> set[str]:
    """Return repo-relative paths reached via ``import … from '...'`` in MDX files.

    The regex is deliberately narrow; anything fancier is no edge rather than a parser dependency.
    """
    if ctx.repo_path is None:
        return set()
    from ..typescript import resolve_ts_js_import

    targets: set[str] = set()
    for mdx in _get_repo_scan(ctx).mdx_files:
        try:
            text = mdx.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        try:
            rel = mdx.relative_to(ctx.repo_path).as_posix()
        except ValueError:
            continue
        for match in _MDX_IMPORT_RE.finditer(text):
            spec = match.group(1)
            # The TS resolver leaves ``..`` unflattened, so flatten it here.
            if spec.startswith("."):
                import os as _os
                joined = _os.path.normpath(_os.path.join(_os.path.dirname(rel), spec))
                joined = joined.replace("\\", "/")
                spec_for_resolve = "./" + joined
                resolved = resolve_ts_js_import(spec_for_resolve, "_root_.mdx", ctx)
            else:
                resolved = resolve_ts_js_import(spec, rel, ctx)
            if resolved is None:
                continue
            if resolved.startswith("external:"):
                continue
            targets.add(resolved)
    return targets


def _vitest_glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate a vitest/minimatch glob into a regex matching repo paths.

    ``**`` is zero or more segments, ``*`` one segment, ``?`` one non-``/``
    char; ``fnmatch`` would make ``foo/**/x`` miss ``foo/x``.
    """
    out: list[str] = ["^"]
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if i + 1 < len(glob) and glob[i + 1] == "*":
                # Consume a trailing ``/`` so ``foo/**/bar`` matches ``foo/bar``.
                i += 2
                if i < len(glob) and glob[i] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c in ".+()|^$[]{}\\":
            out.append("\\" + c)
            i += 1
        else:
            out.append(c)
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _glob_matches(regex: re.Pattern[str], path_set: set[str]) -> set[str]:
    return {candidate for candidate in path_set if regex.match(candidate)}


def _vitest_include_globs(text: str) -> Iterator[str]:
    """Every string inside a vitest config's ``include: [...]`` lists."""
    for inc_match in _VITEST_INCLUDE_RE.finditer(text):
        for str_match in _VITEST_STRING_RE.finditer(inc_match.group(1)):
            yield str_match.group(1)


def _vitest_glob_targets(cfg_dir: Path, glob_pat: str, ctx: ResolverContext) -> set[str]:
    base = (cfg_dir / glob_pat).as_posix()
    try:
        rel_glob = Path(base).relative_to(ctx.repo_path).as_posix()
    except ValueError:
        return set()
    return _glob_matches(_vitest_glob_to_regex(rel_glob), ctx.path_set)


def find_vitest_include_targets(ctx: ResolverContext) -> set[str]:
    """Return repo-relative source files matching vitest ``include`` globs."""
    if ctx.repo_path is None:
        return set()

    targets: set[str] = set()
    for cfg in _get_repo_scan(ctx).vitest_configs:
        try:
            text = cfg.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for glob_pat in _vitest_include_globs(text):
            targets |= _vitest_glob_targets(cfg.parent, glob_pat, ctx)
    return targets


_GRAFANA_ENTRY_EXTS: tuple[str, ...] = (
    ".ts", ".tsx", ".js", ".jsx",
)


def find_grafana_plugin_targets(ctx: ResolverContext) -> set[str]:
    """Grafana plugin entries: ``module.*`` beside ``plugin.json`` or in its ``src/``."""
    if ctx.repo_path is None:
        return set()

    targets: set[str] = set()
    for plugin_file in _get_repo_scan(ctx).plugin_jsons:
        try:
            rel_dir = plugin_file.parent.relative_to(ctx.repo_path).as_posix()
        except ValueError:
            continue

        dir_prefix = "" if rel_dir == "." else f"{rel_dir}/"
        candidate_prefixes = (dir_prefix, f"{dir_prefix}src/")

        for prefix in candidate_prefixes:
            for ext in _GRAFANA_ENTRY_EXTS:
                candidate = f"{prefix}module{ext}"
                if candidate in ctx.path_set:
                    targets.add(candidate)

    return targets


_NPM_SCRIPT_SOURCE_EXTS: tuple[str, ...] = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
)

_NPM_SCRIPT_RUNNERS: frozenset[str] = frozenset({
    "tsx", "ts-node", "ts-node-esm", "vite-node", "swc-node",
    "node", "deno", "bun",
    "esbuild", "rollup", "vite", "webpack",
})

# Directories of maintained scripts run with runtime CLI arguments (benches, examples, demos).
_EXPERIMENT_DIR_NAMES: frozenset[str] = frozenset({
    "bench", "benches", "benchmark", "benchmarks",
    "treeshake", "treeshaking",
    "example", "examples",
    "demo", "demos",
    "sample", "samples",
    "playground", "playgrounds",
    "scratch",
    "scripts",
})


def _iter_script_tokens(script: str) -> list[str]:
    """Split a script command on whitespace and shell separators; quotes are stripped, not honoured."""
    cleaned = re.sub(r"&&|\|\||;|\|", " ", script)
    return [tok.strip("'\"") for tok in cleaned.split() if tok.strip("'\"")]


# Bare directory tokens (``eslint src runtime-tests build``).
_SCRIPT_DIR_TOKENS: frozenset[str] = frozenset({
    "src", "lib", "app", "test", "tests", "scripts",
    "benchmarks", "perf-measures", "runtime-tests", "build",
    "examples",
})


def _is_script_source(path: str) -> bool:
    return path.lower().endswith(_NPM_SCRIPT_SOURCE_EXTS)


def _source_files(files: list[str] | None) -> Iterator[str]:
    return (f for f in files or () if _is_script_source(f))


def _dirs_in_repo(path_set: set[str]) -> dict[str, list[str]]:
    """``{directory: files anywhere under it}`` for every directory in *path_set*."""
    dirs_in_repo: dict[str, list[str]] = {}
    for p in path_set:
        idx = 0
        while True:
            slash = p.find("/", idx)
            if slash == -1:
                break
            dirs_in_repo.setdefault(p[:slash], []).append(p)
            idx = slash + 1
    return dirs_in_repo


def _normalize_script_path(candidate: str) -> str:
    """Normalise ``a/./b`` and ``a/../b`` segments."""
    parts: list[str] = []
    for seg in candidate.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def _source_token_targets(token: str, pkg_prefix: str, path_set: set[str]) -> tuple[str, ...]:
    candidate = (pkg_prefix + token).lstrip("./")
    candidate = re.sub(r"\\", "/", candidate)
    norm = _normalize_script_path(candidate)
    return (norm,) if norm in path_set else ()


def _glob_token_targets(token: str, pkg_prefix: str, path_set: set[str]) -> set[str]:
    rel_glob = (pkg_prefix + token).lstrip("./")
    if rel_glob.startswith("./"):
        rel_glob = rel_glob[2:]
    try:
        regex = _vitest_glob_to_regex(rel_glob)
    except re.error:
        return set()
    return _glob_matches(regex, path_set)


def _script_token_targets(
    token: str, pkg_prefix: str, dirs_in_repo: dict[str, list[str]], path_set: set[str]
) -> Iterable[str]:
    """Source files one script token names, or nothing."""
    if not token or token.startswith("-"):
        return ()
    has_glob = "*" in token or "?" in token
    if _is_script_source(token) and not has_glob:
        return _source_token_targets(token, pkg_prefix, path_set)
    # A glob needs a slash, so runner names and flags are never expanded.
    if has_glob and "/" in token:
        return _glob_token_targets(token, pkg_prefix, path_set)
    if "/" not in token and token in _SCRIPT_DIR_TOKENS:
        return _source_files(dirs_in_repo.get((pkg_prefix + token).rstrip("/")))
    return ()


def _package_script_targets(
    pkg_rel: str, data: dict, dirs_in_repo: dict[str, list[str]], path_set: set[str]
) -> set[str]:
    """Source files one ``package.json``'s ``scripts`` reach."""
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        return set()
    pkg_prefix = "" if pkg_rel in ("", ".") else f"{pkg_rel}/"
    targets: set[str] = set()

    if posixpath.basename(pkg_rel).lower() in _EXPERIMENT_DIR_NAMES:
        targets.update(_source_files(dirs_in_repo.get(pkg_rel) if pkg_rel else None))

    for command in scripts.values():
        if not isinstance(command, str):
            continue
        for token in _iter_script_tokens(command):
            targets.update(_script_token_targets(token, pkg_prefix, dirs_in_repo, path_set))
    return targets


def _experiment_dir_targets(dirs_in_repo: dict[str, list[str]]) -> Iterator[str]:
    for dir_rel, files in dirs_in_repo.items():
        if dir_rel.rsplit("/", 1)[-1].lower() in _EXPERIMENT_DIR_NAMES:
            yield from _source_files(files)


def find_npm_script_entry_targets(ctx: ResolverContext) -> set[str]:
    """Return repo-relative source files ``package.json`` scripts reach.

    Covers source paths (``tsx bench/x.ts``), glob arguments (``"src/**/*.ts"``),
    bare directory tokens, and every source file under an experiment directory.
    """
    if ctx.repo_path is None:
        return set()

    path_set = ctx.path_set
    dirs_in_repo = _dirs_in_repo(path_set)
    targets: set[str] = set()
    for pkg_rel, data in _parsed_package_jsons(ctx):
        targets |= _package_script_targets(pkg_rel, data, dirs_in_repo, path_set)

    # Experiment directories nested anywhere, with or without their own package.json.
    targets.update(_experiment_dir_targets(dirs_in_repo))
    return targets
