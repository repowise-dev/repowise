"""No seventh answer to "what is this repo's hotspot health" (P10).

An architecture check rather than a behaviour one. Six implementations shipped
at once: the owner in :mod:`repowise.core.analysis.health.scoring`, a top-25%-
by-NLOC average in ``get_overview``, the same wrong average again in
``repowise status``, an inline re-derivation in the CLAUDE.md fetcher, a
snapshot read on each of two dashboard routes, and ``get_health`` omitting the
number entirely while ``get_overview`` invented one. They disagreed on all 42
local indexes, median 2.67 points of 10.

Nothing caught that, because nothing tested it: ``compute_kpis`` had tests that
never asserted ``hotspot_health``, and the two surfaces carrying the wrong
definition had no tests at all. A reviewer noticing the seventh copy is not a
plan, so this fails when one appears.

Ceiling: this matches on module contents, not on semantics. A module that
computes hotspot health under names none of the lists below know stays
invisible. It catches the shape that actually recurred here — a surface
deriving the number itself instead of calling the owner.
``tests/unit/health/test_hotspot_health.py`` is what catches a *wrong* answer;
this only catches a second implementation of the question.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_OWNER_MODULE = "repowise/core/analysis/health/scoring.py"
_OWNER_MODULE_FROM_SRC = "core/src/repowise/core/analysis/health/scoring.py"
_OWNER_IMPORT = "from repowise.core.analysis.health.scoring import"

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"

# Modules that legitimately mention ``hotspot_health`` without computing it.
# Each entry says why. The list only ever shrinks: a new surface that renders
# this number belongs in the "imports the owner" set, not here.
_PLUMBING: dict[str, str] = {
    # --- storage and schema: the value arrives already computed --------------
    "repowise/core/persistence/models.py": "the column",
    "repowise/core/persistence/crud/analysis/health.py": "writes/reads the column",
    "repowise/core/persistence/crud/git.py": "supplies the hotspot path set, not the score",
    "repowise/core/persistence/stores/_sql_analysis.py": "passthrough to the crud writer",
    "repowise/core/persistence/_interfaces/_analysis.py": "the store ABC's parameter name",
    "repowise/server/schemas/repository.py": "the response field",
    "repowise/server/schemas/code_health.py": "the trend response fields",
    "alembic/versions/0019_code_health.py": "the migration that adds the column",
    # --- writers: hand ``compute_kpis`` output to the snapshot ---------------
    "repowise/core/pipeline/persist.py": "persists the KPI dict from the health report",
    "repowise/cli/commands/health_cmd/persist.py": "same, for `repowise health`",
    "repowise/cli/commands/upgrade_flow.py": "same, for `repowise upgrade`",
    # --- trend surfaces: diff recorded snapshots, no current value -----------
    "repowise/core/analysis/health/trends.py": "diffs snapshots; owns no current value",
    "repowise/server/routers/code_health/trends_routes.py": "serves the snapshot series",
    "repowise/cli/commands/health_cmd/trends.py": "prints the snapshot series",
    # --- pure renderers: read a value someone else computed ------------------
    "repowise/cli/commands/health_cmd/command.py": "prints the KPI dict it was handed",
    "repowise/core/generation/editor_files/data.py": "the CodeHealthBlock field",
    "repowise/server/routers/repos.py": "serves the latest snapshot column on /repos/summary",
}


def _modules_mentioning_hotspot_health() -> list[pathlib.Path]:
    out = []
    for path in sorted(_PACKAGES.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover - defensive
            continue
        if "hotspot_health" in text:
            out.append(path)
    return out


def _relative(path: pathlib.Path) -> str:
    """``packages/<pkg>/src/`` and ``packages/core/`` stripped to a stable key."""
    parts = path.as_posix().split("/")
    if "src" in parts:
        return "/".join(parts[parts.index("src") + 1 :])
    # packages/core/alembic/versions/... -> alembic/versions/...
    return "/".join(parts[parts.index("packages") + 2 :])


def test_the_owner_module_is_where_the_arithmetic_lives() -> None:
    """Guards the constant this whole test file is keyed on."""
    owner = _PACKAGES / _OWNER_MODULE_FROM_SRC
    assert owner.is_file(), f"the owner moved; update _OWNER_MODULE ({_OWNER_MODULE})"
    text = owner.read_text(encoding="utf-8")
    assert "def hotspot_health(" in text


def test_every_surface_gets_hotspot_health_from_the_one_owner() -> None:
    """A module that mentions the KPI either imports it, stores it, or renders it.

    Anything else is a seventh implementation.
    """
    offenders: list[str] = []
    for path in _modules_mentioning_hotspot_health():
        rel = _relative(path)
        if rel == _OWNER_MODULE:
            continue
        if rel in _PLUMBING:
            continue
        text = path.read_text(encoding="utf-8")
        if _OWNER_IMPORT in text:
            continue
        offenders.append(rel)

    assert not offenders, (
        "these modules reference hotspot_health without importing the shared "
        f"owner and without a recorded reason: {offenders}. Call "
        "`repowise.core.analysis.health.scoring.hotspot_health`, or add an "
        "entry to _PLUMBING saying why this module only stores or renders it."
    )


@pytest.mark.parametrize("rel", sorted(_PLUMBING))
def test_the_plumbing_allowlist_has_no_dead_entries(rel: str) -> None:
    """An allowlist entry for a module that no longer mentions the KPI is rot.

    Without this the list silently grows into a place where a real copy can
    hide behind a stale name.
    """
    known = {_relative(p) for p in _modules_mentioning_hotspot_health()}
    assert rel in known, (
        f"_PLUMBING lists {rel!r}, which no longer mentions hotspot_health. "
        "Remove the entry."
    )


def test_the_retired_top_quartile_definition_is_gone() -> None:
    """The exact shape that shipped twice must not reappear.

    Both wrong sites sorted every metric row by NLOC and sliced ``// 4``. This
    catches a paste of that idiom anywhere under ``packages/``.

    The pattern deliberately tolerates the closing paren: the shipped code read
    ``[: max(1, len(by_nloc) // 4)]``, and a first cut of this test matched only
    ``// 4]``, so it stayed green against the very code it was written to
    outlaw. Verified by probe against the real shape, not the remembered one.
    """
    quartile_slice = re.compile(r"//\s*4\s*\)?\s*\]")
    offenders: list[str] = []
    for path in sorted(_PACKAGES.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover - defensive
            continue
        if not quartile_slice.search(text):
            continue
        if "nloc" not in text.lower():
            continue
        offenders.append(_relative(path))

    assert not offenders, (
        "the top-quartile-by-NLOC hotspot definition is back in "
        f"{offenders}. It ranks file size, not churn, and it read higher than "
        "the real KPI on 31 of 42 measured repos."
    )
