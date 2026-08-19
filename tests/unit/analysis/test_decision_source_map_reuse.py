"""Inline-marker scan reuses ingestion's ``source_map`` for discovery + reads.

The init pipeline threads the already-computed ``{rel_path: bytes}`` map into
the extractor so it no longer re-walks the tree and re-reads every file. These
tests pin that the source_map path is behaviourally identical to the legacy
self-walk path, and that the update path (``restrict_to_files``) still reads
correctly, preferring in-memory bytes and falling back to disk.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.decision_extractor import DecisionExtractor

# Fixtures use ``\n``-escaped single-line string concatenation on purpose: no
# PHYSICAL line in this committed file begins with a comment marker, so a real
# repowise self-index does not harvest these examples as decisions. Same
# convention as test_decision_rationale_comments.py.
_PY = (
    "# WHY: cache the resolver because rebuilding it per call dominated latency\n"
    "def resolve():\n"
    "    pass\n\n\n"
    "# DECISION: one engine per run keeps the counters honest\n"
    "class Engine:\n"
    "    pass\n"
)

_GO = "// RATIONALE: sync.Pool avoids GC churn under sustained load\npackage util\n"

_MD = (
    "# ADR notes\n\n"
    "Some prose about the system.\n\n"
    "<!-- WHY: an HTML-comment line is not a recognised marker prefix -->\n\n"
    "```\n"
    "# DECISION: this one is inside a code fence and must be ignored\n"
    "```\n\n"
    "# TRADEOFF: accept eventual consistency for higher write throughput\n"
)


def _write_fixture(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "src" / "app.py").write_text(_PY, encoding="utf-8")
    (root / "src" / "util.go").write_text(_GO, encoding="utf-8")
    (root / "docs" / "adr.md").write_text(_MD, encoding="utf-8")


def _source_map(root: Path) -> dict[str, bytes]:
    """Mirror ingestion's map: POSIX rel path -> raw bytes."""
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def _key(d) -> tuple:
    return (
        Path(d.evidence_file).as_posix(),
        d.evidence_line,
        (d.title or "")[:80],
        (d.decision or "")[:120],
    )


async def test_source_map_path_matches_walk_path(tmp_path):
    _write_fixture(tmp_path)

    walk = await DecisionExtractor(repo_path=tmp_path, source_map=None).scan_inline_markers()
    reused = await DecisionExtractor(
        repo_path=tmp_path, source_map=_source_map(tmp_path)
    ).scan_inline_markers()

    assert {_key(d) for d in reused} == {_key(d) for d in walk}
    # Four real markers; the fenced DECISION in adr.md is excluded by both paths.
    assert len(reused) == 4
    assert not any("inside a fence" in (d.decision or "") for d in reused)


async def test_source_map_evidence_paths_are_posix(tmp_path):
    _write_fixture(tmp_path)
    reused = await DecisionExtractor(
        repo_path=tmp_path, source_map=_source_map(tmp_path)
    ).scan_inline_markers()

    files = {d.evidence_file for d in reused}
    # Paths come straight from source_map keys (POSIX), matching graph node ids.
    assert "src/app.py" in files
    assert "docs/adr.md" in files
    assert all("\\" not in f for f in files)


async def test_restrict_prefers_source_map_bytes_then_disk(tmp_path):
    _write_fixture(tmp_path)
    # source_map carries app.py; util.go is intentionally absent so the scan
    # must fall back to a disk read for it.
    sm = {"src/app.py": (tmp_path / "src" / "app.py").read_bytes()}
    ex = DecisionExtractor(repo_path=tmp_path, source_map=sm)

    decisions = await ex.scan_inline_markers(
        restrict_to_files=["src/app.py", "src/util.go"]
    )
    files = {d.evidence_file for d in decisions}
    assert "src/app.py" in files  # from in-memory bytes
    assert "src/util.go" in files  # from disk fallback


async def test_restrict_skips_missing_files(tmp_path):
    _write_fixture(tmp_path)
    ex = DecisionExtractor(repo_path=tmp_path, source_map=None)
    # A deleted/renamed path in the change set must not raise or fabricate.
    decisions = await ex.scan_inline_markers(
        restrict_to_files=["src/app.py", "src/gone.py"]
    )
    assert {d.evidence_file for d in decisions} == {"src/app.py"}
