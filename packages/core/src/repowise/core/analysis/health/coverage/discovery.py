"""Coverage artifact discovery + report-path resolution.

Two jobs that make ingested coverage *actually line up* with the repo:

1. **Discovery** — find coverage report files on disk. The file traverser
   blocks ``coverage/``, ``htmlcov/``, ``target/`` and friends, so report
   artifacts never appear in the indexed file set; we glob the filesystem
   directly with a curated, bounded set of patterns.

2. **Resolution** — reconcile the file paths *inside* a report (lcov ``SF:``
   records, Cobertura ``filename`` attrs, ...) to repowise's canonical file
   key, which is **repo-relative, forward-slash POSIX** (set in
   ``ingestion/traverser.py`` via ``abs_path.relative_to(repo_root).as_posix()``).

   Almost no coverage tool emits that key: lcov / nyc / c8 / cargo-llvm-cov
   write absolute paths; Cobertura writes paths relative to its own
   ``<source>`` root. Mapping them back is the most common reason coverage
   appears to "silently show 0%". We resolve by **longest trailing-segment
   overlap** against the indexed tree, so no per-report path-rewrite config
   is needed, and we report unmatched files **loudly** rather than rendering
   a silent 0%.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path

import pathspec

from repowise.core.fs_walk import PRUNED_DIRS, WalkSnapshot, iter_glob

from .detector import parse as parse_coverage
from .model import (
    ContextCoverageReport,
    CoverageReport,
    FileCoverage,
    TestCoverage,
    coverage_map_entry,
)

# Default glob patterns, relative to the repo root. Ordered roughly by how
# canonical/common the location is. Kept curated (not a blind ``**/*.info``)
# so discovery stays fast and doesn't wander into ``node_modules``.
DEFAULT_DISCOVERY_GLOBS: tuple[str, ...] = (
    "coverage/lcov.info",
    "coverage/**/lcov.info",
    "lcov.info",
    "coverage.lcov",
    "coverage/cobertura.xml",
    "coverage/cobertura-coverage.xml",
    "**/cobertura.xml",
    "**/cobertura-coverage.xml",
    "coverage.xml",
    "coverage/coverage.xml",
    "coverage/clover.xml",
    "**/clover.xml",
    "target/llvm-cov/**/*.lcov",
    "target/nextest/**/*.xml",
    "coverage.out",
    "cover.out",
    "target/site/jacoco/jacoco.xml",
    "**/target/site/jacoco*/jacoco.xml",
    # Root module only: ``build`` is pruned under ``**``, so a nested
    # module's report is passed explicitly.
    "build/reports/jacoco/**/*.xml",
)

# Directories we never descend into when expanding ``**`` patterns — heavy,
# vendored, or irrelevant. The shared junk set plus derived-output names;
# NOT ``coverage``/``target`` (that is where the reports live). Applied at
# traversal time via the shared pruned walk, and post-hoc as a safety net
# for the non-recursive glob paths. A pruned name spelled literally in a
# pattern (``build/reports/...``) is an explicit opt-in and is not pruned.
_PRUNE_DIRS = PRUNED_DIRS | frozenset({"dist", "build"})

# Hard cap on discovered artifacts — a sane upper bound that still covers
# polyglot monorepos with several per-package reports.
_MAX_ARTIFACTS = 50


@dataclass
class CoverageConfig:
    """The ``coverage:`` block of ``.repowise/config.yaml``.

    All fields are optional; the defaults give zero-config auto-discovery.
    """

    auto_discover: bool = True
    # Override the default discovery globs entirely.
    artifacts: tuple[str, ...] = ()
    # Explicit report paths or globs (relative to repo root); bypass discovery.
    paths: tuple[str, ...] = ()
    # Per-report prefix: a ``paths`` entry written as a mapping
    # ``{path: ..., path_prefix: ...}``, keyed by that entry's path or glob.
    # Overrides ``path_prefix`` for every report the entry matches.
    path_prefixes: dict[str, str] = field(default_factory=dict)
    # Gitignore-style globs (repo-relative) for files coverage leaves out:
    # changed files before patch coverage, report entries before storing.
    ignore: tuple[str, ...] = ()
    # Force a parser instead of content-sniffing.
    format: str | None = None
    # Remove this leading prefix from report paths before matching.
    strip_prefix: str | None = None
    # Prepend this prefix to report paths before matching.
    path_prefix: str | None = None
    # Re-discover + re-parse reports on every ``repowise update`` (default:
    # reuse the rows already in the DB; only re-ingest if a report is found).
    reingest_on_update: bool = False
    # Patch-coverage gate for ``repowise coverage check`` (percent, 0-100).
    fail_under: float | None = None
    # Small-change tolerance: a change with fewer changed executable lines
    # than this never fails the gate.
    min_coverable_lines: int | None = None

    @classmethod
    def from_repo_config(cls, repo_config: dict | None) -> CoverageConfig:
        block = (repo_config or {}).get("coverage")
        if not isinstance(block, dict):
            return cls()

        def _strs(val: object) -> tuple[str, ...]:
            if isinstance(val, str):
                return (val,)
            if isinstance(val, (list, tuple)):
                return tuple(str(v) for v in val if v)
            return ()

        paths, path_prefixes = _report_entries(block.get("paths"))
        return cls(
            auto_discover=bool(block.get("auto_discover", True)),
            artifacts=_strs(block.get("artifacts")),
            paths=paths,
            path_prefixes=path_prefixes,
            ignore=_strs(block.get("ignore")),
            format=block.get("format") or None,
            strip_prefix=block.get("strip_prefix") or None,
            path_prefix=block.get("path_prefix") or None,
            reingest_on_update=bool(block.get("reingest_on_update", False)),
            fail_under=_percent(block.get("fail_under")),
            min_coverable_lines=_line_count(block.get("min_coverable_lines")),
        )

    def reports(self, repo_root: Path) -> dict[Path, str | None]:
        """Each report this config names, mapped to its per-report prefix (or ``None``).

        Explicit ``paths`` (globs expanded; a missing file is skipped), else
        discovery when on. Passed to :func:`build_coverage_map` as both the
        report list and ``report_prefixes``.
        """
        if self.paths:
            out: dict[Path, str | None] = {}
            for pattern in self.paths:
                for path in expand_report_patterns([pattern], repo_root):
                    out.setdefault(path, self.path_prefixes.get(pattern))
            return out
        if self.auto_discover:
            return dict.fromkeys(discover_artifacts(repo_root, globs=self.artifacts or None))
        return {}


def configured_ignore(repo_root: Path | str) -> tuple[str, ...]:
    """``coverage.ignore`` from *repo_root*'s config, empty when it cannot be read.

    For the surfaces that read stored coverage (REST, agent tools), so their
    patch coverage leaves out the same files the CLI gate does.
    """
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        return CoverageConfig.from_repo_config(load_repo_config(repo_root)).ignore
    except (RepoConfigError, OSError):
        return ()


def _report_entries(value: object) -> tuple[tuple[str, ...], dict[str, str]]:
    """``coverage.paths``: each entry a path or glob, or ``{path, path_prefix}``."""
    entries = value if isinstance(value, (list, tuple)) else [value]
    paths: list[str] = []
    prefixes: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict):
            path = entry.get("path")
            if not path:
                continue
            paths.append(str(path))
            if entry.get("path_prefix"):
                prefixes[str(path)] = str(entry["path_prefix"])
        elif entry:
            paths.append(str(entry))
    return tuple(paths), prefixes


def _percent(value: object) -> float | None:
    """A 0-100 percentage from config, ``None`` when absent or not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= 100 else None


