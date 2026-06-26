"""npm/yarn/pnpm workspace package resolution for TypeScript imports.

Reads the root ``package.json``'s ``workspaces`` field (string list or
``{"packages": [...]}`` form), expands glob patterns, and reads each
sibling package's ``name`` field. The resulting ``{pkg_name: dir_posix}``
map lets the TS resolver turn ``import x from "@myorg/foo"`` into the
correct intra-repo file rather than an ``external:`` node.

Subpath imports (``@myorg/foo/bar/baz``) honour Node.js ``"exports"``
subpath patterns when the workspace's ``package.json`` declares them:

    "exports": {
      ".":             "./src/index.ts",
      "./util":        "./src/util.ts",
      "./graph/*":     "./src/graph/*.tsx",
      "./modules/*":   { "import": "./src/modules/*.ts" }
    }

Conditional values (``{"import": ..., "default": ..., ...}``) are
flattened to the first plausible source target. Packages without an
``exports`` field fall back to the legacy ``<pkg>/<subpath>`` probe so
"plain" monorepo layouts keep working.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import ResolverContext


# ---------------------------------------------------------------------------
# Single pruned filesystem scan
# ---------------------------------------------------------------------------
# The mdx / vitest-config / package.json finders below all need to locate
# files by name across the repo. Doing that with per-finder ``rglob`` calls
# walked the ENTIRE tree (including node_modules, virtualenvs, .git) once
# per pattern — 12+ full walks, measured at ~6 min on a medium repo with a
# checked-in .venv. One ``os.walk`` with directory pruning, memoized on the
# resolver context, replaces all of them.
#
# The prune list is deliberately narrow: vendored/derived trees that can
# never contain *our* manifests or docs. ``.github`` and other dot-dirs that
# may hold real package.json files (custom actions) are NOT pruned wholesale.
_SCAN_PRUNE_DIRS: frozenset[str] = frozenset({
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".tox",
    "__pycache__",
    ".repowise",
    ".repowise.prebench-bak",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    ".parcel-cache",
    ".yarn",
    ".pnpm-store",
})

_VITEST_CONFIG_NAMES: frozenset[str] = frozenset({
    "vitest.config.ts", "vitest.config.js", "vitest.config.mts",
    "vitest.config.mjs", "vitest.config.cjs", "vitest.config.cts",
    "vite.config.ts", "vite.config.js", "vite.config.mts",
    "vite.config.mjs",
})


@dataclass
class _RepoFileScan:
    """File locations gathered by the single pruned walk."""

    mdx_files: list[Path] = field(default_factory=list)
    vitest_configs: list[Path] = field(default_factory=list)
    package_jsons: list[Path] = field(default_factory=list)


def _scan_repo_files(repo_path: Path, *, prune_nested_git: bool = True) -> _RepoFileScan:
    """One pruned walk collecting every finder's target files.

    Uses the shared :func:`repowise.core.fs_walk.walk_repo` (nested-git
    pruning, cycle guard) with this module's deliberately-narrow prune set.
    """
    from repowise.core.fs_walk import walk_repo

    scan = _RepoFileScan()
    for dirpath, _dirnames, filenames in walk_repo(
        repo_path, prune_dirs=_SCAN_PRUNE_DIRS, prune_nested_git=prune_nested_git
    ):
        for fname in filenames:
            if fname.endswith(".mdx"):
                scan.mdx_files.append(Path(dirpath) / fname)
            elif fname in _VITEST_CONFIG_NAMES:
                scan.vitest_configs.append(Path(dirpath) / fname)
            elif fname == "package.json":
                scan.package_jsons.append(Path(dirpath) / fname)
    return scan


def _get_repo_scan(ctx: "ResolverContext") -> _RepoFileScan:
    """Memoized accessor — one walk per resolver context."""
    cached = getattr(ctx, "_ts_repo_file_scan", None)
    if cached is not None:
        return cached
    scan = (
        _scan_repo_files(ctx.repo_path, prune_nested_git=ctx.prune_nested_git)
        if ctx.repo_path is not None
        else _RepoFileScan()
    )
    ctx._ts_repo_file_scan = scan  # type: ignore[attr-defined]
    return scan


# Order in which we collapse Node "conditional exports" objects down to
# a single target. Source-pointing conditions come first so a TS-aware
# static analyser sees the original ``.ts`` file rather than a built
# artefact, then ESM/default, with CJS last.
_CONDITION_PRIORITY: tuple[str, ...] = (
    "source",
    "import",
    "default",
    "node",
    "require",
    "types",
    "browser",
)


def _flatten_export_value(value: Any) -> str | None:
    """Collapse a Node ``exports`` entry to a single relative target string.

    Returns ``None`` for blocked entries (``null``) or shapes we can't
    handle. Recursively unwraps nested condition objects and arrays.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for cond in _CONDITION_PRIORITY:
            inner = value.get(cond)
            if inner is None:
                continue
            flat = _flatten_export_value(inner)
            if flat:
                return flat
        return None
    if isinstance(value, list):
        for item in value:
            flat = _flatten_export_value(item)
            if flat:
                return flat
    return None


