"""One-shot steering instructions for model-written pages (issue #2289).

A reader who gets a wrong page has one lever in the reader UI: ask for it again
with a sentence about what the page should cover. The instruction rides on that
single request and nothing persists it, which is the half of #2289 that keeps
stored ``human_notes`` display-only rather than retroactively promoting every
note a user had already written into a model instruction.

Two things live here, and they have to move together: the prompt block, and the
reuse digest. A steering instruction that changes the prompt without changing
the reuse key produces a page that reads exactly like the one the user
complained about, because the persistent reuse gate would hand back the stored
body (see ``reuse.py``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from html import escape

from repowise.core.generation.models import MAX_STEERING_CHARS, compute_page_id

# The page is written from a template that already states its sections, length
# and citation rules, and those are validated after the call. Steering is
# framed as a choice of emphasis so a model cannot read it as permission to
# replace that structure, which would trade one bad page for a rejected one.
_STEERING_HEADER = (
    "\n\n## Steering instruction\n"
    "The person who requested this page wrote the instruction below. It applies to "
    "this request only. Use it to decide what the page emphasizes and which parts of "
    "the code it explains. It does not replace the required sections, the length "
    "range, or the citation and formatting rules above; keep every one of those.\n\n"
)


def render_steering_prompt(text: str) -> str:
    """The prompt block for *text*, or ``""`` when there is nothing to say.

    The instruction is wrapped and escaped so a stray ``<`` in it cannot break
    the frame the surrounding prompt fragments use for their own blocks.
    """
    body = text.strip()
    if not body:
        return ""
    return f"{_STEERING_HEADER}<steering>\n{escape(body)}\n</steering>\n"


def steering_reuse_digest(reuse_content_hash: str, text: str) -> str:
    """Fold a steering instruction into a page's subject digest.

    The persistent reuse gate compares the subject digest and ignores the
    prompt when both sides carry one, so an unsteered digest would match the
    page the reader just asked to replace. Hashing the instruction in makes a
    steered request miss that gate by construction, while an empty *text*
    leaves the digest untouched so ordinary runs keep reusing.
    """
    body = text.strip()
    if not body:
        return reuse_content_hash
    return hashlib.sha256(
        f"{reuse_content_hash}\x00steering\x00{body[:MAX_STEERING_CHARS]}".encode()
    ).hexdigest()


def apply_steering(
    steering: Mapping[str, str],
    *,
    page_type: str,
    target_path: str | None,
    user_prompt: str,
    reuse_content_hash: str,
) -> tuple[str, str]:
    """Return *user_prompt* and *reuse_content_hash* as this page's instruction leaves them.

    Keyed on the page id, the same identity the reuse gate and the persist layer
    use, so a regenerate request naming ``module_page:src/core`` reaches the call
    that writes ``module_page:src/core`` and no other. A page with no target path
    has no id to match and comes back unchanged.

    Both values move together, which is why this returns a pair: the reuse gate
    compares the subject digest and ignores the prompt when both sides carry one,
    so steering that only edited the prompt would be handed the stored body the
    reader just asked to replace.
    """
    if target_path is None or not steering:
        return user_prompt, reuse_content_hash
    text = steering.get(compute_page_id(page_type, target_path), "").strip()
    if not text:
        return user_prompt, reuse_content_hash
    return user_prompt + render_steering_prompt(text), steering_reuse_digest(
        reuse_content_hash, text
    )
