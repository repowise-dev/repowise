"""``health.duplication.enabled: false`` skips clone detection and drops its caches.

Off, the pass finds no clone, stores no duplication share, and deletes the
token, delta and pair caches it would otherwise keep under ``.repowise``.
Turned back on, the next pass finds the same clones and writes them again.
"""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands.health_cmd import live_score
from repowise.core.analysis.change_health.analyzer import RevisionAnalysis, RevisionHealthAnalyzer
from repowise.core.analysis.change_health.index_inputs import IndexInputs
from repowise.core.analysis.health.config import HealthConfig
from repowise.core.analysis.health.duplication import CLONE_CACHE_FILES
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.source_reader import MappingSourceReader

from .conftest import python_complex

_FILES = {
    "app/a.py": python_complex("run", 14),
    "app/b.py": python_complex("run", 14),
    "app/c.py": python_complex("other", 3),
}


def _set_duplication(repo: Path, enabled: object) -> None:
    config = repo / ".repowise" / "config.yaml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(f"health:\n  duplication:\n    enabled: {enabled}\n", encoding="utf-8")


def _index_pass(repo: Path):
    """The index's whole-repo pass, with the repo's health config as init applies it."""
    sources = {path: (repo / path).read_bytes() for path in _FILES}
    revision = RevisionHealthAnalyzer(repo_root=str(repo))
    parsed, usable = revision._parse(sources, RevisionAnalysis(), None)
    cfg = HealthConfig.load(repo)
    config = cfg.to_analyzer_config(list(_FILES)) if cfg.has_overrides() else None
    return HealthAnalyzer(
        revision._graph(parsed, usable),
        parsed_files=parsed,
        repo_root=str(repo),
        source_reader=MappingSourceReader(usable),
        duplication_cache_dir=repo / ".repowise",
    ).analyze(config)


def _dry(report) -> set[str]:
    return {f.file_path for f in report.findings if f.biomarker_type == "dry_violation"}


def _caches(repo: Path) -> set[str]:
    return {name for name in CLONE_CACHE_FILES if (repo / ".repowise" / name).exists()}


def test_off_skips_clones_and_deletes_the_caches_and_on_brings_them_back(make_repo):
    repo = make_repo()
    repo.commit("seed", _FILES)
    root = Path(repo.path)

    first = _index_pass(root)
    assert {"app/a.py", "app/b.py"} <= _dry(first)
    assert _caches(root)

    _set_duplication(root, "false")
    off = _index_pass(root)
    assert _dry(off) == set()
    assert all(m.duplication_pct is None for m in off.metrics)
    assert _caches(root) == set()

    _set_duplication(root, "true")
    again = _index_pass(root)
    assert _dry(again) == _dry(first)
    assert _caches(root)


def test_the_live_score_honours_the_switch(make_repo, monkeypatch):
    repo = make_repo()
    repo.commit("seed", _FILES)
    _set_duplication(Path(repo.path), "false")
    monkeypatch.setattr(live_score, "_index_inputs", lambda _path: IndexInputs())

    scores = live_score.score_live(Path(repo.path), ["app/a.py"], None)

    assert scores.head.clone_source is None


def test_an_unreadable_value_keeps_duplication_on(tmp_path):
    _set_duplication(tmp_path, "sometimes")
    assert HealthConfig.load(tmp_path).duplication_enabled is True
    assert "dry_violation" not in HealthConfig.load(tmp_path).disabled_biomarkers


def test_off_is_merged_with_the_health_rules_file(tmp_path):
    _set_duplication(tmp_path, "false")
    (tmp_path / ".repowise" / "health-rules.json").write_text(
        '{"disabled_biomarkers": ["god_class"]}', encoding="utf-8"
    )
    cfg = HealthConfig.load(tmp_path)
    assert cfg.disabled_biomarkers == ["god_class", "dry_violation"]
    assert cfg.has_overrides()


def test_a_biomarker_disabled_in_the_rules_file_keeps_the_caches(make_repo):
    repo = make_repo()
    repo.commit("seed", _FILES)
    root = Path(repo.path)
    _index_pass(root)
    kept = _caches(root)
    (root / ".repowise" / "health-rules.json").write_text(
        '{"disabled_biomarkers": ["dry_violation"]}', encoding="utf-8"
    )

    assert _dry(_index_pass(root)) == set()
    assert _caches(root) == kept