def _build_exports_map(pkg_data: dict) -> dict[str, str]:
    """Return ``{exports_key: relative_target}`` for a workspace package.

    ``exports`` may be a single string (shorthand for ``{".": <str>}``)
    or a subpath dict. Keys that don't start with ``.`` are dropped (the
    Node spec disallows mixing main-entry shorthand with subpath maps).
    """
    raw = pkg_data.get("exports")
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {".": raw}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.startswith("."):
            continue
        flat = _flatten_export_value(value)
        if flat is None:
            continue
        out[key] = flat
    return out


def _match_export_key(subpath: str, exports_map: dict[str, str]) -> str | None:
    """Resolve a subpath against an ``exports`` map.

    ``subpath`` is the part of the import specifier after the package
    name, with no leading slash (``""`` for the bare package, ``"lib/x"``
    for ``@org/pkg/lib/x``). Exact keys win over wildcard patterns; among
    wildcards the longest static prefix wins (Node spec).
    """
    key = "." if subpath == "" else "./" + subpath
    if key in exports_map:
        return exports_map[key]
    best_target: str | None = None
    best_prefix_len = -1
    for pattern, target in exports_map.items():
        if "*" not in pattern:
            continue
        prefix, _, suffix = pattern.partition("*")
        if not key.startswith(prefix):
            continue
        if suffix and not key.endswith(suffix):
            continue
        captured = (
            key[len(prefix) : len(key) - len(suffix)]
            if suffix
            else key[len(prefix) :]
        )
        resolved = target.replace("*", captured, 1) if "*" in target else target
        if len(prefix) > best_prefix_len:
            best_target = resolved
            best_prefix_len = len(prefix)
    return best_target


def _read_workspaces_field(pkg_data: dict) -> list[str]:
    ws = pkg_data.get("workspaces")
    if isinstance(ws, list):
        return [str(p) for p in ws if isinstance(p, str)]
    if isinstance(ws, dict):
        packages = ws.get("packages")
        if isinstance(packages, list):
            return [str(p) for p in packages if isinstance(p, str)]
    return []


def build_workspace_map(repo_path: Path | None) -> dict[str, str]:
    """Return ``{package_name: dir_posix}`` for every workspace package.

    Empty dict if no root ``package.json`` or no ``workspaces`` field.
    """
    return {name: info["dir"] for name, info in build_workspace_info(repo_path).items()}


