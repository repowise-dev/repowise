"""``get_risk`` must not answer a target it never resolved with zeros.

A ``module:`` id or a directory used to come back with every numeric field
zeroed, indistinguishable from a measured zero, while an enricher keyed on the
path prefix bound real signal to the same card.
"""

from __future__ import annotations

import pytest

#: Fields no unresolved card may carry. The four ``*_total``/direction counts
#: are ``include=["graph"]`` keys popped from every card otherwise, so tests
#: asserting their absence must ask for the block or prove nothing.
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

    result = await get_risk(["module:auth"], include=["graph", "churn"])
    card = result["targets"]["module:auth"]

    assert card["resolved"] is False
    # Not ``no_such_module``: that is get_health's "no module of that name",
    # and ``auth`` is a real module there.
    assert card["unresolved_reason"] == "unsupported_target_kind"
    for key in _MEASUREMENT_KEYS:
        assert key not in card, f"{key} must be absent, not zero, on an unresolved target"
    assert "not resolved" in card["risk_summary"]


@pytest.mark.asyncio
async def test_directory_target_is_rejected_not_zeroed(setup_mcp, tmp_path):
    """The dangerous case: a real directory looks like an ordinary target."""
    from repowise.server.mcp_server import get_risk

    (tmp_path / "src" / "auth").mkdir(parents=True, exist_ok=True)

    result = await get_risk(["src/auth"], include=["graph", "churn"])
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
    # Pins the ``include=["graph"]`` gate open, so the ``*_total`` absences
    # asserted above mean something.
    assert card["dependents_total"] >= 1


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
    """An enricher must not attach a measurement to a card that resolved nothing."""
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
    """``path::Symbol`` is an accepted input shape: it reaches no git row and no
    file node, so the guard would swallow it without the carve-out.
    """
    from repowise.server.mcp_server import get_risk

    card = (await get_risk(["src/auth/service.py::AuthService"]))["targets"][
        "src/auth/service.py::AuthService"
    ]

    assert "resolved" not in card
    assert card["dependents_count"] == 0


@pytest.mark.asyncio
async def test_pr_mode_does_not_stamp_counts_back_onto_an_unresolved_card(setup_mcp, tmp_path):
    """PR mode re-walks the response's cards, not the assessed set, and restates
    co-change counts — putting two structural zeros back without a skip.
    """
    from repowise.server.mcp_server import get_risk

    (tmp_path / "src" / "auth").mkdir(parents=True, exist_ok=True)

    result = await get_risk(["src/auth"], changed_files=["src/auth/service.py"])
    card = result["targets"]["src/auth"]

    assert card["resolved"] is False
    assert "co_change_partners_emitted" not in card
    assert "co_change_partners_truncated" not in card
