"""`repowise hook stats` must not report a retired surface as a live one.

Two of the ledger's rows are closed populations: their emission was deleted
and their judges were kept so a transcript backfill still settles the rows
already in the corpus. The table is data-driven off ``efficacy_rows()`` and
had no liveness concept, so ``read/reread`` rendered 100% and
``read/skeleton_nudge`` 0.2% off denominators that stopped growing — the same
number a working surface and a deleted one produce.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.sessions.efficacy import RETIRED_CATEGORIES, ledger_key
from repowise.core.sessions.staging import SessionStagingStore


def _seed(repo, surface: str, category: str, *, acted: bool) -> None:
    store = SessionStagingStore.open_default(repo)
    try:
        text = f"[repowise] {surface}/{category} emission"
        store.record_firing(
            session_id="sess-1",
            key=ledger_key(surface, category, text),
            surface=surface,
            category=category,
            chars=len(text),
            shown_at=1.0,
            duration_ms=50,
            acted=acted,
        )
        store.commit()
    finally:
        store.close()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".repowise" / "sessions").mkdir(parents=True)
    return repo


def _run(repo, *args):
    return CliRunner().invoke(cli, ["hook", "stats", str(repo), *args])


def test_retired_row_shows_retired_not_a_rate(tmp_path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, "read", "reread", acted=True)
    result = _run(repo)
    assert result.exit_code == 0
    assert "retired" in result.output
    # 100% off a closed population is the exact claim this exists to stop.
    assert "100.0%" not in result.output


def test_live_row_still_shows_its_rate(tmp_path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, "search", "triage", acted=True)
    result = _run(repo)
    assert result.exit_code == 0
    assert "100.0%" in result.output


def test_retired_and_live_rows_are_told_apart(tmp_path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, "read", "skeleton_nudge", acted=True)
    _seed(repo, "search", "triage", acted=True)
    result = _run(repo)
    assert result.exit_code == 0
    assert "retired" in result.output
    assert "100.0%" in result.output, "the live row keeps its rate"


def test_json_labels_retired_rows(tmp_path) -> None:
    # The machine-readable twin carried the same lie; a footer cannot reach it.
    repo = _repo(tmp_path)
    _seed(repo, "read", "reread", acted=True)
    _seed(repo, "search", "triage", acted=True)
    result = _run(repo, "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    by_pair = {(r["surface"], r["category"]): r["retired"] for r in payload["surfaces"]}
    assert by_pair[("read", "reread")] is True
    assert by_pair[("search", "triage")] is False


def test_every_retired_pair_has_a_judge(tmp_path) -> None:
    """The set names retirements, not deletions.

    A pair listed here without a classifier would stop settling on backfill,
    which is the one thing keeping these rows worth showing at all.
    """
    from repowise.core.sessions.efficacy import _PATTERNS, COMPLIANCE_CATEGORIES

    emitted = {(surface, category) for surface, category, _ in _PATTERNS}
    for pair in RETIRED_CATEGORIES:
        assert pair in emitted or pair in COMPLIANCE_CATEGORIES
