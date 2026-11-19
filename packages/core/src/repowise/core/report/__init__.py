"""Evidence reports built from the index: ``repowise report assessment``.

A report is one versioned JSON document (:mod:`.build`) of sections, each a
provider on the ``report-section`` seam (:mod:`.model`, :mod:`.sections`),
whose items keep the OpenSSF Scorecard check-record keys plus ``method`` and
``basis``. Renderers on the ``report-renderer`` seam (:mod:`.render`) turn
the document into JSON, Markdown or self-contained HTML.
"""

from __future__ import annotations

from repowise.core.report.model import (
    BASES,
    INCONCLUSIVE,
    SCHEMA_VERSION,
    ReportContext,
    Section,
    WorkspaceReportContext,
    check,
    report_sections,
)

__all__ = [
    "BASES",
    "INCONCLUSIVE",
    "SCHEMA_VERSION",
    "ReportContext",
    "Section",
    "WorkspaceReportContext",
    "check",
    "report_sections",
]
