"""The ``files`` and ``coverage`` datasets: one row per indexed file.

``files`` joins what get_health, get_risk and the coverage report each hold
per file, so "worst files by health", "health against coverage" and "fixes
per package" are one chart. Rows come from the same reads get_health makes
(``HealthFileMetric`` under the health exclude rule), git metadata's own
columns, and the coverage report's per-file rows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from repowise.server.chat_datasets import (
    AVERAGES,
    SUMS,
    DatasetField,
    LoadContext,
    register_dataset,
)

ROOT_PACKAGE = "(root)"
#: A child holding at least this share of a level's files is a wrapper to look past.
_WRAPPER_SHARE = 0.7

_PACKAGE = DatasetField("text", "the package the file belongs to (its nearest manifest root)")
_DIRECTORY = DatasetField(
    "text", "a directory one level inside the package, below any path every file shares"
)
_FILE = DatasetField("text", "the file's shortest unique path suffix, for labels")
_PATH = DatasetField("text", "the file's full repository path")

FILES_FIELDS = {
    "path": _PATH,
    "file": _FILE,
    "package": _PACKAGE,
    "directory": _DIRECTORY,
    "language": DatasetField("text", "the file's language"),
    "health_score": DatasetField(
        "number",
        "maintainability health, 0 (worst) to 10; null when unscored",
        AVERAGES,
        # As nloc_weighted_score averages it, so the repo figure is get_health's.
        weight="nloc",
        weight_floor=1,
        unit="score",
    ),
    "health_band": DatasetField(
        "text", "at risk, needs work, fair, good or excellent; null when unscored"
    ),
    "nloc": DatasetField("number", "lines of code, without blanks and comments", SUMS, unit="lines"),
    "max_ccn": DatasetField("number", "highest cyclomatic complexity of a function in the file", ("max", "avg", "sum")),
    "commits_90d": DatasetField("number", "commits touching the file in the last 90 days", SUMS, unit="commits"),
    "fix_count": DatasetField(
        "number", "bug-fix commits on the file's production code in about 6 months", SUMS, unit="fixes"
    ),
    "coverage_pct": DatasetField(
        "number", "measured line coverage, 0 to 100; null when no report covers it",
        AVERAGES, weight="coverable_lines", unit="%",
    ),
    "coverable_lines": DatasetField("number", "lines the coverage report can measure", SUMS, unit="lines"),
    "is_hotspot": DatasetField("bool", "high churn and complexity, by git history"),
    "is_test": DatasetField("bool", "a test file"),
    "is_production": DatasetField("bool", "in the fix-first scope: not test, tooling, vendored or generated"),
}

COVERAGE_FIELDS = {
    "path": _PATH,
    "file": _FILE,
    "package": _PACKAGE,
    "directory": _DIRECTORY,
    "coverage_pct": DatasetField(
        "number", "measured line coverage, 0 to 100", AVERAGES, weight="coverable_lines", unit="%"
    ),
    "covered_lines": DatasetField("number", "lines the tests ran", SUMS, unit="lines"),
    "coverable_lines": DatasetField("number", "lines the report can measure", SUMS, unit="lines"),
}


# ---------------------------------------------------------------------------
# Path helpers shared by every per-file dataset
# ---------------------------------------------------------------------------


def unique_suffixes(paths: Iterable[str]) -> dict[str, str]:
    """Each path's shortest trailing run of segments no other path ends with."""
    parts = {p: p.split("/") for p in dict.fromkeys(paths)}
    depth = max((len(s) for s in parts.values()), default=0)
    counts = [Counter("/".join(s[-k:]) for s in parts.values()) for k in range(1, depth + 1)]
    out: dict[str, str] = {}
    for path, segments in parts.items():
        for k in range(1, len(segments) + 1):
            label = "/".join(segments[-k:])
            if counts[k - 1][label] == 1 or k == len(segments):
                out[path] = label
                break
    return out


def directories(packages: dict[str, str]) -> dict[str, str]:
    """Each file's directory one level inside its package, past wrapper levels.

    A level where one child holds most of the files (``src/repowise/core``
    holding nearly all of ``packages/core``) tells a chart nothing, so those
    files descend through it; the rest stop at their own child of that level.
    """
    by_package: dict[str, list[str]] = defaultdict(list)
    for path, package in packages.items():
        by_package[package].append(path)
    out: dict[str, str] = {}
    for package, paths in by_package.items():
        root = "" if package == ROOT_PACKAGE else package
        rel = {p: (p[len(root) + 1 :] if root and p.startswith(root + "/") else p) for p in paths}
        _descend(root or package, root, {p: r.split("/")[:-1] for p, r in rel.items()}, out)
    return out


def _descend(package: str, base: str, dirs: dict[str, list[str]], out: dict[str, str]) -> None:
    children = Counter(d[0] for d in dirs.values() if d)
    top, n = children.most_common(1)[0] if children else ("", 0)
    if n and n >= _WRAPPER_SHARE * len(dirs) and n > 1:
        inner = {p: d[1:] for p, d in dirs.items() if d and d[0] == top}
        _descend(package, f"{base}/{top}" if base else top, inner, out)
        dirs = {p: d for p, d in dirs.items() if p not in inner}
    for path, d in dirs.items():
        out[path] = (f"{base}/{d[0]}" if base else d[0]) if d else (base or package)


