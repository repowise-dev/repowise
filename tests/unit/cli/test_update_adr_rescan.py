"""`repowise update` re-reads the ADRs it added or changed; before, only init read them."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import decision_passes
from repowise.core.analysis.decision_extractor import DecisionExtractor
from repowise.core.analysis.decisions.policy import preset_policy
from repowise.core.pipeline.phase_timing import PhaseTimings

_ADR = """\
---
status: accepted
title: {title}
---

## Context

Services need one event backbone.

## Decision

Use {title} for every service.
"""


def _write_adrs(root: Path) -> None:
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-old.md").write_text(_ADR.format(title="Old Choice"), encoding="utf-8")
    (adr_dir / "0002-new.md").write_text(_ADR.format(title="New Choice"), encoding="utf-8")


def _run_and_docs(root: Path, changed: dict[str, str]):
    run = SimpleNamespace(
        repo_path=root,
        file_diffs=[SimpleNamespace(path=p, status=s) for p, s in changed.items()],
        graph_builder=SimpleNamespace(graph=lambda: None),
        git_meta_map={},
        degraded=[],
    )
    docs = SimpleNamespace(
        provider=None,
        cost_tracker=SimpleNamespace(record_as=lambda _op: nullcontext()),
        new_decision_markers=[],
    )
    return run, docs


def _rescan(run, docs) -> None:
    opts = SimpleNamespace(verbose=False)
    decision_passes._rescan_adrs(opts, run, docs, preset_policy("default"), docs.provider)


async def test_discovery_keeps_only_the_named_adrs(tmp_path: Path) -> None:
    _write_adrs(tmp_path)

    found = await DecisionExtractor(repo_path=tmp_path).discover_adrs(
        restrict_to_files=["docs/adr/0002-new.md", "src/app.py"]
    )

    assert [d.title for d in found] == ["New Choice"]


async def test_rescan_finds_changed_adr_beyond_discovery_cap(tmp_path: Path) -> None:
    from repowise.core.analysis.decisions.adr import find_adr_files

    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    for i in range(61):
        (adr_dir / f"{i:04d}.md").write_text(_ADR.format(title=f"Choice {i}"), encoding="utf-8")
    all_files = set(adr_dir.glob("*.md"))
    omitted = (all_files - set(find_adr_files(tmp_path))).pop()
    relative = omitted.relative_to(tmp_path).as_posix()

    found = await DecisionExtractor(repo_path=tmp_path).rescan_adrs([relative])

    assert [d.evidence_file for d in found] == [relative]


async def test_rescan_still_excludes_ignored_and_non_adr_files(tmp_path: Path) -> None:
    _write_adrs(tmp_path)
    (tmp_path / ".gitignore").write_text("docs/adr/0001-old.md\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text(_ADR.format(title="Not an ADR"), encoding="utf-8")

    found = await DecisionExtractor(repo_path=tmp_path).rescan_adrs(
        ["docs/adr/0001-old.md", "notes.md"]
    )

    assert found == []


@pytest.mark.parametrize("status", ["added", "modified"])
def test_update_reads_a_changed_adr_and_skips_unchanged_ones(tmp_path: Path, status: str) -> None:
    _write_adrs(tmp_path)
    run, docs = _run_and_docs(tmp_path, {"docs/adr/0002-new.md": status, "src/app.py": "modified"})

    _rescan(run, docs)

    assert [(d.title, d.source, d.status) for d in docs.new_decision_markers] == [
        ("New Choice", "adr", "active")
    ]
    assert run.degraded == []


def test_a_deleted_adr_is_not_read(tmp_path: Path) -> None:
    _write_adrs(tmp_path)
    run, docs = _run_and_docs(tmp_path, {"docs/adr/0001-old.md": "deleted"})

    _rescan(run, docs)

    assert docs.new_decision_markers == []


def test_update_decision_passes_reach_the_real_adr_rescan(tmp_path: Path, monkeypatch) -> None:
    _write_adrs(tmp_path)
    run, docs = _run_and_docs(tmp_path, {"docs/adr/0002-new.md": "modified"})
    run.cfg = {}
    run.config = SimpleNamespace(
        git_changed=False, traversal_changed=False, generation_changed=False,
    )
    run.timings = PhaseTimings()
    monkeypatch.setattr(decision_passes, "_build_update_vector_store", lambda *_a, **_kw: None)
    monkeypatch.setattr(decision_passes, "_run_session_passes", lambda *_a: None)
    monkeypatch.setattr(decision_passes, "_evolve_decisions", lambda *_a: None)

    decision_passes.run_decision_passes(SimpleNamespace(verbose=False), run, docs)

    assert [decision.title for decision in docs.new_decision_markers] == ["New Choice"]
    assert run.degraded == []


def test_disabled_adr_source_skips_the_rescan(tmp_path: Path, monkeypatch) -> None:
    _write_adrs(tmp_path)
    run, docs = _run_and_docs(tmp_path, {"docs/adr/0002-new.md": "modified"})

    async def unexpected_rescan(*_a, **_kw):
        pytest.fail("disabled ADR source was scanned")

    monkeypatch.setattr(DecisionExtractor, "rescan_adrs", unexpected_rescan)

    decision_passes._rescan_adrs(
        SimpleNamespace(verbose=False), run, docs, preset_policy("off"), None,
    )

    assert docs.new_decision_markers == []
    assert run.degraded == []


def test_adr_rescan_failure_is_reported_without_losing_other_decisions(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _write_adrs(tmp_path)
    run, docs = _run_and_docs(tmp_path, {"docs/adr/0002-new.md": "modified"})
    earlier = object()
    docs.new_decision_markers.append(earlier)

    async def failing_rescan(*_a, **_kw):
        raise RuntimeError("ADR scanner unavailable")

    monkeypatch.setattr(DecisionExtractor, "rescan_adrs", failing_rescan)

    decision_passes._rescan_adrs(
        SimpleNamespace(verbose=True), run, docs, preset_policy("default"), None,
    )

    assert docs.new_decision_markers == [earlier]
    assert run.degraded == ["ADR re-scan: ADR scanner unavailable"]
    assert "ADR re-scan skipped: ADR scanner unavailable" in capsys.readouterr().out
