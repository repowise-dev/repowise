"""Stored advisories on agent surfaces: the package card and the get_risk line.

A file or package with no stored advisory must come back byte for byte as it
did before advisories existed.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._packages import package_cards
from repowise.server.mcp_server.tool_risk.assessment import attach_advisory_calls
from tests.unit.core.inventory_fixture import REPO, session  # noqa: F401
from tests.unit.core.test_advisory_tiers import _import, _seed_calls


def _cards(*paths: str) -> list[dict]:
    return [{"target": p, "hotspot_score": 0.1, "security_signals": []} for p in paths]


async def test_risk_cards_without_an_advisory_are_unchanged(session, tmp_path):  # noqa: F811
    await _seed_calls(session)
    cards = _cards("src/app/util.py", "src/jobs/worker.py")
    before = copy.deepcopy(cards)
    # Calls stored, no advisory document: nothing is added.
    await attach_advisory_calls(session, REPO, str(tmp_path), cards)
    assert json.dumps(cards) == json.dumps(before)


async def test_risk_line_only_on_the_file_whose_call_production_reaches(session, tmp_path):  # noqa: F811
    await _seed_calls(session)
    await _import(session, tmp_path, "osv-scanner.json")
    cards = _cards("src/app/util.py", "src/old/legacy.py", "src/jobs/worker.py")
    untouched = copy.deepcopy(cards[1:])
    await attach_advisory_calls(session, REPO, str(tmp_path), cards)
    assert cards[0]["advisory_call"] == (
        "GHSA-9wx4-h78v-vm56 in requests: production code calls into the package "
        "at src/app/util.py:5"
    )
    # An unreached call and a file with no call add zero bytes.
    assert json.dumps(cards[1:]) == json.dumps(untouched)


async def _package(session, tmp_path, *targets: str) -> dict:  # noqa: F811
    collector = OmissionCollector("get_context", repo_root=str(tmp_path))
    repo = SimpleNamespace(id=REPO)
    return await package_cards(
        session, repo, list(targets), repo_root=tmp_path, collector=collector
    )


async def test_package_card_without_advisories_is_unchanged(session, tmp_path):  # noqa: F811
    await _seed_calls(session)
    before = await _package(session, tmp_path, "package:pypi:requests", "package:yaml")
    await _import(session, tmp_path, "osv-scanner.json")
    after = await _package(session, tmp_path, "package:pypi:requests", "package:yaml")
    # yaml has no advisory: its card is the same bytes.
    assert json.dumps(after["package:yaml"]) == json.dumps(before["package:yaml"])
    assert "advisories" not in before["package:pypi:requests"]
    assert after["package:pypi:requests"]["advisories"] == [
        {"id": "GHSA-9wx4-h78v-vm56", "tier": "package_called", "sites": ["src/app/util.py:5"]}
    ]


async def test_package_card_checks_a_version(session, tmp_path):  # noqa: F811
    await _import(session, tmp_path, "osv.json")
    cards = await _package(
        session, tmp_path, "package:pypi:requests@2.31.0", "package:pypi:flask@3.0"
    )
    hit = cards["package:pypi:requests@2.31.0"]
    assert hit["package"] == "requests"
    assert hit["check"] == {
        "verdict": "has_advisories",
        "version": "2.31.0",
        "advisories": [{"id": "GHSA-9wx4-h78v-vm56", "affected": True, "fix": "2.32.0"}],
    }
    assert cards["package:pypi:flask@3.0"]["check"]["verdict"] == "unknown_no_source"