def build_workspace_info(repo_path: Path | None) -> dict[str, dict[str, Any]]:
    """Return ``{pkg_name: {"dir": <posix>, "exports": {...}, "main": str|None}}``.

    A richer counterpart to :func:`build_workspace_map` that also carries
    the workspace package's ``exports`` subpath map (Node.js spec) plus
    ``main``/``module`` entry-point hints. Lets the resolver translate
    sub-path imports through the package's own resolution rules instead
    of probing ``<pkg>/<subpath>`` blindly.
    """
    if repo_path is None or not repo_path.is_dir():
        return {}
    root_pkg = repo_path / "package.json"
    if not root_pkg.is_file():
        return {}
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    patterns = _read_workspaces_field(data)
    if not patterns:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for pattern in patterns:
        if pattern == ".":
            ws_dirs = [repo_path]
        else:
            ws_dirs = repo_path.glob(pattern)
        for ws_dir in ws_dirs:
            if not ws_dir.is_dir():
                continue
            ws_pkg = ws_dir / "package.json"
            if not ws_pkg.is_file():
                continue
            try:
                ws_data = json.loads(ws_pkg.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if not isinstance(ws_data, dict):
                continue
            name = ws_data.get("name")
            if not isinstance(name, str) or not name:
                continue
            try:
                rel = ws_dir.relative_to(repo_path).as_posix()
            except ValueError:
                continue
            result[name] = {
                "dir": rel,
                "exports": _build_exports_map(ws_data),
                "main": ws_data.get("module") if isinstance(ws_data.get("module"), str)
                        else (ws_data.get("main") if isinstance(ws_data.get("main"), str) else None),
            }
    return result


def get_or_build_workspace_info(ctx: "ResolverContext") -> dict[str, dict[str, Any]]:
    cached = getattr(ctx, "_ts_workspace_info", None)
    if cached is not None:
        return cached
    info = build_workspace_info(ctx.repo_path)
    ctx._ts_workspace_info = info  # type: ignore[attr-defined]
    return info


def get_or_build_workspace_map(ctx: "ResolverContext") -> dict[str, str]:
    """Backward-compat shim — kept for callers that only need name → dir."""
    return {name: info["dir"] for name, info in get_or_build_workspace_info(ctx).items()}


_PROBE_EXTENSIONS: tuple[str, ...] = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
)


def _probe_path(base: str, path_set: set[str]) -> str | None:
    """Locate a concrete file for ``base`` (a repo-relative path stem).

    Tries the path as-is first (handles targets that already carry an
    extension, e.g. ``"./src/graph/sigma-canvas.tsx"`` from an exports
    pattern). Then probes common TS/JS extensions and ``index.*``
    children so directory-shaped specifiers resolve to a barrel file.
    """
    if base in path_set:
        return base
    for ext in _PROBE_EXTENSIONS:
        cand = base + ext
        if cand in path_set:
            return cand
    for ext in _PROBE_EXTENSIONS:
        cand = f"{base}/index{ext}"
        if cand in path_set:
            return cand
    return None


def resolve_via_workspaces(module_path: str, ctx: "ResolverContext") -> str | None:
    """Resolve a bare specifier (``@scope/pkg`` or ``@scope/pkg/sub/file``)
    against the workspace map. Honours each workspace's ``exports``
    subpath map (Node.js spec) before falling back to a ``<pkg>/<subpath>``
    probe so plain monorepo layouts without ``exports`` keep working.
    Returns a repo-relative path or None.
    """
    info = get_or_build_workspace_info(ctx)
    if not info:
        return None

    # Match the longest package-name prefix. ``@scope/pkg/sub/x`` should bind
    # ``@scope/pkg`` and resolve ``sub/x`` under that workspace's dir.
    best_name: str | None = None
    for name in info:
        if module_path == name or module_path.startswith(name + "/"):
            if best_name is None or len(name) > len(best_name):
                best_name = name
    if best_name is None:
        return None

    pkg = info[best_name]
    dir_posix: str = pkg["dir"]
    exports_map: dict[str, str] = pkg["exports"]
    sub = module_path[len(best_name) :].lstrip("/")

    # 1) ``exports`` field — the package's authoritative subpath map.
    if exports_map:
        target = _match_export_key(sub, exports_map)
        if target is not None:
            # Targets are package-relative ("./src/lib/foo.ts"). Strip the
            # leading "./" and join with the package dir to get a repo path.
            stripped = target.lstrip("./")
            resolved = _probe_path(f"{dir_posix}/{stripped}", ctx.path_set)
            if resolved is not None:
                return resolved

    # 2) Bare-package fallback — no ``exports[.]`` entry: try index.*,
    #    then ``main``/``module`` from package.json.
    if not sub:
        cand = _probe_path(f"{dir_posix}/index", ctx.path_set)
        if cand is not None:
            return cand
        main = pkg.get("main")
        if isinstance(main, str):
            cand = _probe_path(f"{dir_posix}/{main.lstrip('./')}", ctx.path_set)
            if cand is not None:
                return cand
        return None

    # 3) Subpath fallback — packages without ``exports`` (plain monorepo
    #    layouts): try ``<pkg>/<sub>`` directly, then under common source
    #    roots (``src``, ``lib``, ``dist``) so the resolver still finds
    #    files in packages that publish from a build directory.
    direct = _probe_path(f"{dir_posix}/{sub}", ctx.path_set)
    if direct is not None:
        return direct
    for src_root in ("src", "lib", "dist"):
        cand = _probe_path(f"{dir_posix}/{src_root}/{sub}", ctx.path_set)
        if cand is not None:
            return cand
    return None


