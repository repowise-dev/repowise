"""``get_risk`` must not answer a target it never resolved with zeros.

``module:ingestion`` and the bare directory it names both came back with
``dependents_count: 0`` while a single file inside that directory came back
with 59 — and the directory also carried ``episodes: 108`` from an enricher
that binds by path prefix, so one response held a confident "nothing depends on
this" beside evidence that something knew the target well. Nothing downstream
could tell that zero from a measured one.
"""

from __future__ import annotations

import pytest

#: Every numeric field a reader could mistake for a measurement. None of these
#: may appear on a card the tool did not resolve.
_MEASUREMENT_KEYS = (
    "dependents_count",
    "dependents_total",
    "direct_dependents_total",
    "transitive_dependents_total",
    "impact_surface_total",
    "co_change_partners_total",
    "hotspot_score",
    "is_hotspot",
    "test_gap",
    "security_signals",
    "episodes",
    "health_score",
)


@pytest.mark.asyncio
async def test_module_target_is_rejected_not_zeroed(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["module:auth"])
    card = result["targets"]["module:auth"]

    assert card["resolved"] is False
    assert card["unresolved_reason"] == "no_such_module"
    for key in _MEASUREMENT_KEYS:
        assert key not in card, f"{key} must be absent, not zero, on an unresolved target"
    assert "not resolved" in card["risk_summary"]


@pytest.mark.asyncio
async def test_directory_target_is_rejected_not_zeroed(setup_mcp, tmp_path):
    """The dangerous case: a real directory looks like an ordinary target."""
    from repowise.server.mcp_server import get_risk

    (tmp_path / "src" / "auth").mkdir(parents=True, exist_ok=True)

    result = await get_risk(["src/auth"])
    card = result["targets"]["src/auth"]

    assert card["resolved"] is False
    assert card["unresolved_reason"] == "directory"
    for key in _MEASUREMENT_KEYS:
        assert key not in card


@pytest.mark.asyncio
async def test_missing_path_reports_the_reason_that_names_the_fix(setup_mcp, tmp_path):
    """``not_indexed`` sends the caller to ``repowise update``; a typo does not."""
    from repowise.server.mcp_server import get_risk

    (tmp_path / "src" / "auth").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "auth" / "on_disk_only.py").write_text("x = 1\n", encoding="utf-8")

    result = await get_risk(["src/auth/on_disk_only.py", "src/auth/typo.py"])

    assert result["targets"]["src/auth/on_disk_only.py"]["unresolved_reason"] == "not_indexed"
    assert result["targets"]["src/auth/typo.py"]["unresolved_reason"] == "no_such_path"


@pytest.mark.asyncio
async def test_a_real_file_keeps_its_real_counts(setup_mcp):
    """The discriminator: rejecting bad targets must not reject good ones."""
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"], include=["graph"])
    card = result["targets"]["src/auth/service.py"]

    assert "resolved" not in card
    assert "unresolved_reason" not in card
    assert card["dependents_count"] >= 1
    assert card["hotspot_score"] == 0.92


@pytest.mark.asyncio
async def test_unresolved_and_resolved_targets_coexist_in_one_call(setup_mcp, tmp_path):
    """A batch must not lose the good card, or leak counts onto the bad one."""
    from repowise.server.mcp_server import get_risk

    (tmp_path / "src" / "auth").mkdir(parents=True, exist_ok=True)

    result = await get_risk(["src/auth", "src/auth/service.py"])
    targets = result["targets"]

    assert set(targets) == {"src/auth", "src/auth/service.py"}
    assert targets["src/auth"]["resolved"] is False
    assert targets["src/auth/service.py"]["dependents_count"] >= 1


@pytest.mark.asyncio
async def test_enrichment_does_not_bind_signal_to_an_unresolved_card(setup_mcp, tmp_path):
    """The half that made the zero convincing.

    The live reproduction returned ``episodes: 108`` for a bare directory,
    sitting directly beside ``dependents_count: 0``. That is not one subsystem
    with no data, it is two subsystems disagreeing about whether the target
    means anything, and the response showed only the reassuring half. An
    enricher must not attach a measurement to a card that resolved nothing.
    """
    import time

    from repowise.core.precedent.store import (
        TIER_STRUCTURAL,
        Episode,
        EpisodeStore,
        default_store_path,
    )
    from repowise.server.mcp_server import get_risk

    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "auth").mkdir(parents=True, exist_ok=True)
    with EpisodeStore(default_store_path(tmp_path)) as store:
        store.replace_kinds(
            tier=TIER_STRUCTURAL,
            kinds=["layout_drift"],
            episodes=[
                Episode(
                    tier=TIER_STRUCTURAL,
                    kind="layout_drift",
                    subject="src/auth",
                    body="The auth package was reorganised.",
                    evidence="git log --stat src/auth",
                    nodes=("src/auth",),
                    birth_commit=None,
                )
            ],
            now=time.time(),
        )

    card = (await get_risk(["src/auth"]))["targets"]["src/auth"]

    assert card["resolved"] is False
    assert "episodes" not in card


@pytest.mark.asyncio
async def test_symbol_targets_are_not_rejected(setup_mcp):
    """``path::Symbol`` is an accepted input shape, not an unresolved target.

    It reaches no ``git_metadata`` row and no file node either, so the guard
    would swallow it without this carve-out — and the episode enricher already
    resolves a symbol id to its file, which only makes sense if the tool is
    expected to be handed one.
    """
    from repowise.server.mcp_server import get_risk

    card = (await get_risk(["src/auth/service.py::AuthService"]))["targets"][
        "src/auth/service.py::AuthService"
    ]

    assert "resolved" not in card
    assert card["dependents_count"] == 0
