"""``/api/feedback``: forward in-app feedback from the OSS dashboard to the
hosted Repowise backend.

The self-hosted dashboard has no database or mail transport of its own, so the
local server acts as a thin, server-side relay: the browser POSTs here, and we
forward the message to the hosted feedback endpoint (which persists it and
emails the maintainers). The submission is tagged ``source="oss"`` and carries
the running server version so OSS feedback is distinguishable from hosted, and
triageable by version.

This is an explicit, user-initiated action (they typed a message and clicked
send), so it is not gated on the telemetry opt-out. Forwarding server-side
(rather than a direct browser call) keeps the web app talking only to its local
origin and sidesteps cross-origin restrictions on the hosted API.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from repowise.server import __version__
from repowise.server.deps import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"], dependencies=[Depends(verify_api_key)])

#: Hosted feedback sink. Persists the row and emails the maintainer list. No
#: localhost override: feedback is only useful when it reaches the maintainers.
_HOSTED_FEEDBACK_URL = "https://api.repowise.dev/feedback"

_CATEGORIES = frozenset({"ui_ux", "bug", "feature_request", "other"})


class FeedbackRequest(BaseModel):
    category: str = Field(..., description="One of ui_ux, bug, feature_request, other")
    message: str = Field(..., min_length=1, max_length=4000)
    email: str | None = Field(default=None, max_length=320)
    page_url: str | None = Field(default=None, max_length=2048)


class FeedbackResponse(BaseModel):
    ok: bool


@router.post("", response_model=FeedbackResponse)
async def submit_feedback(body: FeedbackRequest) -> FeedbackResponse:
    """Relay one feedback message to the hosted backend, tagged as OSS."""
    category = body.category if body.category in _CATEGORIES else "other"
    payload = {
        "category": category,
        "message": body.message.strip(),
        "email": (body.email or "").strip() or None,
        "page_url": body.page_url,
        "source": "oss",
        "client_version": __version__,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_HOSTED_FEEDBACK_URL, json=payload)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("feedback_forward_failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Couldn't reach the feedback service. Please try again.",
        ) from exc

    logger.info("feedback_forwarded category=%s has_email=%s", category, bool(payload["email"]))
    return FeedbackResponse(ok=True)