# ---------------------------------------------------------------------------
# TsWorkspaceIndex — aggregated workspace view consumed by the dead-code
# analyzer (exports-wildcard entry points), the resolver, and any future
# pass that needs to know "what does the workspace publish?".
# ---------------------------------------------------------------------------


@dataclass
class TsWorkspaceIndex:
    """Aggregated TypeScript/JavaScript workspace metadata.

    ``packages`` mirrors :func:`build_workspace_info` — kept here so a
    single object carries every workspace fact the rest of the pipeline
    needs. ``exports_entry_paths`` is the set of repo-relative source
    files that any workspace package's ``package.json`` ``exports`` map
    resolves to (including wildcards expanded against ``ctx.path_set``).
    These files are *the* public surface of the monorepo — flagging them
    as unreachable just because nothing inside the repo imports them is
    a false positive, since downstream consumers reach them via the
    package boundary the analyzer can't observe.
    """

    packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    exports_entry_paths: set[str] = field(default_factory=set)


def _expand_exports_wildcard(
    target: str, exports_pattern: str, pkg_dir: str, path_set: set[str]
) -> set[str]:
    """Return every concrete source file matching a wildcard exports target.

    ``exports_pattern`` is the key like ``"./locales/*"``; ``target`` is
    the right-hand side like ``"./src/locales/*.ts"``. The function
    interprets ``*`` as ``[^/]*`` (single path segment, Node spec) and
    enumerates ``path_set`` entries under ``pkg_dir`` that match the
    expanded prefix/suffix.
    """
    if "*" not in target:
        # Non-wildcard target — just probe the concrete path.
        stripped = target.lstrip("./")
        resolved = _probe_path(f"{pkg_dir}/{stripped}", path_set)
        return {resolved} if resolved is not None else set()
    # Decompose ``./src/locales/*.ts`` into prefix=``src/locales/`` and
    # suffix=``.ts``. The Node spec only allows one ``*`` per pattern.
    stripped = target.lstrip("./")
    prefix, _, suffix = stripped.partition("*")
    base_prefix = f"{pkg_dir}/{prefix}"
    matches: set[str] = set()
    for candidate in path_set:
        if not candidate.startswith(base_prefix):
            continue
        if suffix and not candidate.endswith(suffix):
            continue
        # Reject paths whose captured segment crosses a directory boundary
        # unless the pattern itself spans dirs (``**`` is not part of the
        # spec; ``*`` matches a single segment).
        captured = candidate[len(base_prefix) : len(candidate) - len(suffix) if suffix else len(candidate)]
        if "/" in captured and exports_pattern.endswith("/*"):
            continue
        matches.add(candidate)
    return matches


def build_ts_workspace_index(ctx: "ResolverContext") -> TsWorkspaceIndex:
    """Build the workspace index for *ctx*.

    Idempotent — safe to call multiple times. Reads the workspace
    metadata via :func:`build_workspace_info` and resolves every
    ``exports`` target (concrete and wildcard) against ``ctx.path_set``
    so a file's reachability through the package boundary is observable
    to downstream passes.
    """
    packages = get_or_build_workspace_info(ctx)
    entries: set[str] = set()
    path_set = ctx.path_set
    for _name, pkg in packages.items():
        dir_posix: str = pkg["dir"]
        exports_map: dict[str, str] = pkg.get("exports") or {}
        for pattern, target in exports_map.items():
            entries.update(_expand_exports_wildcard(target, pattern, dir_posix, path_set))
        # ``main``/``module`` shorthand — package's primary entry.
        main = pkg.get("main")
        if isinstance(main, str):
            resolved = _probe_path(f"{dir_posix}/{main.lstrip('./')}", path_set)
            if resolved is not None:
                entries.add(resolved)
    return TsWorkspaceIndex(packages=packages, exports_entry_paths=entries)


