"""Cross-run reuse of model-written pages (issue #1089).

A model page is reused from the prior run when it still matches on the
*subject* it documents: ``content_hash`` when both sides have one, else the
rendered prompt's ``source_hash`` under the same model. The prompt drifts run
to run (RAG context is rebuilt concurrently), so a prompt-only gate re-billed
every page on every full run; the subject digest stays put for an unchanged
file, group or repo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

import jinja2

from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.template import TEMPLATE_PROVIDER_NAME

from ..agent_digest import rejoin_questions
from ..models import compute_source_hash
from ..nav_titles import NAV_TITLE_KEY
from .prompts import SYSTEM_PROMPTS

if TYPE_CHECKING:
    from .core import PriorPage

# Bumped by hand when a change to the model-page reuse fingerprint must force
# a one-time regeneration of subject-keyed pages whose renderer bytes did not
# change: a prompt-template edit is picked up automatically (the template
# source is hashed), so this only covers the cases hashing cannot see, the
# same convention as ``STRUCTURAL_GENERATION_VERSION``.
MODEL_PAGE_GENERATION_VERSION = "1"


def files_digest(paths: Iterable[str], source_map: Mapping[str, bytes]) -> str:
    """``path:sha256(bytes)`` per file, sorted and joined: a page's file subject.

    A file missing from *source_map* hashes as empty (stable, not an error),
    so the key covers the files the prompt actually carried.
    """
    parts = {f"{p}:{hashlib.sha256(source_map.get(p) or b'').hexdigest()}" for p in paths}
    return "\x1f".join(sorted(parts))


def group_material(
    structural_key: str,
    members: Iterable[str] | None,
    source_map: Mapping[str, bytes] | None,
    parts: Iterable[Mapping[str, Any]] = (),
) -> str:
    """A module page's subject: its membership key plus its members' bytes.

    A map page is also written from how its parts' pages open, so those
    openings are part of its subject: a rewritten part regenerates the map
    instead of leaving it describing the old part. Empty without a
    ``structural_key``, which keeps the page on the prompt-hash gate.
    """
    if not structural_key:
        return ""
    material = f"{structural_key}|{files_digest(members or (), source_map or {})}"
    openings = "\x00".join(f"{p.get('path', '')}\x01{p.get('summary', '')}" for p in parts)
    if openings:
        material += "|parts:" + hashlib.sha256(openings.encode("utf-8")).hexdigest()
    return material


def model_reuse_key(generator: Any, material: str, page_type: str, template: str) -> str:
    """Stable reuse key for a model-written page's subject.

    ``material`` is the page's subject in a form that does not drift between
    runs: the documented file's byte hash, a group's sorted member list, the
    repo's name. It is folded with a renderer fingerprint (the resolved
    prompt-template source, through the Jinja loader so a custom style's
    override fingerprints itself, plus system prompt, language, style and
    :data:`MODEL_PAGE_GENERATION_VERSION`), so a template or style upgrade
    forces a one-time regen like the structural pages' ``render_key``.

    An empty *material* yields an empty key, which keeps the page out of
    subject-keyed reuse and falls back to the prompt-hash gate.
    """
    if not material:
        return ""
    env = generator._jinja_env
    try:
        loader = getattr(env, "loader", None)
        source = loader.get_source(env, template)[0] if loader else None
    except (jinja2.TemplateNotFound, OSError):
        source = None
    fingerprint = hashlib.sha256(
        "\x00".join(
            [
                MODEL_PAGE_GENERATION_VERSION,
                page_type,
                template,
                source or "",
                SYSTEM_PROMPTS.get(page_type, ""),
                generator._language or "en",
                generator._style.fingerprint,
            ]
        ).encode()
    ).hexdigest()
    return hashlib.sha256(f"{material}:{fingerprint}".encode()).hexdigest()


def reuse_prior_response(
    prior: PriorPage | None,
    *,
    model_name: str,
    reuse_content_hash: str,
    prompt: str,
) -> GeneratedResponse | None:
    """The prior run's response for this page when it may be reused, else None.

    *prompt* is the rendered prompt plus any salt, hashed only when the
    subject digest is missing on either side. A row stamped ``template`` is a
    stub, never prose: reusing it would present a placeholder as the finished
    page and, because stubs are kept out of the resume ledger, let the run
    claim the page was written when nothing wrote it.
    """
    if prior is None or prior.model_name != model_name:
        return None
    if prior.provider_name == TEMPLATE_PROVIDER_NAME:
        return None
    if reuse_content_hash and prior.content_hash:
        matched = prior.content_hash == reuse_content_hash
    else:
        # Pre-key pages, or a caller that could not derive a digest: the
        # gate's historical rendered-prompt comparison.
        matched = prior.source_hash == compute_source_hash(prompt)
    if not matched:
        return None
    # The stored body lost its questions to the digest and its ``Nav:`` line
    # to the page's metadata; the response it was split from carried both.
    content = rejoin_questions(prior.content, prior.digest)
    nav = prior.metadata.get(NAV_TITLE_KEY) if prior.metadata else None
    return GeneratedResponse(
        content=f"Nav: {nav}\n\n{content}" if isinstance(nav, str) and nav else content,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        usage={"reused_from_prior_run": True},
    )
