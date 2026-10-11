"""The text Repowise Lens (the Claude Code mod in packages/claude-mod) reads.

Lens draws rows from two strings the Python side writes: the distill omission
marker at the end of a distilled output, and the augment hook's edit-time
notices. It parses them; it does not recompute them. The golden lines in
tests/fixtures/lens/ are asserted here against the real formatters and parsed
by the mod's own tests from the same files, so a wording change on either side
fails CI instead of silently blanking a Lens row.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.commands.augment_cmd import decision_inject
from repowise.core.distill.markers import parse_markers, render_marker
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DecisionAcceptance,
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
    GitMetadata,
    Repository,
)

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "lens"
_REPO_ID = "repo1"


def _golden_lines(name: str) -> list[str]:
    return (GOLDEN / name).read_text(encoding="utf-8").splitlines()


def test_distill_marker_matches_the_golden_line() -> None:
    (golden,) = _golden_lines("distill-marker.txt")
    assert render_marker("21ece412acd8", 64, 2337) == golden
    (parsed,) = parse_markers(golden)
    assert (parsed.ref, parsed.lines_omitted, parsed.tokens_omitted) == ("21ece412acd8", 64, 2337)


def _decision(spec: dict) -> list:
    rows: list = [
        DecisionRecord(
            id=spec["id"],
            repository_id=_REPO_ID,
            title=spec["title"],
            decision=spec["decision"],
            rationale=spec.get("rationale", ""),
            status=spec["status"],
            source=spec["source"],
            scope_basis="",
            confidence=0.9,
            staleness_score=0.0,
            evidence_file=spec["id"],
        ),
        DecisionNodeLink(
            repository_id=_REPO_ID, decision_id=spec["id"], node_id=spec["file"], link_type="file"
        ),
    ]
    if spec.get("accepted"):
        rows.append(
            DecisionAcceptance(
                repository_id=_REPO_ID,
                decision_id=spec["id"],
                seq=1,
                action="accepted",
                currency="active",
                reason=spec["title"],
                scope_json=json.dumps([spec["id"]]),
                evidence_json=json.dumps([spec["id"]]),
                accepter="tester",
            )
        )
    rows += [
        DecisionEvidence(
            decision_id=spec["id"], source="session", evidence_commit=s, source_quote="q"
        )
        for s in spec.get("sessions", [])
    ]
    return rows


async def _build_wiki_db(repo_root: Path) -> None:
    db_path = repo_root / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    last_fix = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=14)
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(repo_root)))
        for row in _decision(
            {
                "id": "d-standing",
                "title": "Use JWT auth",
                "decision": "All service auth uses short-lived JWT tokens",
                "rationale": "session cookies broke the mobile clients",
                "status": "active",
                "source": "cli",
                "accepted": True,
                "file": "src/core/auth.py",
                "sessions": ["sess-a", "sess-b"],
            }
        ) + _decision(
            {
                "id": "d-mined",
                "title": "Keep the parser allocation-free",
                "decision": "reuse the buffer",
                "status": "proposed",
                "source": "comment",
                "file": "src/core/parser.py",
            }
        ):
            session.add(row)
        session.add(
            GitMetadata(
                id="gm1",
                repository_id=_REPO_ID,
                file_path="src/core/pipeline.py",
                prior_defect_count=5,
                bug_magnet=True,
                last_fix_at=last_fix,
                fix_symbol_counts_json=json.dumps({"src/core/pipeline.py::run_pipeline": 4}),
            )
        )
        await session.commit()
    await engine.dispose()


async def test_augment_edit_notices_match_the_golden_lines(tmp_path) -> None:
    await _build_wiki_db(tmp_path)
    standing, mined, fixes = _golden_lines("augment-edit-notices.txt")

    # No session id: the formatters run without claiming the session ledger.
    assert decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "", {}) == standing
    assert decision_inject._edit_decision_notice(tmp_path, "src/core/parser.py", "", {}) == mined
    assert decision_inject._edit_fix_history_notice(tmp_path, "src/core/pipeline.py", "") == fixes