def _line_count(value: object) -> int | None:
    """A non-negative whole number from config, ``None`` when absent or not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def expand_report_patterns(patterns: Iterable[str], base: Path) -> list[Path]:
    """The report files *patterns* name, relative to *base*.

    A pattern is a literal path or a glob, used by both ``--report`` (relative
    to cwd) and ``coverage.paths`` (relative to the repo root). A path that
    exists as written is taken literally, even when it holds ``[``; anything
    else is expanded like discovery's globs (``**`` through the pruned walk,
    so ``**/lcov.info`` never reaches ``node_modules``). Its leading literal
    directories, absolute ones included, become the walk's root. A glob's
    matches are sorted; files are de-duplicated across patterns, first
    mention first. A pattern that names no file adds nothing.
    """
    snapshots: dict[Path, WalkSnapshot] = {}
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in patterns:
        if (base / pattern).is_file():
            found = [base / pattern]
        else:
            parts = Path(pattern).parts
            first = next((i for i, part in enumerate(parts) if _has_magic(part)), len(parts))
            root = base.joinpath(*parts[:first])
            rest = "/".join(parts[first:])
            found = sorted(_expand_pattern(root, rest, snapshots)) if rest else []
        for path in found:
            key = path.resolve()
            if key not in seen:
                seen.add(key)
                out.append(path)
    return out


@dataclass
class ResolvedCoverage:
    """Outcome of resolving a parsed report against the indexed tree."""

    # Canonical repo key -> engine coverage dict (the shape ``HealthAnalyzer``
    # consumes via ``coverage_map``).
    coverage_map: dict[str, dict] = field(default_factory=dict)
    # ``FileCoverage`` rows with ``file_path`` rewritten to canonical keys,
    # for DB persistence (``save_coverage_files``).
    files: list[FileCoverage] = field(default_factory=list)
    # The first report's format (the single-valued persisted column) and every
    # distinct format merged, in report order.
    source_format: str | None = None
    source_formats: list[str] = field(default_factory=list)
    # Diagnostics — surfaced to the user so "coverage didn't show up" is
    # never silent.
    matched_exact: int = 0
    matched_suffix: int = 0
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    # Report files ``coverage.ignore`` leaves out, resolved or not. Dropped,
    # and counted in neither ``matched`` nor ``unmatched``.
    ignored: int = 0
    # True when more than half the report's files failed to map to the tree.
    # Set by :func:`resolve_reports`; consumers must flag the aggregate as
    # partial rather than reporting the mapped subset's numbers as repo-wide.
    mapping_partial: bool = False

    @property
    def matched(self) -> int:
        return self.matched_exact + self.matched_suffix

    @property
    def total(self) -> int:
        return self.matched + len(self.unmatched) + len(self.ambiguous)

    @property
    def provenance(self) -> CoverageProvenance:
        return CoverageProvenance(
            source_formats=tuple(self.source_formats),
            report_path_count=self.total,
            matched_path_count=self.matched,
            unmatched_path_count=len(self.unmatched),
            ambiguous_path_count=len(self.ambiguous),
            unmatched_sample=tuple((self.unmatched + self.ambiguous)[:UNMATCHED_SAMPLE_CAP]),
            mapping_partial=self.mapping_partial,
        )


#: Unmatched report paths kept for the diagnostic; the counts stay exact.
UNMATCHED_SAMPLE_CAP = 10


@dataclass(frozen=True)
class CoverageProvenance:
    """Where an ingest's coverage came from, stored beside its rows.

    Counts are over the report's file entries. ``None`` means the writer did
    not know them, which is not zero.
    """

    source_formats: tuple[str, ...] = ()
    report_path_count: int | None = None
    matched_path_count: int | None = None
    unmatched_path_count: int | None = None
    ambiguous_path_count: int | None = None
    unmatched_sample: tuple[str, ...] = ()
    mapping_partial: bool = False


def discover_artifacts(
    repo_root: Path,
    *,
    globs: tuple[str, ...] | list[str] | None = None,
) -> list[Path]:
    """Return coverage report files under *repo_root*, de-duplicated.

    Globs the filesystem directly (the report dirs are excluded from the
    indexed file set). Recursive ``**`` patterns are expanded through the
    shared pruned walk — ONE filesystem pass answers all of them, and the
    walk never descends into junk trees or nested git repos (recursive
    ``Path.glob`` did, and was filtered only post-hoc). Literal patterns
    stay direct file checks and single-level wildcards stay plain globs,
    so each pattern keeps its original location scoping (``lcov.info``
    matches at the root only, ``**/clover.xml`` anywhere). Results keep
    pattern-priority order, are de-duplicated by resolved path, and are
    capped at :data:`_MAX_ARTIFACTS`.
    """
    patterns = tuple(globs) if globs else DEFAULT_DISCOVERY_GLOBS
    snapshots: dict[Path, WalkSnapshot] = {}
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in patterns:
        for match in _expand_pattern(repo_root, pattern, snapshots):
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(match)
            if len(out) >= _MAX_ARTIFACTS:
                return out
    return out


def _has_magic(text: str) -> bool:
    return any(ch in text for ch in "*?[")


def _expand_pattern(
    root: Path, pattern: str, snapshots: dict[Path, WalkSnapshot]
) -> Iterator[Path]:
    """Files under *root* matching the POSIX *pattern*, through the pruned walk.

    The one expansion behind discovery and ``expand_report_patterns``. A
    literal pattern is a direct file check; single-level wildcards are a
    bounded plain glob; ``**`` is served from a pruned walk of *root*
    (*snapshots* caches one per root), so it never enters dependency, cache
    or build trees, nested repositories, or symlinks. A pruned name spelled
    literally before the first wildcard is an explicit opt-in.
    """
    if not _has_magic(pattern):
        direct = root / pattern
        if direct.is_file():
            yield direct
        return
    if "**" not in pattern:
        # Single-level wildcards cannot recurse; a plain glob is bounded.
        matches: Iterable[Path] = root.glob(pattern)
    else:
        # Recursive pattern: split at the first ``**`` into a fixed (or
        # shallow-globbed) root and a tail served from the shared snapshot.
        prefix, _, tail = pattern.partition("**")
        prefix = prefix.rstrip("/")
        tail = tail.lstrip("/") or "*"
        if _has_magic(prefix):
            roots = [d for d in root.glob(prefix) if d.is_dir()]
        else:
            roots = [root / prefix]
        if not _has_magic(prefix) and any(part in _PRUNE_DIRS for part in Path(prefix).parts):
            # The snapshot never enters a pruned dir, so a root spelled under
            # one is walked live (still pruning below it).
            matches = iter_glob(roots[0], tail, prune_dirs=_PRUNE_DIRS)
        else:
            if root not in snapshots:
                snapshots[root] = WalkSnapshot(root, prune_dirs=_PRUNE_DIRS)
            snap = snapshots[root]
            matches = chain.from_iterable(snap.iter_glob(r, tail) for r in roots)
    literal = 0
    for seg in pattern.split("/")[:-1]:
        if _has_magic(seg):
            break
        literal += 1
    for match in matches:
        if not match.is_file():
            continue
        try:
            rel_parts = match.relative_to(root).parts
        except ValueError:
            rel_parts = match.parts
        # Safety net for the plain-glob paths, which do not prune.
        if not any(part in _PRUNE_DIRS for part in rel_parts[literal:-1]):
            yield match


def normalize_report_path(
    raw: str,
    *,
    strip_prefix: str | None = None,
    path_prefix: str | None = None,
) -> str:
    """Normalize a report file path toward the canonical key shape.

    POSIX separators, no leading ``./`` or ``/``, optional drive letter
    dropped, then an optional configured *strip_prefix* removed and
    *path_prefix* prepended. The result is still not guaranteed to be a
    real repo key — that is what :func:`_match_key` is for — but it is the
    best deterministic starting point.
    """
    p = raw.strip().replace("\\", "/")
    # Drop a Windows drive letter (``C:/...``).
    if len(p) >= 2 and p[1] == ":":
        p = p[2:]
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if strip_prefix:
        sp = strip_prefix.replace("\\", "/").strip("/")
        if sp and p.startswith(sp + "/"):
            p = p[len(sp) + 1 :]
    if path_prefix:
        pp = path_prefix.replace("\\", "/").strip("/")
        if pp:
            p = f"{pp}/{p}"
    return p


def _build_suffix_index(repo_keys: set[str]) -> dict[str, list[str]]:
    """Map each basename -> repo keys ending in that basename.

    Basename is the cheap first filter; trailing-segment overlap then
    disambiguates among same-named files (``mod.rs`` is everywhere in Rust).
    """
    index: dict[str, list[str]] = defaultdict(list)
    for key in repo_keys:
        base = key.rsplit("/", 1)[-1]
        index[base].append(key)
    return index


def _match_key(
    norm_path: str,
    repo_keys: set[str],
    suffix_index: dict[str, list[str]],
    *,
    prefer_dir: str | None = None,
) -> tuple[str | None, bool]:
    """Resolve a normalized report path to a canonical key.

    Returns ``(key, ambiguous)``. ``key`` is None when nothing matched;
    ``ambiguous`` is True when several repo files tie on the longest
    trailing-segment overlap and *prefer_dir* (the report's own directory)
    does not single one out: we refuse to guess.

    A match must agree on more than the basename when the report names a
    directory: ``other/pkg/utils.py`` is not ``src/utils.py``. It is accepted
    when at least one directory also matches, or when the whole repo key is
    the tail of the report path (a root-level file under an absolute path).
    A path that runs through a dependency or environment directory
    (``node_modules``, ``site-packages``) is someone else's file and never
    matches by suffix.
    """
    if norm_path in repo_keys:
        return norm_path, False

    base = norm_path.rsplit("/", 1)[-1]
    candidates = suffix_index.get(base)
    if not candidates:
        return None, False

    report_segs = norm_path.split("/")
    best_overlap = 0
    winners: list[str] = []
    for cand in candidates:
        overlap = _agreed_overlap(report_segs, cand.split("/"))
        if overlap is None:
            continue
        if overlap > best_overlap:
            best_overlap = overlap
            winners = [cand]
        elif overlap == best_overlap:
            winners.append(cand)

    if len(winners) > 1 and prefer_dir:
        winners = _nearest(winners, prefer_dir)
    if len(winners) == 1:
        return winners[0], False
    return None, bool(winners)


#: Directories whose files belong to a dependency or environment, not the repo.
_VENDORED_DIRS = PRUNED_DIRS | frozenset({"site-packages", "dist-packages", "vendor"})


def _common_run(a: Iterable[str], b: Iterable[str]) -> int:
    """How many leading items *a* and *b* share."""
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _agreed_overlap(report_segs: list[str], cand_segs: list[str]) -> int | None:
    """Trailing segments shared, or ``None`` when the match rests on too little.

    See :func:`_match_key` for what counts as agreement.
    """
    overlap = _common_run(reversed(report_segs), reversed(cand_segs))
    if not (overlap >= 2 or overlap == len(cand_segs) or len(report_segs) == 1):
        return None
    if _VENDORED_DIRS.intersection(report_segs[: len(report_segs) - overlap]):
        return None
    return overlap


def _nearest(keys: list[str], directory: str) -> list[str]:
    """The keys sharing the longest leading directory run with *directory*."""
    dir_segs = directory.split("/")

    def shared(key: str) -> int:
        return _common_run(key.split("/")[:-1], dir_segs)

    best = max(shared(k) for k in keys)
    return [k for k in keys if shared(k) == best] if best else keys


def _is_absolute(raw: str) -> bool:
    p = raw.strip().replace("\\", "/")
    return p.startswith("/") or (len(p) >= 2 and p[1] == ":")


def _resolve_path(
    raw: str,
    report: CoverageReport,
    repo_keys: set[str],
    suffix_index: dict[str, list[str]],
    *,
    strip_prefix: str | None,
    path_prefix: str | None,
    go_modules: Sequence[tuple[str, str]] = (),
) -> tuple[str | None, bool, bool]:
    """``(key, ambiguous, exact)`` for one report path.

    Tried in order: the path joined to each Cobertura ``<source>`` root, the
    report's own statement of where its paths live (roots that disagree are
    ambiguous); then a relative path under the report's own directory and its
    parents, nearest first, since runners write paths relative to the package
    they ran in (a monorepo's ``packages/web/coverage/lcov.info`` naming
    ``src/index.ts``); then the path as written. Ties prefer the report's own
    directory. A configured *path_prefix* already says where the paths live,
    so it skips the report-directory step.

    Without a *path_prefix*, a path under a Go module's import path
    (*go_modules*, ``(module_path, module_dir)`` longest first) is first
    rewritten into that module's directory, which says where it lives too.
    """
    located = bool(path_prefix)
    if not path_prefix:
        in_module = _in_go_module(raw, go_modules)
        if in_module is not None:
            raw, located = in_module, True
    norm = normalize_report_path(raw, strip_prefix=strip_prefix, path_prefix=path_prefix)
    origin = report.origin_dir
    relative = not _is_absolute(raw)
    if relative and report.source_roots:
        found: dict[str, bool] = {}  # key -> matched a path exactly
        for root in report.source_roots:
            joined = normalize_report_path(
                f"{root.rstrip('/')}/{raw}", strip_prefix=strip_prefix, path_prefix=path_prefix
            )
            key, _ = _match_key(joined, repo_keys, suffix_index, prefer_dir=origin)
            if key is not None:
                found[key] = found.get(key, False) or key in (joined, norm)
        if found:
            return _one_root(found)
    if origin and relative and not located:
        key = _under_origin(norm, origin, repo_keys)
        if key is not None:
            return key, False, True
    key, ambiguous = _match_key(norm, repo_keys, suffix_index, prefer_dir=origin)
    return key, ambiguous, key is not None and norm == key


def _one_root(found: dict[str, bool]) -> tuple[str | None, bool, bool]:
    """The source-root step's answer: one key, or ambiguous when roots disagree."""
    if len(found) > 1:
        return None, True, False
    key, exact = next(iter(found.items()))
    return key, False, exact


