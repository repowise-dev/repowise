"""Shields-compatible code-health badge (JSON endpoint + self-rendered SVG)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.grading import band_for
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.schemas import HealthBadgeResponse

from ._router import router

# Shields-compatible band colors. Named colors for the JSON endpoint (shields
# resolves them) + hexes for the self-rendered SVG so it matches without a
# round-trip to img.shields.io.
_BADGE_COLOR_NAME: dict[str, str] = {
    "healthy": "brightgreen",
    "warning": "yellow",
    "alert": "red",
    "unknown": "lightgrey",
}
_BADGE_COLOR_HEX: dict[str, str] = {
    "brightgreen": "#4c1",
    "yellow": "#dfb317",
    "red": "#e05d44",
    "lightgrey": "#9f9f9f",
}


def _badge_fields(average_health: float | None) -> tuple[str, str, str, str]:
    """Return ``(label, message, color_name, band)`` for the health badge."""
    if average_health is None:
        return "health", "no data", _BADGE_COLOR_NAME["unknown"], "unknown"
    band = band_for(float(average_health))
    return "health", f"{average_health:.1f}/10", _BADGE_COLOR_NAME[band], band


def _render_badge_svg(label: str, message: str, color_name: str) -> str:
    """Render a flat shields-style SVG so the badge needs no external service.

    Char-width estimate matches shields' Verdana ~7px/char heuristic; exact
    pixel fidelity isn't needed for a README badge.
    """
    hex_color = _BADGE_COLOR_HEX.get(color_name, "#9f9f9f")
    lw = len(label) * 7 + 10
    mw = len(message) * 7 + 10
    total = lw + mw
    lx = lw * 10 // 2
    mx = (lw + mw // 2) * 10
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'role="img" aria-label="{label}: {message}">'
        f"<title>{label}: {message}</title>"
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{hex_color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="110">'
        f'<text x="{lx}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" '
        f'textLength="{(lw - 10) * 10}">{label}</text>'
        f'<text x="{lx}" y="140" transform="scale(.1)" textLength="{(lw - 10) * 10}">{label}</text>'
        f'<text x="{mx}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" '
        f'textLength="{(mw - 10) * 10}">{message}</text>'
        f'<text x="{mx}" y="140" transform="scale(.1)" textLength="{(mw - 10) * 10}">{message}</text>'
        f"</g></svg>"
    )


async def _badge_average_health(session: AsyncSession, repo_id: str) -> float | None:
    """The one number these endpoints render.

    These are README-embedded and public, so they get hit by anyone loading the
    README — and they were reading the entire health dataset to print ``7.5/10``.
    ``get_health_summary`` hydrates every per-file metric row, runs the grouped
    deduction aggregate for a ranking no badge shows, scans the whole findings
    table for per-dimension counts, and loads the graph's language map, all to
    have one float taken off the result. ``get_average_health`` computes that
    float and nothing else, from the same rows with the same weighting.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    avg = await crud.get_average_health(session, repo_id)
    # A repo with no metrics has always badged as a perfect 10.0 here, because
    # ``get_health_summary`` returns 10.0 for an empty table. Preserved rather
    # than quietly corrected: the crud helper reports "unmeasured" honestly as
    # ``None``, and mapping that to 10.0 is this endpoint's existing contract.
    return 10.0 if avg is None else avg


@router.get("/api/repos/{repo_id}/health/badge.json", response_model=HealthBadgeResponse)
async def health_badge_json(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Shields.io endpoint-badge payload (color + ``N.N/10`` score, no letter).

    Embed via ``https://img.shields.io/endpoint?url=<this-url>``.
    """
    avg = await _badge_average_health(session, repo_id)
    label, message, color, band = _badge_fields(avg)
    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": color,
        "band": band,
    }


@router.get("/api/repos/{repo_id}/health/badge.svg")
async def health_badge_svg(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Self-rendered flat SVG health badge (no external service round-trip)."""
    avg = await _badge_average_health(session, repo_id)
    label, message, color, _band = _badge_fields(avg)
    svg = _render_badge_svg(label, message, color)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "max-age=300, public"},
    )
