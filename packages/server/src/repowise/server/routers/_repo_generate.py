"""Request bodies and validation for the ``/generate`` endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field


class GenerateSelectionBody(BaseModel):
    """Which pages a generate request targets.

    Two selection philosophies, kept distinct exactly as the CLI keeps them:

    - **Explicit**: ``all`` / ``unwritten`` / ``stale``, an explicit ``page_ids``
      list, or every page under a ``path_prefix`` — the caller names the pages.
    - **Ranked** (``kind="ranked"``): write the most important slice by the same
      importance model ``repowise init`` uses, sized by ``coverage_pct`` (a
      fraction in ``(0, 1]``; ``1.0`` == everything) or ``top_n`` (a target page
      count, not exact). The two are mutually exclusive.

    The two philosophies cannot be combined; :func:`_validate_generate_selection`
    enforces it with an actionable 400.
    """

    kind: Literal["all", "unwritten", "stale", "page_ids", "path_prefix", "ranked"] = "unwritten"
    page_ids: list[str] | None = None
    path_prefix: str | None = None
    # Ranked selection only.
    coverage_pct: float | None = None
    top_n: int | None = None


class GenerateRequestBody(BaseModel):
    """Body for the generate + estimate endpoints.

    ``cascade`` is optional: left unset it resolves to ``none`` for a ranked
    selection (the ranked set is already a coherent slice) and ``dependents`` for
    an explicit one, matching the CLI ``generate`` defaults.
    """

    selection: GenerateSelectionBody = Field(default_factory=GenerateSelectionBody)
    cascade: Literal["none", "dependents", "full"] | None = None
    style: str | None = None


def _reject_structural_page_ids(page_ids: list[str]) -> None:
    from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES

    structural = [pid for pid in page_ids if pid.split(":", 1)[0] not in MODEL_WRITTEN_PAGE_TYPES]
    if structural:
        raise HTTPException(
            status_code=400,
            detail=(
                "generate writes the concept layer only; these pages render "
                "from structure and refresh on update, not generate: " + ", ".join(structural)
            ),
        )


def _validate_ranked(sel: GenerateSelectionBody) -> None:
    has_coverage = sel.coverage_pct is not None
    has_top_n = sel.top_n is not None
    if has_coverage == has_top_n:
        raise HTTPException(
            status_code=400,
            detail="A ranked selection needs exactly one of coverage_pct or top_n.",
        )
    if has_coverage and not 0.0 < sel.coverage_pct <= 1.0:
        raise HTTPException(
            status_code=400,
            detail="coverage_pct must be a fraction in (0, 1] (0.2 == the top 20%, 1.0 == all).",
        )
    if has_top_n and sel.top_n <= 0:
        raise HTTPException(status_code=400, detail="top_n must be a positive number of pages.")
    if sel.page_ids is not None or sel.path_prefix is not None:
        raise HTTPException(
            status_code=400,
            detail="A ranked selection cannot also carry page_ids or path_prefix.",
        )


def _validate_generate_selection(sel: GenerateSelectionBody) -> None:
    """Reject an incoherent selection (see :class:`GenerateSelectionBody`) with a 400."""
    if sel.kind == "page_ids":
        _reject_structural_page_ids(sel.page_ids or [])

    if sel.kind == "ranked":
        _validate_ranked(sel)
    elif sel.coverage_pct is not None or sel.top_n is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "coverage_pct / top_n rank pages by importance and require "
                'selection kind "ranked", not "' + sel.kind + '".'
            ),
        )


def _reject_unknown_style(style: str, field: str) -> None:
    """400 naming ``field`` and listing the valid styles, when ``style`` is unknown."""
    from repowise.core.generation.styles import is_known_style, list_styles

    if not is_known_style(style):
        valid = ", ".join(s.name for s in list_styles())
        raise HTTPException(
            status_code=400, detail=f"Unknown {field} '{style}'. Valid styles: {valid}."
        )


def _validate_generate_style(style: str | None) -> None:
    """Reject an unknown wiki style with a 400 listing the valid ones."""
    if style is not None:
        _reject_unknown_style(style, "style")


def _generate_job_config(body: GenerateRequestBody) -> dict:
    """Build the executor's job config from a validated request body."""
    selection: dict = {"kind": body.selection.kind}
    if body.selection.kind == "page_ids":
        selection["page_ids"] = body.selection.page_ids or []
    elif body.selection.kind == "path_prefix":
        selection["path_prefix"] = body.selection.path_prefix
    elif body.selection.kind == "ranked":
        if body.selection.coverage_pct is not None:
            selection["coverage_pct"] = body.selection.coverage_pct
        if body.selection.top_n is not None:
            selection["top_n"] = body.selection.top_n
    config: dict = {"mode": "generate", "selection": selection, "cascade": body.cascade}
    if body.style is not None:
        config["style"] = body.style
    return config