def _in_go_module(raw: str, go_modules: Sequence[tuple[str, str]]) -> str | None:
    """*raw* rewritten from a Go import path to its repo path, ``None`` outside every module."""
    for module_path, module_dir in go_modules:
        if raw.startswith(module_path + "/"):
            rest = raw[len(module_path) + 1 :]
            return f"{module_dir}/{rest}" if module_dir else rest
    return None


#: Directories whose ``go.mod`` is not a module of this repository's own code.
_GO_MOD_SKIP_DIRS = frozenset({"vendor", "testdata"})


def _go_modules(repo_root: Path) -> list[tuple[str, str]]:
    """``(module_path, module_dir)`` for each ``go.mod`` on disk, longest module path first.

    Go coverprofiles name files by import path (``example.com/m/main.go``), so
    a module in a subdirectory only matches once its path is mapped back. Read
    from disk through the pruned walk, not from the index: the traverser
    never keeps ``go.mod`` as a file.
    """
    modules: dict[str, str] = {}
    found = []
    for go_mod in iter_glob(repo_root, "go.mod"):
        rel = go_mod.relative_to(repo_root)
        if go_mod.is_file() and not _GO_MOD_SKIP_DIRS.intersection(rel.parts):
            found.append(rel)
    # Shallowest first, then by path: when two go.mod files declare the same
    # module path (a copied fixture, an example), the one nearest the root wins.
    for rel in sorted(found, key=lambda p: (len(p.parts), p.as_posix())):
        try:
            text = (repo_root / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        module_path = _module_directive(text)
        if module_path and module_path not in modules:
            module_dir = rel.parent.as_posix()
            modules[module_path] = "" if module_dir == "." else module_dir
    return sorted(modules.items(), key=lambda m: (-len(m[0]), m[0]))


def _module_directive(go_mod: str) -> str | None:
    # A local reader rather than the Go import resolver's: importing that
    # pulls the whole ingestion package into the index-free coverage gate.
    for line in go_mod.splitlines():
        parts = line.split("//", 1)[0].split(None, 1)
        if len(parts) == 2 and parts[0] == "module":
            return parts[1].strip().strip("\"`") or None
    return None


def _under_origin(norm: str, origin: str, repo_keys: set[str]) -> str | None:
    """*norm* under the report's directory or its nearest parent that has it."""
    segs = origin.split("/")
    for depth in range(len(segs), 0, -1):
        candidate = "/".join([*segs[:depth], norm])
        if candidate in repo_keys:
            return candidate
    return None


def _merge_into(dst: FileCoverage, src: FileCoverage) -> None:
    """Hit-wins merge of *src* into *dst* (same canonical key).

    A line covered by any report counts as covered; this enables
    multi-suite / multi-language ingestion with no config.
    """
    covered = set(dst.covered_lines) | set(src.covered_lines)
    coverable = set(dst.coverable_lines) | set(src.coverable_lines)
    total = max(dst.total_coverable_lines, src.total_coverable_lines, len(covered))
    dst.covered_lines = sorted(covered)
    dst.coverable_lines = sorted(coverable)
    dst.total_coverable_lines = total
    dst.covered_line_count = len(covered)
    dst.line_coverage_pct = round(len(covered) / total * 100.0, 2) if total else 0.0
    if src.branch_coverage_pct is not None:
        dst.branch_coverage_pct = (
            src.branch_coverage_pct
            if dst.branch_coverage_pct is None
            else max(dst.branch_coverage_pct, src.branch_coverage_pct)
        )


def resolve_reports(
    reports: list[CoverageReport],
    repo_keys: set[str],
    *,
    strip_prefix: str | None = None,
    path_prefix: str | None = None,
    ignore: Sequence[str] = (),
    go_modules: Sequence[tuple[str, str]] = (),
) -> ResolvedCoverage:
    """Resolve one or more parsed reports against the indexed tree.

    Reports are merged hit-wins by canonical key. Returns the engine
    ``coverage_map``, rewritten ``FileCoverage`` rows for persistence, and
    diagnostics (matched/unmatched/ambiguous) for loud reporting.

    A report's own ``path_prefix`` wins over *path_prefix*. Entries *ignore*
    matches (gitignore syntax), on their resolved key or, when they resolve to
    none, on their normalized report path, are dropped and counted in
    ``ignored``. *go_modules* applies to Go coverprofiles only and is
    described at :func:`_resolve_path`.
    """
    suffix_index = _build_suffix_index(repo_keys)
    ignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore)
    result = ResolvedCoverage()
    by_key: dict[str, FileCoverage] = {}
    report_file_count = 0
    for report in reports:
        prefix = report.path_prefix or path_prefix
        # Only a coverprofile names files by import path; ``module web`` must
        # not rewrite an lcov report's ``web/src/x.ts``.
        modules = go_modules if report.source_format == "go-coverprofile" else ()
        if report.source_format not in (None, "unknown"):
            if result.source_format is None:
                result.source_format = report.source_format
            if report.source_format not in result.source_formats:
                result.source_formats.append(report.source_format)
        for fc in report.files:
            report_file_count += 1
            key, ambiguous, exact = _resolve_path(
                fc.file_path,
                report,
                repo_keys,
                suffix_index,
                strip_prefix=strip_prefix,
                path_prefix=prefix,
                go_modules=modules,
            )
            if key is None:
                norm = normalize_report_path(
                    fc.file_path, strip_prefix=strip_prefix, path_prefix=prefix
                )
                if ignore_spec.match_file(norm):
                    result.ignored += 1
                elif ambiguous:
                    result.ambiguous.append(fc.file_path)
                else:
                    result.unmatched.append(fc.file_path)
                continue
            if ignore_spec.match_file(key):
                result.ignored += 1
                continue
            if exact:
                result.matched_exact += 1
            else:
                result.matched_suffix += 1
            resolved_fc = FileCoverage(
                file_path=key,
                line_coverage_pct=fc.line_coverage_pct,
                branch_coverage_pct=fc.branch_coverage_pct,
                covered_lines=list(fc.covered_lines),
                total_coverable_lines=fc.total_coverable_lines,
                coverable_lines=list(fc.coverable_lines),
                covered_line_count=fc.covered_line_count,
            )
            if key in by_key:
                _merge_into(by_key[key], resolved_fc)
            else:
                by_key[key] = resolved_fc

    # Severe mapping loss is a property of the *report*, not of the matched
    # subset: a 200-file report that mapped 20 files is a fragment no matter
    # how coherent those 20 look. Majority-unmapped is the threshold the
    # coverage path already documents as the "treats loss as success" trap
    # (issue #1746), and it deliberately avoids a hard ratio on the matched
    # side so monorepos ingesting one package's report stay unflagged.
    # Ignored entries were dropped on purpose, so they count on neither side.
    measured_count = report_file_count - result.ignored
    if measured_count:
        result.mapping_partial = result.matched * 2 < measured_count

    for key, fc in by_key.items():
        result.coverage_map[key] = coverage_map_entry(fc, result.source_format)
    result.files = list(by_key.values())
    return result


