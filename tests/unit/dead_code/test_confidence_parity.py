"""Cross-language contract tests for the dead-code vocabulary.

The TypeScript surface cannot import from Python, so `packages/types/src/dead-code.ts`
hand-mirrors two things the engine owns: the confidence tier boundaries
(`DEAD_CODE_CONFIDENCE` against `RISK_CAP_CONFIDENCE` / `SAFE_CONFIDENCE_THRESHOLD`)
and the risk-factor labels (`DEAD_CODE_RISK_FACTOR_LABELS` against `_FACTOR_BLURB`).
These parse the `.ts` file and fail when either mirror drifts.
"""

from __future__ import annotations

import re
from pathlib import Path

from repowise.core.analysis.dead_code.risk_factors import (
    _FACTOR_BLURB,
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
)


def test_ts_confidence_floor_matches_python_risk_cap() -> None:
    """Ensure DEAD_CODE_CONFIDENCE.MEDIUM in TypeScript matches RISK_CAP_CONFIDENCE (0.4)."""
    repo_root = Path(__file__).parents[3]
    ts_file = repo_root / "packages" / "types" / "src" / "dead-code.ts"
    assert ts_file.exists(), f"TypeScript types file not found at {ts_file}"

    content = ts_file.read_text(encoding="utf-8")

    medium_match = re.search(
        r"DEAD_CODE_CONFIDENCE\s*=\s*\{[\s\S]*?MEDIUM:\s*([0-9.]+)", content
    )
    assert medium_match is not None, "Could not parse DEAD_CODE_CONFIDENCE.MEDIUM from dead-code.ts"
    ts_medium = float(medium_match.group(1))

    assert ts_medium == RISK_CAP_CONFIDENCE, (
        f"Cross-language drift detected! TypeScript DEAD_CODE_CONFIDENCE.MEDIUM is {ts_medium}, "
        f"but Python RISK_CAP_CONFIDENCE is {RISK_CAP_CONFIDENCE}."
    )


def test_ts_confidence_high_matches_python_safe_threshold() -> None:
    """Ensure DEAD_CODE_CONFIDENCE.HIGH in TypeScript matches SAFE_CONFIDENCE_THRESHOLD (0.7)."""
    repo_root = Path(__file__).parents[3]
    ts_file = repo_root / "packages" / "types" / "src" / "dead-code.ts"
    assert ts_file.exists(), f"TypeScript types file not found at {ts_file}"

    content = ts_file.read_text(encoding="utf-8")

    high_match = re.search(
        r"DEAD_CODE_CONFIDENCE\s*=\s*\{[\s\S]*?HIGH:\s*([0-9.]+)", content
    )
    assert high_match is not None, "Could not parse DEAD_CODE_CONFIDENCE.HIGH from dead-code.ts"
    ts_high = float(high_match.group(1))

    assert ts_high == SAFE_CONFIDENCE_THRESHOLD, (
        f"Cross-language drift detected! TypeScript DEAD_CODE_CONFIDENCE.HIGH is {ts_high}, "
        f"but Python SAFE_CONFIDENCE_THRESHOLD is {SAFE_CONFIDENCE_THRESHOLD}."
    )


def test_ts_risk_factor_labels_match_python_blurbs() -> None:
    """Every factor the engine can emit has the same label in TypeScript.

    The UI joins `risk_factors` into a sentence, so an unlabelled factor renders
    as its raw API slug ("config, asset" where the engine says "configuration,
    runtime-loaded web asset"). Key sets are compared, not just values, so
    adding a factor in Python fails here until TypeScript learns its wording.
    """
    repo_root = Path(__file__).parents[3]
    ts_file = repo_root / "packages" / "types" / "src" / "dead-code.ts"
    content = ts_file.read_text(encoding="utf-8")

    block = re.search(
        r"DEAD_CODE_RISK_FACTOR_LABELS[^=]*=\s*\{(.*?)\n\};", content, re.DOTALL
    )
    assert block is not None, "Could not parse DEAD_CODE_RISK_FACTOR_LABELS from dead-code.ts"
    ts_labels = dict(re.findall(r"(\w+):\s*\"([^\"]*)\"", block.group(1)))

    assert ts_labels == _FACTOR_BLURB, (
        "Cross-language drift detected between DEAD_CODE_RISK_FACTOR_LABELS in "
        f"dead-code.ts and _FACTOR_BLURB in risk_factors.py.\n  TypeScript: {ts_labels}\n"
        f"  Python:     {_FACTOR_BLURB}"
    )


def test_every_emittable_factor_has_a_label() -> None:
    """`_FACTOR_BLURB` covers every tag the classifier's tables can produce.

    Guards the other direction from the test above: a new token added to the
    filename / pair / directory tables with a tag nobody wrote a blurb for
    would keep both mirrors consistent and still render the raw slug.
    """
    from repowise.core.analysis.dead_code.risk_factors import (
        _DIRECTORY_RISK_TOKENS,
        _FILENAME_RISK_TOKEN_PAIRS,
        _FILENAME_RISK_TOKENS,
    )

    emittable = (
        set(_FILENAME_RISK_TOKENS.values())
        | set(_FILENAME_RISK_TOKEN_PAIRS.values())
        | set(_DIRECTORY_RISK_TOKENS.values())
    )
    assert emittable <= set(_FACTOR_BLURB), (
        f"risk factors with no blurb: {sorted(emittable - set(_FACTOR_BLURB))}"
    )
