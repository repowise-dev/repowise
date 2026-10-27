"""The finding status vocabulary, shared by the routes that read and write it."""

from __future__ import annotations

from fastapi import HTTPException

ALLOWED_STATUSES = frozenset({"open", "acknowledged", "resolved", "false_positive"})

STATUS_FILTER_DESCRIPTION = (
    "Comma-separated statuses (open | acknowledged | resolved | false_positive), "
    "or 'all'. Defaults to open work."
)


def parse_status_filter(value: str) -> str:
    """Validate a status filter, or 400.

    Unvalidated, a typo returns ``200 []`` — indistinguishable from a filter
    that genuinely matches nothing, which is the harder bug to notice.
    """
    wanted = [v.strip() for v in value.split(",") if v.strip()]
    if not wanted:
        return "open"
    if wanted == ["all"]:
        return "all"
    unknown = sorted(set(wanted) - ALLOWED_STATUSES)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status: {', '.join(unknown)}. Allowed: {', '.join(sorted(ALLOWED_STATUSES))}, all",
        )
    return ",".join(wanted)