@dataclass
class ResolvedTestCoverage:
    """Outcome of resolving a :class:`ContextCoverageReport` against the tree.

    Mirrors :class:`ResolvedCoverage` but keeps the test dimension: every
    record's ``file_path`` (and, best-effort, ``test_file``) is rewritten to
    a canonical repo key. ``has_contexts`` propagates the loud-degradation
    signal so a report with no contexts stays visibly empty.
    """

    records: list[TestCoverage] = field(default_factory=list)
    source_format: str | None = None
    has_contexts: bool = False
    matched_exact: int = 0
    matched_suffix: int = 0
    # Report source paths that did not map to an indexed file.
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    # How many records had their test's own file resolved to a repo key.
    test_files_resolved: int = 0
    # Records whose source ``coverage.ignore`` leaves out.
    ignored: int = 0

    @property
    def matched(self) -> int:
        return self.matched_exact + self.matched_suffix


# Extensions that make a test-id prefix look like a real source path (rather
# than a bare suite name), so we only try to resolve path-shaped ids.
_CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb", ".java", ".scala")


def _extract_test_file(test_id: str) -> str | None:
    """Pull a resolvable source path out of a raw test id, or ``None``.

    coverage.py contexts look like ``path/to/test_x.py::Class::test|run``:
    the file lives before ``::`` (and any ``|phase`` suffix). lcov ``TN:``
    names are often bare suite labels with no path, which we skip.
    """
    head = test_id.split("::", 1)[0].split("|", 1)[0].strip()
    if not head:
        return None
    looks_pathy = "/" in head or "\\" in head or head.endswith(_CODE_EXTS)
    return head if looks_pathy else None