def get_or_build_ts_index(ctx: "ResolverContext") -> TsWorkspaceIndex:
    """Memoized accessor — builds the index once per resolver context."""
    cached = getattr(ctx, "_ts_workspace_index", None)
    if cached is not None:
        return cached
    index = build_ts_workspace_index(ctx)
    ctx._ts_workspace_index = index  # type: ignore[attr-defined]
    return index


# ---------------------------------------------------------------------------
# MDX import scan + vitest config scan — entry-point sources the static
# graph never observes through the TS/JS parser path.
# ---------------------------------------------------------------------------

import re as _re

_MDX_IMPORT_RE = _re.compile(
    r"""import\s+
        (?:type\s+)?
        (?:\{[^}]*\}|\*\s+as\s+\w+|\w+(?:\s*,\s*\{[^}]*\})?)
        \s+from\s+['"]([^'"]+)['"]""",
    _re.VERBOSE,
)

_VITEST_INCLUDE_RE = _re.compile(
    r"""include\s*:\s*\[\s*((?:['"][^'"]+['"]\s*,?\s*)+)\]""",
    _re.MULTILINE,
)
_VITEST_STRING_RE = _re.compile(r"""['"]([^'"]+)['"]""")


def find_mdx_import_targets(ctx: "ResolverContext") -> set[str]:
    """Return repo-relative paths reached only via ``import`` in MDX/MD files.

    React-component libraries published as documentation (``.mdx`` files
    that embed live TSX components) hide their consumers from the static
    TS parser because nothing in this repo parses MDX. The regex below
    is intentionally narrow: ``import … from '...'``. Anything fancier
    (JSX-in-MDX components, MDX-specific shorthand) falls out and gets
    treated as no-edge — better than a parser dependency.
    """
    if ctx.repo_path is None:
        return set()
    from .typescript import resolve_ts_js_import

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
            # Pre-normalise relative specifiers so ``../foo`` resolves
            # against ``ctx.path_set`` — the underlying TS resolver
            # leaves ``..`` segments unflattened and relies on the
            # parser layer to do this, which doesn't run for MDX.
            if spec.startswith("."):
                import os as _os
                joined = _os.path.normpath(_os.path.join(_os.path.dirname(rel), spec))
                joined = joined.replace("\\", "/")
                # Re-express as a relative spec rooted at the repo so
                # ``resolve_ts_js_import`` treats it as relative.
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


def _vitest_glob_to_regex(glob: str) -> _re.Pattern[str]:
    """Translate a vitest/minimatch glob into a regex matching repo paths.

    ``**`` matches zero-or-more path segments (including empty); a single
    ``*`` matches one path segment (no ``/``); ``?`` matches a single
    non-``/`` char. Anything else is escaped. Python's ``fnmatch`` treats
    ``*`` as "any chars including ``/``", which collapses ``foo/**/x``
    into a too-strict regex that misses ``foo/x`` — hence this
    bespoke translator.
    """
    out: list[str] = ["^"]
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if i + 1 < len(glob) and glob[i + 1] == "*":
                # ``**`` — zero or more path segments. Consume optional
                # trailing ``/`` so ``foo/**/bar`` matches ``foo/bar``.
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
    return _re.compile("".join(out))


def find_vitest_include_targets(ctx: "ResolverContext") -> set[str]:
    """Return repo-relative source files matching vitest ``include`` globs.

    Belt-and-suspenders alongside the ``*.test.*`` never-flag pattern —
    catches custom test layouts like ``runtime-tests/**`` that escape
    the filename convention.
    """
    if ctx.repo_path is None:
        return set()

    targets: set[str] = set()
    for cfg in _get_repo_scan(ctx).vitest_configs:
        try:
            text = cfg.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        cfg_dir = cfg.parent
        for inc_match in _VITEST_INCLUDE_RE.finditer(text):
            for str_match in _VITEST_STRING_RE.finditer(inc_match.group(1)):
                glob_pat = str_match.group(1)
                # Resolve glob relative to the config file's directory.
                base = (cfg_dir / glob_pat).as_posix()
                try:
                    rel_glob = Path(base).relative_to(ctx.repo_path).as_posix()
                except ValueError:
                    continue
                regex = _vitest_glob_to_regex(rel_glob)
                for candidate in ctx.path_set:
                    if regex.match(candidate):
                        targets.add(candidate)
    return targets


