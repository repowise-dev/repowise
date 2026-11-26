"""The identity models and the analyzer stamp have to move together.

A stored opportunity id names the model that minted it. Nothing re-mints those
ids except the re-score, and the re-score is triggered by
``HEALTH_ANALYZER_VERSION`` alone - neither identity model version is an input
to that gate. So a model bump that lands without an analyzer bump leaves every
stored id describing a model that no longer exists, on a store that will never
be told to recompute.

``REFACTORING_MODEL_VERSION`` went 1 -> 2 with no matching analyzer bump. It was
harmless only because an unrelated change had already moved the analyzer in the
same release. This pins the stamps so that cannot happen quietly again.

The same holds for the finding id kernel and the change-finding id kernel: both
ids are stored on rows only the re-score (or, for commit-health rows, an
analyzer version mismatch) rewrites.

Every version stamp is listed in ``docs/architecture/version-stamps.md``. The
tests below keep that page and the code in step: a new module-level
``*_VERSION`` constant fails until it has a row, and a recompute stamp has to be
either pinned here or named as moving alone.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

from repowise.core.analysis.change_health.identity import (
    IDENTITY_VERSION as CHANGE_FINDING_ID_VERSION,
)
from repowise.core.analysis.health.engine import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.health.finding_identity import FINDING_ID_VERSION
from repowise.core.analysis.health.perf.causal import PERFORMANCE_MODEL_VERSION
from repowise.core.analysis.health.refactoring.identity import (
    REFACTORING_MODEL_VERSION,
)

# The stamps as they stand together. Moving any identity kernel means moving the
# analyzer with it, and updating this tuple in the same commit.
_STAMPS = (47, 5, 4, 2, 1)


def test_the_identity_models_and_the_analyzer_stamp_are_pinned_together() -> None:
    """If you changed an identity kernel: bump the analyzer too, then ``_STAMPS``."""
    assert (
        HEALTH_ANALYZER_VERSION,
        PERFORMANCE_MODEL_VERSION,
        REFACTORING_MODEL_VERSION,
        FINDING_ID_VERSION,
        CHANGE_FINDING_ID_VERSION,
    ) == _STAMPS


# --- the stamp list --------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_DOC = _REPO / "docs" / "architecture" / "version-stamps.md"
_PACKAGES = _REPO / "packages"

#: Recompute stamps pinned by the test above, as (constant, path) doc keys.
_COUPLED = frozenset(
    {
        ("HEALTH_ANALYZER_VERSION", "core/analysis/health/engine.py"),
        ("PERFORMANCE_MODEL_VERSION", "core/analysis/health/perf/causal.py"),
        ("REFACTORING_MODEL_VERSION", "core/analysis/health/refactoring/identity.py"),
        ("FINDING_ID_VERSION", "core/analysis/health/finding_identity.py"),
        ("IDENTITY_VERSION", "core/analysis/change_health/identity.py"),
    }
)

#: Recompute stamps with a gate of their own, which move without the analyzer.
_RECOMPUTE_ALONE = frozenset(
    {
        ("SECURITY_SCANNER_VERSION", "core/analysis/security/patterns.py"),
        ("GIT_HISTORY_VERSION", "core/ingestion/git_indexer/_constants.py"),
        ("STORE_FORMAT_VERSION", "core/upgrade/version.py"),
        ("PARSER_SCHEMA_VERSION", "core/upgrade/version.py"),
        ("CONTRACTS_VERSION", "core/workspace/contracts.py"),
        ("ROUTE_TABLE_VERSION", "core/workspace/repo_routes.py"),
        ("STATEMENT_SITES_VERSION", "core/workspace/statement_sites.py"),
        ("KG_BUILDER_VERSION", "core/analysis/knowledge_graph.py"),
        ("SNAPSHOT_FACTS_VERSION", "core/events/snapshot.py"),
        ("ACTIONS_MODEL_VERSION", "core/analysis/actions/model.py"),
        ("FIX_FIRST_MODEL_VERSION", "core/analysis/health/fix_first/model.py"),
        ("ONBOARDING_GENERATION_VERSION", "core/generation/onboarding/slots.py"),
        ("STRUCTURAL_GENERATION_VERSION", "core/generation/page_generator/structural.py"),
        ("MODEL_PAGE_GENERATION_VERSION", "core/generation/page_generator/reuse.py"),
        ("MIN_SHOWN_ANALYZER_VERSION", "core/analysis/history/delta.py"),
    }
)

_KINDS = {
    "Recompute stamps": "recompute",
    "Local cache versions": "cache",
    "Schema and format IDs": "format",
}
_ROW = re.compile(r"^\|\s*`(\w+)`\s*\|\s*`([^`]+)`\s*\|")
_STAMP_NAME = re.compile(r"^_?(?:[A-Z][A-Z0-9_]*_)?VERSION$")


@functools.cache
def _doc_rows() -> dict[tuple[str, str], str]:
    """Each (constant, path) row of the stamp page, mapped to its section's kind."""
    rows: dict[tuple[str, str], str] = {}
    kind = None
    for line in _DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            kind = _KINDS.get(line[3:].strip())
        elif kind and (match := _ROW.match(line)):
            rows[(match[1], match[2])] = kind
    return rows


def _literal_stamp(node: ast.stmt) -> str | None:
    """The name a module-level ``*_VERSION = <int or str literal>`` binds, else None."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target, value = node.targets[0], node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        target, value = node.target, node.value
    else:
        return None
    if not isinstance(target, ast.Name) or not _STAMP_NAME.match(target.id):
        return None
    if not isinstance(value, ast.Constant) or isinstance(value.value, bool):
        return None
    return target.id if isinstance(value.value, (int, str)) else None


@functools.cache
def _source_stamps() -> frozenset[tuple[str, str]]:
    """Every literal module-level ``*_VERSION`` under ``packages/*/src/repowise/``."""
    found: set[tuple[str, str]] = set()
    for path in _PACKAGES.glob("*/src/repowise/**/*.py"):
        text = path.read_text(encoding="utf-8")
        if "VERSION" not in text:
            continue
        package = path.relative_to(_PACKAGES).parts[0]
        rel = path.relative_to(_PACKAGES / package / "src" / "repowise").as_posix()
        for node in ast.parse(text, filename=str(path)).body:
            if name := _literal_stamp(node):
                found.add((name, rel))
    return frozenset(found)


def test_every_version_constant_has_a_row_on_the_stamp_page() -> None:
    """A new stamp is documented with what a bump triggers before it lands."""
    missing = _source_stamps() - set(_doc_rows())
    assert not missing, (
        f"add a row to {_DOC.relative_to(_REPO).as_posix()} for each of: "
        + ", ".join(f"{name} ({path})" for name, path in sorted(missing))
    )


def test_every_row_on_the_stamp_page_names_a_constant_in_the_code() -> None:
    stale = set(_doc_rows()) - _source_stamps()
    assert not stale, f"rows naming no constant: {sorted(stale)}"


def test_every_recompute_stamp_is_pinned_or_named_as_moving_alone() -> None:
    """A recompute stamp is either coupled to the analyzer or has its own gate."""
    recompute = {key for key, kind in _doc_rows().items() if kind == "recompute"}
    assert not _COUPLED & _RECOMPUTE_ALONE
    assert recompute == _COUPLED | _RECOMPUTE_ALONE
