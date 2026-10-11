"""Deterministic invariant reviewer for the generated knowledge graph.

A zero-LLM, zero-DB gate that validates the in-memory KG (layers, tour, node
summaries) against hard structural invariants and soft quality signals before it
is persisted. See :mod:`checks` for the individual ``check_*`` functions,
:mod:`runner` for the gate (:func:`run_review` / :func:`apply_review`), and
:mod:`findings` for the report types.
"""

from __future__ import annotations

from .findings import Finding, ReviewReport, Severity
from .runner import apply_review, run_review

__all__ = [
    "Finding",
    "ReviewReport",
    "Severity",
    "apply_review",
    "run_review",
]
