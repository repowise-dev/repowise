"""What the estate sections share: the missing-artifact item and the evidence wording."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import check


def missing_graph(name: str, method: str) -> list[dict[str, Any]]:
    """The one item a section gives when the workspace has no stored system graph."""
    return [
        check(
            name,
            reason=(
                "no system graph is stored for this workspace; 'repowise update --workspace' "
                "builds it once two or more member repos are indexed"
            ),
            method=method,
            basis="unresolved",
        )
    ]


def evidence_note(evidence: Any) -> str:
    """``""`` when the graph's evidence is measured, else ``"; <state> evidence (<codes>)"``.

    The reasons are spelled out once, in the estate summary's ``evidence`` item.
    """
    if evidence is None or evidence.state == "measured":
        return ""
    codes = ", ".join(r.code.replace("_", " ") for r in evidence.reasons)
    return f"; {evidence.state} evidence ({codes})"
