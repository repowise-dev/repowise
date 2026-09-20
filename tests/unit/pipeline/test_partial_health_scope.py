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