# ---------------------------------------------------------------------------
# npm-script entry detection
# ---------------------------------------------------------------------------

# Source-file extensions a script might point at directly. Includes the
# ``.mts``/``.cts`` family because hono/zod benchmarks favour them.
_NPM_SCRIPT_SOURCE_EXTS: tuple[str, ...] = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
)

# Runner tokens that take a single source path as their first non-flag
# positional. Detection is positional rather than name-based so we don't
# have to track shell-syntax quirks (``--`` separators, env-var prefixes,
# multi-command ``&&`` chains).
_NPM_SCRIPT_RUNNERS: frozenset[str] = frozenset({
    "tsx", "ts-node", "ts-node-esm", "vite-node", "swc-node",
    "node", "deno", "bun",
    "esbuild", "rollup", "vite", "webpack",
})

# Sub-package names that conventionally hold ad-hoc / experimental
# scripts — bench harnesses, tree-shaking experiments, examples, demos.
# Their source files are typically passed as CLI arguments at runtime
# (``rollup -c --input X.ts``, ``tsx index.ts <file>``) rather than
# imported by anything in the static graph. Treating them as entry
# points matches how a human reads the repo: maintained code, not dead.
_EXPERIMENT_DIR_NAMES: frozenset[str] = frozenset({
    "bench", "benches", "benchmark", "benchmarks",
    "treeshake", "treeshaking",
    "example", "examples",
    "demo", "demos",
    "sample", "samples",
    "playground", "playgrounds",
    "scratch",
    # ``scripts/`` is conventionally ad-hoc tooling invoked via npm
    # commands or pre/post-commit hooks — never imported by application
    # code, but always maintained. Treat the whole dir as live.
    "scripts",
})


def _iter_script_tokens(script: str) -> list[str]:
    """Split a script command on whitespace and shell separators.

    Strips quotes off individual tokens but does NOT honour shell quoting
    (``"a b"`` becomes two tokens) — matches Node's npm script semantics
    where commands are passed to ``sh -c`` and we only care about token
    *shape*, not faithful argv reconstruction.
    """
    # Replace shell chain operators with spaces so each chunk parses.
    cleaned = _re.sub(r"&&|\|\||;|\|", " ", script)
    return [tok.strip("'\"") for tok in cleaned.split() if tok.strip("'\"")]


