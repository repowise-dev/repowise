"""Per-candidate scoring functions.

Each scorer takes a candidate plus the precomputed graph metrics and
returns a float in roughly ``[0, 2]``. Higher = more important.

Scoring is intentionally simple and explainable. The previous bypass
rules in ``_is_significant_file`` (test files, ``__init__.py``,
``betweenness > 0``, ``entry_point``) are folded in here as **bonuses**,
not hard overrides — high-value files still compete against each other
for the bucket's slot count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# -- File score --------------------------------------------------------

# Bonuses applied additively on top of normalized PageRank+betweenness.
# Tuned so a file with even one strong signal scores above a generic
# mid-PageRank file with no other characteristics.
_BONUS_ENTRY_POINT = 0.40
_BONUS_HOTSPOT = 0.20
_BONUS_INIT_PY_RE_EXPORTER = 0.15
_BONUS_BETWEENNESS_BRIDGE = 0.10  # any bridge file (bet > 0)

# Penalties — applied multiplicatively at the end. Keeps tests/trivial
# files in the pool but ranks them below substantive code unless they
# have countervailing signals.
_PENALTY_TEST = 0.60
_PENALTY_TRIVIAL_SIZE_BYTES = 4000
_PENALTY_TRIVIAL_SYMBOL_CAP = 4
_PENALTY_TRIVIAL = 0.40


def _normalize(value: float, max_value: float) -> float:
    """Return ``value / max_value`` clamped to ``[0, 1]``; safe on 0."""
    if max_value <= 0:
        return 0.0
    return max(0.0, min(value / max_value, 1.0))


def score_file(
    parsed: Any,
    *,
    pagerank: float,
    betweenness: float,
    max_pagerank: float,
    max_betweenness: float,
    is_hotspot: bool,
) -> float:
    """Score a code file for ``file_page`` candidacy.

    ``parsed`` is a :class:`ParsedFile`. Files with zero symbols and no
    entry/hotspot signal return 0.0 — they will never be selected.
    """
    fi = parsed.file_info
    n_symbols = len(parsed.symbols)

    # Files with no symbols and no architectural signal are noise.
    if n_symbols == 0 and not fi.is_entry_point and not is_hotspot:
        return 0.0

    base = _normalize(pagerank, max_pagerank) + _normalize(betweenness, max_betweenness) * 0.5

    if fi.is_entry_point:
        base += _BONUS_ENTRY_POINT
    if is_hotspot:
        base += _BONUS_HOTSPOT
    if betweenness > 0 and not fi.is_entry_point:
        base += _BONUS_BETWEENNESS_BRIDGE
    if fi.path.endswith("__init__.py") and n_symbols >= 2:
        base += _BONUS_INIT_PY_RE_EXPORTER

    # Penalties.
    if fi.is_test:
        base *= _PENALTY_TEST
    if (
        n_symbols <= _PENALTY_TRIVIAL_SYMBOL_CAP
        and fi.size_bytes < _PENALTY_TRIVIAL_SIZE_BYTES
        and not fi.is_entry_point
    ):
        base *= _PENALTY_TRIVIAL

    return base


# -- Symbol score ------------------------------------------------------

# Kind weights — bias symbol spotlights toward functions/classes over
# variables/constants.
_KIND_WEIGHT = {
    "function": 1.0,
    "method": 0.9,
    "class": 1.0,
    "interface": 0.9,
    "struct": 0.8,
    "enum": 0.7,
    "type_alias": 0.5,
    "variable": 0.3,
    "constant": 0.4,
}


def score_symbol(symbol: Any, file_pagerank: float, max_pagerank: float) -> float:
    """Score a public symbol for ``symbol_spotlight`` candidacy."""
    if symbol.visibility != "public":
        return 0.0
    weight = _KIND_WEIGHT.get(str(symbol.kind), 0.5)
    return _normalize(file_pagerank, max_pagerank) * weight


# -- Module score ------------------------------------------------------


def score_module(*, size: int, cohesion: float, min_module_size: int) -> float:
    """Score a community/directory cluster for ``module_page`` candidacy.

    Communities below ``min_module_size`` score 0 — they fold into the
    parent directory's module page (or are dropped if there is no
    parent).
    """
    if size < min_module_size:
        return 0.0
    # Cohesion is typically [0, 1]. Multiply by log-ish size factor.
    return (size**0.5) * (0.5 + cohesion)


# -- SCC score ---------------------------------------------------------


def score_scc(*, cycle_size: int) -> float:
    """Score a strongly-connected component for ``scc_page`` candidacy.

    Two-file cycles are easy refactors and score low; large cycles
    score high because they are the most architecturally interesting.
    """
    if cycle_size <= 1:
        return 0.0
    return float(cycle_size)


# -- API contract score ------------------------------------------------


def score_api_contract(parsed: Any) -> float:
    """Score an API contract file. Bigger surface = higher score."""
    # Approximate: number of public symbols + a small file-size factor.
    n_public = sum(1 for s in parsed.symbols if s.visibility == "public")
    size_kb = max(1, parsed.file_info.size_bytes // 1024)
    return float(n_public) + min(size_kb / 10.0, 5.0)


# -- Infra score -------------------------------------------------------


def score_infra(parsed: Any) -> float:
    """Score an infrastructure file.

    Larger / more-referenced files (Dockerfile vs a one-line .env)
    score higher.
    """
    size_kb = max(1, parsed.file_info.size_bytes // 1024)
    name = Path(parsed.file_info.path).name
    # Boost canonical roots: a project's main Dockerfile/Makefile
    # should always rank above sibling override files.
    boost = 0.5 if name in {"Dockerfile", "Makefile", "GNUmakefile"} else 0.0
    return min(size_kb / 5.0, 5.0) + boost