def place(paths: Iterable[str], files: dict[str, dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """``(package, directory)`` for paths a dataset holds: the files dataset's, else the top level."""
    known = {p: (files[p]["package"], files[p]["directory"]) for p in paths if p in files}
    unknown = {p: _top_level(p) for p in paths if p not in files}
    dirs = directories(unknown)
    return {**known, **{p: (unknown[p], dirs[p]) for p in unknown}}


def _top_level(path: str) -> str:
    head, sep, _ = path.partition("/")
    return head if sep else ROOT_PACKAGE


async def files_index(ctx: LoadContext) -> dict[str, dict[str, Any]]:
    """The files dataset by path, for datasets that place rows by file."""
    return {row["path"]: row for row in await ctx.rows("files")}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


async def _coverage_rows(session: Any, repo_id: str) -> list[Any]:
    from repowise.core.persistence.crud.analysis.plain_rows import plain_rows
    from repowise.core.persistence.models import CoverageFile

    q = select(
        CoverageFile.file_path,
        CoverageFile.line_coverage_pct,
        CoverageFile.total_coverable_lines,
        CoverageFile.covered_line_count,
    ).where(CoverageFile.repository_id == repo_id)
    return plain_rows(await session.execute(q))


@register_dataset(
    "files",
    description="One row per indexed file: health, size, complexity, churn, fixes, coverage.",
    fields=FILES_FIELDS,
    basis=("inferred", "health engine and git history"),
    example={"x": "file", "y": "health_score", "sort": {"field": "health_score", "order": "asc"}, "top": 15},
    hierarchy=("package", "directory", "file"),
    noun="files",
)
async def load_files(ctx: LoadContext) -> list[dict[str, Any]]:
    from repowise.core.analysis.health.grading import band_for
    from repowise.core.analysis.health.ranking import scope_reason
    from repowise.core.persistence.crud.analysis.health import (
        get_file_language_map,
        health_exclude_spec,
    )
    from repowise.core.persistence.crud.analysis.plain_rows import plain_rows
    from repowise.core.persistence.crud.git import code_file_rows
    from repowise.core.persistence.models import GitMetadata, HealthFileMetric
    from repowise.server.mcp_server._helpers import filter_rows_by_attr

    session, repo_id = ctx.session, ctx.repository.id
    metric_q = select(
        HealthFileMetric.file_path,
        HealthFileMetric.score,
        HealthFileMetric.nloc,
        HealthFileMetric.max_ccn,
        HealthFileMetric.module,
        HealthFileMetric.is_test,
        HealthFileMetric.code_origin,
    ).where(HealthFileMetric.repository_id == repo_id)
    # The rule get_health counts files by, so totals here match its numbers.
    spec = await health_exclude_spec(session, repo_id, ctx.repo_path)
    metrics = filter_rows_by_attr(plain_rows(await session.execute(metric_q)), "file_path", spec)

    git_q = select(
        GitMetadata.file_path,
        GitMetadata.commit_count_90d,
        GitMetadata.prior_defect_count,
        GitMetadata.is_hotspot,
    ).where(code_file_rows(repo_id))
    git = {g.file_path: g for g in plain_rows(await session.execute(git_q))}
    coverage = {c.file_path: c for c in await _coverage_rows(session, repo_id)}
    languages = await get_file_language_map(session, repo_id)

    paths = [m.file_path for m in metrics]
    packages = {m.file_path: m.module or ROOT_PACKAGE for m in metrics}
    dirs = directories(packages)
    labels = unique_suffixes(paths)
    rows = []
    for m in metrics:
        g = git.get(m.file_path)
        c = coverage.get(m.file_path)
        # Unrounded, so an average here is exactly the engine's; views round for display.
        score = m.score
        rows.append(
            {
                "path": m.file_path,
                "file": labels[m.file_path],
                "package": packages[m.file_path],
                "directory": dirs[m.file_path],
                "language": languages.get(m.file_path),
                "health_score": score,
                "health_band": band_for(score).replace("_", " ") if score is not None else None,
                "nloc": m.nloc,
                "max_ccn": m.max_ccn,
                "commits_90d": g.commit_count_90d if g else 0,
                "fix_count": g.prior_defect_count if g else 0,
                "coverage_pct": round(c.line_coverage_pct, 2) if c else None,
                "coverable_lines": c.total_coverable_lines if c else None,
                "is_hotspot": bool(g and g.is_hotspot),
                "is_test": bool(m.is_test),
                "is_production": scope_reason(m) is None,
            }
        )
    return rows


@register_dataset(
    "coverage",
    description="One row per file the coverage report measured; avg coverage_pct is line-weighted.",
    fields=COVERAGE_FIELDS,
    basis=("measured", "coverage report"),
    example={"x": "package", "y": "coverage_pct", "aggregate": "avg"},
    hierarchy=("package", "directory", "file"),
    noun="measured files",
)
async def load_coverage(ctx: LoadContext) -> list[dict[str, Any]]:
    report = await _coverage_rows(ctx.session, ctx.repository.id)
    files = await files_index(ctx)
    placed = place([c.file_path for c in report], files)
    labels = unique_suffixes(placed)
    rows = []
    for c in report:
        package, directory = placed[c.file_path]
        covered = c.covered_line_count
        if covered is None:
            covered = round(c.line_coverage_pct / 100 * (c.total_coverable_lines or 0))
        rows.append(
            {
                "path": c.file_path,
                "file": labels[c.file_path],
                "package": package,
                "directory": directory,
                "coverage_pct": round(c.line_coverage_pct, 2),
                "covered_lines": covered,
                "coverable_lines": c.total_coverable_lines,
            }
        )
    return rows
