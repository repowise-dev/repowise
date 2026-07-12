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
from dataclasses import dataclass, field
from pathlib import Path

from .detector import parse as parse_coverage
from .model import ContextCoverageReport, CoverageReport, FileCoverage, TestCoverage

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
)

# Directories we never descend into when expanding ``**`` patterns — heavy,
# vendored, or irrelevant. Keeps discovery bounded on large repos.
_PRUNE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".next",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
    }
)

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
    # Explicit report paths (relative to repo root) — bypass discovery.
    paths: tuple[str, ...] = ()
    # Force a parser instead of content-sniffing.
    format: str | None = None
    # Remove this leading prefix from report paths before matching.
    strip_prefix: str | None = None
    # Prepend this prefix to report paths before matching.
    path_prefix: str | None = None
    # Re-discover + re-parse reports on every ``repowise update`` (default:
    # reuse the rows already in the DB; only re-ingest if a report is found).
    reingest_on_update: bool = False

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

        return cls(
            auto_discover=bool(block.get("auto_discover", True)),
            artifacts=_strs(block.get("artifacts")),
            paths=_strs(block.get("paths")),
            format=block.get("format") or None,
            strip_prefix=block.get("strip_prefix") or None,
            path_prefix=block.get("path_prefix") or None,
            reingest_on_update=bool(block.get("reingest_on_update", False)),
        )


@dataclass
class ResolvedCoverage:
    """Outcome of resolving a parsed report against the indexed tree."""

    # Canonical repo key -> engine coverage dict (the shape ``HealthAnalyzer``
    # consumes via ``coverage_map``).
    coverage_map: dict[str, dict] = field(default_factory=dict)
    # ``FileCoverage`` rows with ``file_path`` rewritten to canonical keys,
    # for DB persistence (``save_coverage_files``).
    files: list[FileCoverage] = field(default_factory=list)
    source_format: str | None = None
    # Diagnostics — surfaced to the user so "coverage didn't show up" is
    # never silent.
    matched_exact: int = 0
    matched_suffix: int = 0
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return self.matched_exact + self.matched_suffix

    @property
    def total(self) -> int:
        return self.matched + len(self.unmatched) + len(self.ambiguous)


def discover_artifacts(
    repo_root: Path,
    *,
    globs: tuple[str, ...] | list[str] | None = None,
) -> list[Path]:
    """Return coverage report files under *repo_root*, de-duplicated.

    Globs the filesystem directly (the report dirs are excluded from the
    indexed file set). Results are pruned of vendored dirs, de-duplicated
    by resolved path, and capped at :data:`_MAX_ARTIFACTS`.
    """
    patterns = tuple(globs) if globs else DEFAULT_DISCOVERY_GLOBS
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in patterns:
        for match in repo_root.glob(pattern):
            if not match.is_file():
                continue
            try:
                rel_parts = match.relative_to(repo_root).parts
            except ValueError:
                rel_parts = match.parts
            if any(part in _PRUNE_DIRS for part in rel_parts[:-1]):
                continue
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(match)
            if len(out) >= _MAX_ARTIFACTS:
                return out
    return out


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
) -> tuple[str | None, bool]:
    """Resolve a normalized report path to a canonical key.

    Returns ``(key, ambiguous)``. ``key`` is None when nothing matched;
    ``ambiguous`` is True when several repo files tie on the longest
    trailing-segment overlap (we refuse to guess).
    """
    if norm_path in repo_keys:
        return norm_path, False

    base = norm_path.rsplit("/", 1)[-1]
    candidates = suffix_index.get(base)
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0], False

    report_segs = norm_path.split("/")
    best_overlap = 0
    winners: list[str] = []
    for cand in candidates:
        cand_segs = cand.split("/")
        overlap = 0
        for a, b in zip(reversed(report_segs), reversed(cand_segs), strict=False):
            if a != b:
                break
            overlap += 1
        if overlap > best_overlap:
            best_overlap = overlap
            winners = [cand]
        elif overlap == best_overlap:
            winners.append(cand)

    if len(winners) == 1:
        return winners[0], False
    return None, True


