"""analyze_test_impact output, byte-for-byte, over the sealed PR fixture.

Set ``REPOWISE_REGEN_GOLDEN=1`` to rewrite the golden after an intended change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from repowise.core.analysis.test_impact import analyze_test_impact, legacy_guarding_tests
from repowise.core.exclusion import build_exclude_spec
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_pr_test_impact_semantics import _fixture, _seed

_GOLDEN = Path(__file__).parent / "golden" / "test_impact.json"


async def _scenarios(session, tmp_path) -> dict:
    fixture = _fixture()
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "exclude_patterns:\n  - src/excluded.py\n  - tests/excluded/**\n",
        encoding="utf-8",
    )
    current = await insert_repo(
        session, name="current", local_path="/tmp/current", head_commit="fixture-head"
    )
    await _seed(session, current.id, fixture)
    stale = await insert_repo(
        session, name="stale", local_path="/tmp/stale", head_commit="moved-on"
    )
    await _seed(session, stale.id, fixture)
    bare = await insert_repo(
        session, name="bare", local_path="/tmp/bare", head_commit="fixture-head"
    )
    await _seed(session, bare.id, fixture, with_coverage=False)

    changed = [*fixture["changed_files"], "app/removed_module.py", "src/seed.py"]
    status = {
        "app/removed_module.py": "deleted",
        "src/seed.py": "deleted",
        "src/inferred.py": "added",
        "src/both.py": "modified",
    }
    out = {
        "current": await analyze_test_impact(
            session,
            current.id,
            changed,
            repository_alias="alpha",
            exclude_spec=build_exclude_spec(tmp_path),
            change_status=status,
        ),
        "stale": await analyze_test_impact(session, stale.id, fixture["changed_files"]),
        "no_coverage": await analyze_test_impact(session, bare.id, changed),
        "nothing_changed": await analyze_test_impact(session, current.id, []),
    }
    out["legacy_current"] = legacy_guarding_tests(out["current"])
    out["legacy_no_coverage"] = legacy_guarding_tests(out["no_coverage"])
    text = json.dumps(out, indent=2, sort_keys=True, default=str)
    for repo, name in ((current, "current"), (stale, "stale"), (bare, "bare")):
        text = text.replace(repo.id, f"<{name}>")
    out = json.loads(text)
    for impact in out.values():
        if impact["coverage"]["ingested_at"] is not None:
            impact["coverage"]["ingested_at"] = "<ingested_at>"
    return out


async def test_test_impact_matches_golden(async_session, tmp_path) -> None:
    got = await _scenarios(async_session, tmp_path)
    if os.environ.get("REPOWISE_REGEN_GOLDEN"):
        _GOLDEN.parent.mkdir(exist_ok=True)
        _GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert got == json.loads(_GOLDEN.read_text(encoding="utf-8"))