def resolve_test_reports(
    report: ContextCoverageReport,
    repo_keys: set[str],
    *,
    strip_prefix: str | None = None,
    path_prefix: str | None = None,
    ignore: Sequence[str] = (),
) -> ResolvedTestCoverage:
    """Resolve per-test records against the indexed tree.

    Reuses the aggregate path resolver (:func:`normalize_report_path` +
    :func:`_match_key`) for *both* the source path and the test's own file,
    so no second resolver is introduced. Records whose source path does not
    map to the tree are dropped and counted in ``unmatched`` / ``ambiguous``;
    records whose source *ignore* matches (as in :func:`resolve_reports`) are
    dropped and counted in ``ignored``.
    """
    suffix_index = _build_suffix_index(repo_keys)
    ignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore)
    out = ResolvedTestCoverage(source_format=report.source_format, has_contexts=report.has_contexts)
    for rec in report.records:
        norm = normalize_report_path(
            rec.file_path, strip_prefix=strip_prefix, path_prefix=path_prefix
        )
        key, ambiguous = _match_key(norm, repo_keys, suffix_index)
        if ignore_spec.match_file(key or norm):
            out.ignored += 1
            continue
        if key is None:
            if ambiguous:
                out.ambiguous.append(rec.file_path)
            else:
                out.unmatched.append(rec.file_path)
            continue
        if norm == key:
            out.matched_exact += 1
        else:
            out.matched_suffix += 1

        test_key: str | None = None
        head = _extract_test_file(rec.test_id)
        if head:
            tnorm = normalize_report_path(head, strip_prefix=strip_prefix, path_prefix=path_prefix)
            tkey, _ = _match_key(tnorm, repo_keys, suffix_index)
            if tkey is not None:
                test_key = tkey
                out.test_files_resolved += 1

        out.records.append(
            TestCoverage(
                test_id=rec.test_id,
                file_path=key,
                covered_lines=list(rec.covered_lines),
                source_format=rec.source_format,
                test_file=test_key,
            )
        )
    return out