def _merge_into(dst: FileCoverage, src: FileCoverage) -> None:
    """Hit-wins merge of *src* into *dst* (same canonical key).

    A line covered by any report counts as covered; this enables
    multi-suite / multi-language ingestion with no config.
    """
    covered = set(dst.covered_lines) | set(src.covered_lines)
    total = max(dst.total_coverable_lines, src.total_coverable_lines, len(covered))
    dst.covered_lines = sorted(covered)
    dst.total_coverable_lines = total
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
) -> ResolvedCoverage:
    """Resolve one or more parsed reports against the indexed tree.

    Reports are merged hit-wins by canonical key. Returns the engine
    ``coverage_map``, rewritten ``FileCoverage`` rows for persistence, and
    diagnostics (matched/unmatched/ambiguous) for loud reporting.
    """
    suffix_index = _build_suffix_index(repo_keys)
    result = ResolvedCoverage()
    by_key: dict[str, FileCoverage] = {}

    for report in reports:
        if result.source_format is None and report.source_format not in (None, "unknown"):
            result.source_format = report.source_format
        for fc in report.files:
            norm = normalize_report_path(
                fc.file_path, strip_prefix=strip_prefix, path_prefix=path_prefix
            )
            key, ambiguous = _match_key(norm, repo_keys, suffix_index)
            if key is None:
                if ambiguous:
                    result.ambiguous.append(fc.file_path)
                else:
                    result.unmatched.append(fc.file_path)
                continue
            if norm == key:
                result.matched_exact += 1
            else:
                result.matched_suffix += 1
            resolved_fc = FileCoverage(
                file_path=key,
                line_coverage_pct=fc.line_coverage_pct,
                branch_coverage_pct=fc.branch_coverage_pct,
                covered_lines=list(fc.covered_lines),
                total_coverable_lines=fc.total_coverable_lines,
            )
            if key in by_key:
                _merge_into(by_key[key], resolved_fc)
            else:
                by_key[key] = resolved_fc

    for key, fc in by_key.items():
        result.coverage_map[key] = {
            "line_coverage_pct": fc.line_coverage_pct,
            "branch_coverage_pct": fc.branch_coverage_pct,
            "covered_lines": list(fc.covered_lines),
            "total_coverable_lines": fc.total_coverable_lines,
            "source_format": result.source_format,
        }
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
) -> ResolvedTestCoverage:
    """Resolve per-test records against the indexed tree.

    Reuses the aggregate path resolver (:func:`normalize_report_path` +
    :func:`_match_key`) for *both* the source path and the test's own file,
    so no second resolver is introduced. Records whose source path does not
    map to the tree are dropped and counted in ``unmatched`` / ``ambiguous``.
    """
    suffix_index = _build_suffix_index(repo_keys)
    out = ResolvedTestCoverage(source_format=report.source_format, has_contexts=report.has_contexts)
    for rec in report.records:
        norm = normalize_report_path(
            rec.file_path, strip_prefix=strip_prefix, path_prefix=path_prefix
        )
        key, ambiguous = _match_key(norm, repo_keys, suffix_index)
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
) -> tuple[ResolvedCoverage, list[tuple[Path, str]]]:
    """Read + parse + resolve coverage reports end-to-end.

    Returns the :class:`ResolvedCoverage` and a list of ``(path, error)``
    for reports that could not be read or parsed (caller decides how loud
    to be). Unreadable/empty reports are skipped, never fatal.
    """
    parsed: list[CoverageReport] = []
    errors: list[tuple[Path, str]] = []
    for path in report_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append((path, f"could not read: {exc}"))
            continue
        report = parse_coverage(text, format=coverage_format)
        if not report.files:
            errors.append((path, f"no coverage entries (detected={report.source_format})"))
            continue
        parsed.append(report)
    resolved = resolve_reports(
        parsed, repo_keys, strip_prefix=strip_prefix, path_prefix=path_prefix
    )
    return resolved, errors
