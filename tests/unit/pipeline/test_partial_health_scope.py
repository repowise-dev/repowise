"""The update's health pass scopes its work to what it keeps.

The performance closure adds files whose call paths reach a changed sink. Only
their performance findings survive the filter in ``run_partial_analysis``, so
running every other detector on them, and re-splicing their clone windows,
was work whose output was discarded: on a central hugo helper the closure ran
to 249 files and cost two thirds of the health pass.
"""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import registered_biomarkers
from repowise.core.analysis.health.scoring import dimensions_for
from repowise.core.pipeline.incremental import _performance_only_config


def test_closure_files_keep_only_the_performance_detectors() -> None:
    config = _performance_only_config(None, {"pkg/caller.py"})

    disabled = config["per_file_disabled"]["pkg/caller.py"]
    names = {b.name for b in registered_biomarkers()}
    assert disabled == {n for n in names if "performance" not in dimensions_for(n)}
    assert all("performance" in dimensions_for(n) for n in names - disabled)
    assert "io_in_loop" not in disabled
    assert "dry_violation" in disabled


def test_an_empty_closure_leaves_the_config_alone() -> None:
    assert _performance_only_config(None, set()) is None
    cfg = {"disabled_biomarkers": ["dry_violation"]}
    assert _performance_only_config(cfg, set()) is cfg


def test_existing_per_file_rules_are_kept_and_widened() -> None:
    cfg = {"per_file_disabled": {"pkg/caller.py": {"io_in_loop"}, "pkg/other.py": {"god_class"}}}

    out = _performance_only_config(cfg, {"pkg/caller.py"})

    assert "io_in_loop" in out["per_file_disabled"]["pkg/caller.py"]
    assert "dry_violation" in out["per_file_disabled"]["pkg/caller.py"]
    assert out["per_file_disabled"]["pkg/other.py"] == {"god_class"}
    # The caller's dict is not mutated.
    assert cfg["per_file_disabled"]["pkg/caller.py"] == {"io_in_loop"}


def test_duplication_is_narrowed_to_the_changed_files(monkeypatch, tmp_path) -> None:
    """The clone detector sees the changed files, not the closure."""
    import networkx as nx

    from repowise.core.analysis.health import engine as engine_mod
    from repowise.core.analysis.health.duplication import DuplicationReport

    seen: dict = {}

    def fake_detect_clones(parsed_files, git_meta_map, **kwargs):
        seen["changed_files"] = kwargs.get("changed_files")
        return DuplicationReport()

    monkeypatch.setattr(engine_mod, "detect_clones", fake_detect_clones)
    analyzer = engine_mod.HealthAnalyzer(nx.DiGraph(), git_meta_map={}, parsed_files=[])
    analyzer.analyze(None, changed_files={"a.py", "b.py"}, duplication_files={"a.py"})
    assert seen["changed_files"] == {"a.py"}
    analyzer.analyze(None, changed_files={"a.py", "b.py"})
    assert seen["changed_files"] == {"a.py", "b.py"}


def test_empty_splice_set_skips_the_clone_detector(monkeypatch) -> None:
    import networkx as nx

    from repowise.core.analysis.health import engine as engine_mod

    def fail(*_args, **_kwargs):
        raise AssertionError("detect_clones ran with nothing to splice")

    monkeypatch.setattr(engine_mod, "detect_clones", fail)
    analyzer = engine_mod.HealthAnalyzer(nx.DiGraph(), git_meta_map={}, parsed_files=[])
    analyzer.analyze(None, changed_files={"README.md"}, duplication_files=set())


def test_prose_changes_are_not_spliced_and_dead_code_can_be_skipped(monkeypatch) -> None:
    from types import SimpleNamespace

    import networkx as nx

    from repowise.core.analysis.health import engine as engine_mod
    from repowise.core.pipeline.incremental import analysis

    seen: dict = {}

    class FakeAnalyzer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def analyze(self, config=None, **kwargs):
            seen["duplication_files"] = kwargs.get("duplication_files")
            return engine_mod.HealthReport(repo_id="", analyzed_at=None)

    monkeypatch.setattr("repowise.core.analysis.health.HealthAnalyzer", FakeAnalyzer)
    parsed = [
        SimpleNamespace(file_info=SimpleNamespace(path=p, language=lang))
        for p, lang in (("a.py", "python"), ("README.md", "markdown"))
    ]
    diffs = [SimpleNamespace(path=p, status="modified") for p in ("a.py", "README.md")]
    builder = SimpleNamespace(graph=lambda: nx.DiGraph(), community_detection=lambda: {})
    monkeypatch.setattr(
        "repowise.core.analysis.communities.file_community_labels", lambda _b: {}
    )

    health, dead = analysis.run_partial_analysis(
        ".", builder, {}, parsed, diffs, dead_code=False
    )

    assert seen["duplication_files"] == {"a.py"}
    assert health is not None
    assert dead is None
