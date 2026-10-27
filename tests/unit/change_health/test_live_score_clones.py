"""``health --score`` on an unedited file equals the score the index stores.

The live pass reads only the scored file, so its clone partners come from the
index's clone state; without that, cross-file duplication went uncounted and
the live score read higher than the stored one.
"""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands.health_cmd import live_score
from repowise.core.analysis.change_health.analyzer import RevisionAnalysis, RevisionHealthAnalyzer
from repowise.core.analysis.change_health.index_inputs import IndexInputs
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.source_reader import MappingSourceReader

from .conftest import python_complex

_SOURCE = "app/a.py"
_FILES = {
    _SOURCE: python_complex("run", 14),
    "app/b.py": python_complex("run", 14),
    "app/c.py": python_complex("other", 3),
}


def _full_pass(repo: Path) -> dict[str, float]:
    """The index's pass over every file, which writes its clone state."""
    sources = {path: (repo / path).read_bytes() for path in _FILES}
    revision = RevisionHealthAnalyzer(repo_root=str(repo))
    parsed, usable = revision._parse(sources, RevisionAnalysis(), None)
    report = HealthAnalyzer(
        revision._graph(parsed, usable),
        parsed_files=parsed,
        repo_root=str(repo),
        source_reader=MappingSourceReader(usable),
        duplication_cache_dir=repo / ".repowise",
    ).analyze()
    assert any(
        f.biomarker_type == "dry_violation" and f.file_path == _SOURCE for f in report.findings
    )
    return {m.file_path: m.score for m in report.metrics}


def test_score_of_an_unedited_file_equals_the_stored_score(make_repo, monkeypatch):
    repo = make_repo()
    repo.commit("seed", _FILES)
    stored = _full_pass(Path(repo.path))
    inputs = IndexInputs(clone_index_dir=Path(repo.path) / ".repowise")
    monkeypatch.setattr(live_score, "_index_inputs", lambda _path: inputs)

    scores = live_score.score_live(Path(repo.path), [_SOURCE], "HEAD")

    row = scores.rows[0]
    assert scores.head.clone_source == "index"
    assert row.head_score == stored[_SOURCE]
    assert row.base_score == stored[_SOURCE]


def test_without_the_index_clone_state_the_payload_says_so(make_repo, monkeypatch):
    repo = make_repo()
    repo.commit("seed", _FILES)
    monkeypatch.setattr(live_score, "_index_inputs", lambda _path: IndexInputs())

    scores = live_score.score_live(Path(repo.path), [_SOURCE], None)

    assert scores.head.clone_source == "analyzed_files"
    assert live_score.live_payload(scores)["clone_source"] == "analyzed_files"


def test_with_duplication_disabled_no_clone_source_is_claimed(make_repo, monkeypatch):
    repo = make_repo()
    repo.commit("seed", _FILES)
    rules = Path(repo.path) / ".repowise" / "health-rules.json"
    rules.parent.mkdir()
    rules.write_text('{"disabled_biomarkers": ["dry_violation"]}', encoding="utf-8")
    monkeypatch.setattr(live_score, "_index_inputs", lambda _path: IndexInputs())

    scores = live_score.score_live(Path(repo.path), [_SOURCE], None)

    assert scores.head.clone_source is None


def test_a_failing_index_splice_is_logged_as_a_warning(make_repo, monkeypatch):
    from structlog.testing import capture_logs

    from repowise.core.analysis.health import engine

    repo = make_repo()
    repo.commit("seed", _FILES)
    _full_pass(Path(repo.path))

    def broken(*_args, **_kwargs):
        raise KeyError("app/b.py")

    monkeypatch.setattr(engine, "clones_against_index", broken)
    inputs = IndexInputs(clone_index_dir=Path(repo.path) / ".repowise")
    monkeypatch.setattr(live_score, "_index_inputs", lambda _path: inputs)

    with capture_logs() as logs:
        scores = live_score.score_live(Path(repo.path), [_SOURCE], None)

    assert scores.head.clone_source == "analyzed_files"
    assert any(
        e["event"] == "health_index_clones_failed" and e["log_level"] == "warning" for e in logs
    )