def build_coverage_map(
    repo_root: Path,
    report_paths: list[Path],
    repo_keys: set[str],
    *,
    coverage_format: str | None = None,
    strip_prefix: str | None = None,
    path_prefix: str | None = None,
    report_prefixes: Mapping[Path, str | None] | None = None,
    ignore: Sequence[str] = (),
) -> tuple[ResolvedCoverage, list[tuple[Path, str]]]:
    """Read + parse + resolve coverage reports end-to-end.

    Returns the :class:`ResolvedCoverage` and a list of ``(path, error)``
    for reports that could not be read or parsed (caller decides how loud
    to be). Unreadable/empty reports are skipped, never fatal.

    *report_prefixes* gives a report its own prefix (``--report PATH=PREFIX``,
    a ``coverage.paths`` mapping entry), overriding *path_prefix*. *ignore* is
    ``coverage.ignore``. A Go coverprofile's import paths are mapped back to
    the directories of the ``go.mod`` files on disk.
    """
    prefixes = report_prefixes or {}
    parsed: list[CoverageReport] = []
    errors: list[tuple[Path, str]] = []
    for path in report_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append((path, f"could not read: {exc}"))
            continue
        except UnicodeDecodeError:
            # A binary artifact, most often a coverage.py ``.coverage`` database:
            # export it with ``coverage lcov`` or ``coverage xml`` first.
            errors.append((path, "not a text coverage report"))
            continue
        report = parse_coverage(text, format=coverage_format)
        if not report.files:
            errors.append((path, f"no coverage entries (detected={report.source_format})"))
            continue
        report.origin_dir = _repo_relative_dir(path, repo_root)
        report.path_prefix = prefixes.get(path)
        parsed.append(report)
    resolved = resolve_reports(
        parsed,
        repo_keys,
        strip_prefix=strip_prefix,
        path_prefix=path_prefix,
        ignore=ignore,
        # The walk only when a coverprofile needs it.
        go_modules=(
            _go_modules(repo_root)
            if any(r.source_format == "go-coverprofile" for r in parsed)
            else ()
        ),
    )
    return resolved, errors


def _repo_relative_dir(path: Path, repo_root: Path) -> str | None:
    """*path*'s directory relative to *repo_root* (POSIX), ``None`` outside it or at its root."""
    try:
        rel = path.resolve().parent.relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return rel if rel not in ("", ".") else None