def find_npm_script_entry_targets(ctx: "ResolverContext") -> set[str]:
    """Return repo-relative source files referenced by ``package.json`` scripts.

    Hono's ``benchmarks/{jsx,routers,query-param}/**`` and zod's
    ``packages/{bench,treeshake,tsc}/**`` are invoked as ``tsx <path>`` /
    ``bun run <path>`` / ``rollup -c <path>`` from their package's
    ``scripts.*`` — never imported by the main entry graph, so they read
    as ``in_degree==0`` despite being live, maintained code. This scan
    surfaces those paths as entry points.

    Also picks up quoted glob arguments (prettier / eslint / format-check
    style ``"src/**/*.ts"``) so files only ever consumed by build tooling
    aren't flagged dead — they're maintained code, just not application
    code.
    """
    if ctx.repo_path is None:
        return set()

    # NOTE: not .resolve()d — the shared scan walks ctx.repo_path as given,
    # so relative_to() below must use the same base or it silently drops
    # every manifest.
    repo_root = ctx.repo_path
    path_set = ctx.path_set
    targets: set[str] = set()

    # Build a quick "directory → files under it" index lazily by scanning
    # ``path_set`` once. Cheap: a few thousand strings at most.
    dirs_in_repo: dict[str, list[str]] = {}
    for p in path_set:
        idx = 0
        while True:
            slash = p.find("/", idx)
            if slash == -1:
                break
            dirs_in_repo.setdefault(p[:slash], []).append(p)
            idx = slash + 1

    for pkg_file in _get_repo_scan(ctx).package_jsons:
        # node_modules manifests are already pruned by the shared scan.
        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        scripts = data.get("scripts") or {}
        if not isinstance(scripts, dict):
            continue
        pkg_dir = pkg_file.parent
        try:
            pkg_rel = pkg_dir.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        pkg_prefix = "" if pkg_rel in ("", ".") else f"{pkg_rel}/"

        # Experimental sub-package convention: ``packages/bench``,
        # ``packages/treeshake``, ``examples/`` style directories invoke
        # their source files via runtime-supplied CLI arguments
        # (``rollup --input``, ``import.meta.resolve``) that no static
        # scan can resolve. The presence of an ``experimental`` directory
        # name + a ``package.json`` is the human-meaningful "this is a
        # maintained script bag" signal — mark every source file under
        # it as an entry. Honour ``"private": true`` *or* the directory
        # name match; the conventional names are the load-bearing signal.
        pkg_dir_name = pkg_dir.name.lower()
        if pkg_dir_name in _EXPERIMENT_DIR_NAMES:
            dir_files = dirs_in_repo.get(pkg_rel) if pkg_rel else None
            if dir_files:
                for f in dir_files:
                    if any(f.lower().endswith(ext) for ext in _NPM_SCRIPT_SOURCE_EXTS):
                        targets.add(f)

        for command in scripts.values():
            if not isinstance(command, str):
                continue
            tokens = _iter_script_tokens(command)
            for token in tokens:
                if not token or token.startswith("-"):
                    continue
                # Source file with a known extension — try resolving as a
                # path relative to the package directory. Glob meta-chars
                # are handled by the next branch.
                lower = token.lower()
                if (
                    any(lower.endswith(ext) for ext in _NPM_SCRIPT_SOURCE_EXTS)
                    and "*" not in token
                    and "?" not in token
                ):
                    candidate = (pkg_prefix + token).lstrip("./")
                    candidate = _re.sub(r"\\", "/", candidate)
                    # Normalise ``a/./b`` and ``a/../b`` segments.
                    parts: list[str] = []
                    for seg in candidate.split("/"):
                        if seg in ("", "."):
                            continue
                        if seg == "..":
                            if parts:
                                parts.pop()
                            continue
                        parts.append(seg)
                    norm = "/".join(parts)
                    if norm in path_set:
                        targets.add(norm)
                    continue
                # Glob argument (prettier / eslint / format scope) — only
                # expand when the token has a glob meta-character and
                # contains a slash, so plain runner names like ``tsc`` and
                # plain flags don't get misinterpreted.
                if ("*" in token or "?" in token) and "/" in token:
                    rel_glob = (pkg_prefix + token).lstrip("./")
                    # Strip stray leading ``./`` left over from npm conv.
                    if rel_glob.startswith("./"):
                        rel_glob = rel_glob[2:]
                    try:
                        regex = _vitest_glob_to_regex(rel_glob)
                    except _re.error:
                        continue
                    for candidate in path_set:
                        if regex.match(candidate):
                            targets.add(candidate)
                    continue
                # Bare directory token (``eslint src runtime-tests build``)
                # — mark every source file inside as live. Constrained to
                # directories that actually exist in ``path_set`` to avoid
                # treating arbitrary identifiers (``run``, ``--``) as dirs.
                if "/" not in token and token in (
                    "src", "lib", "app", "test", "tests", "scripts",
                    "benchmarks", "perf-measures", "runtime-tests", "build",
                    "examples",
                ):
                    dir_rel = pkg_prefix + token
                    files = dirs_in_repo.get(dir_rel.rstrip("/"))
                    if files:
                        for f in files:
                            if any(f.lower().endswith(ext) for ext in _NPM_SCRIPT_SOURCE_EXTS):
                                targets.add(f)

    # Catch experimental sub-directories nested inside a package — e.g.
    # ``packages/tsc/bench/*.ts``, ``examples/*/index.ts`` — where the
    # parent ``package.json`` doesn't itself match the experimental
    # convention but a descendant directory does. We only scan dirs that
    # already appear in ``dirs_in_repo`` (i.e., that hold source files),
    # so the pass is bounded by what's in ``path_set``.
    for dir_rel, files in dirs_in_repo.items():
        last_seg = dir_rel.rsplit("/", 1)[-1].lower()
        if last_seg not in _EXPERIMENT_DIR_NAMES:
            continue
        for f in files:
            if any(f.lower().endswith(ext) for ext in _NPM_SCRIPT_SOURCE_EXTS):
                targets.add(f)
    return targets
