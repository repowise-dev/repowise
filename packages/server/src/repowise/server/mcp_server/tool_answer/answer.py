"""MCP Tool: get_answer — RAG-style synthesis over the wiki layer.

Single-call retrieval + LLM synthesis. Replaces the agent's multi-turn
search → context → read loop with one tool call that returns:

    {
      "answer":            str   — 2–5 sentence synthesised answer
      "citations":         list  — file paths backing the answer
      "confidence":        str   — "high" | "medium" | "low"
      "fallback_targets":  list  — top retrieval hits the agent should Read
                                   to verify (always present)
      "retrieval":         list  — raw top-N hits with snippets
      "symbol_bodies":     list  — full live body of each question-named
                                   definition (collapses the get_symbol
                                   follow-up); present only when the answer
                                   names a function/method/class that was
                                   hydrated
      "episodes":          list  — at most one dated fact recorded about this
                                   checkout that bears on the question, served
                                   beside the answer and never in place of it;
                                   present only when its scope intersects the
                                   answer's and confidence is below high (see
                                   ``episodes``)
      "more_definitions":  list    only on an answer-by-union (homonym) reply
                                   whose bodies overflowed the char budget:
                                   {file, name, line, symbol_id, hint} entries
                                   the agent fetches with get_symbol, not Read
    }

Answer-by-union: when the question names a symbol with N>=2 definitions no
qualifier disambiguates (``_severity_for`` x 4), the tool returns the UNION of
their bodies in ``symbol_bodies`` (grounding="exact_symbol", confidence="high")
rather than a best_guesses pointer list (the pointer list is what triggers the
agent's get_symbol/get_context drill). A qualified miss (``Parent.leaf`` matching
no def) returns not-found instead of guessing a same-named symbol elsewhere.

When no LLM provider is configured, the tool degrades to retrieval-only
mode (returns ranked hits + snippets, confidence="low") so C1 / index-only
deployments still benefit from the structured single-call shortcut.

This module is the orchestrator only: the retrieval re-rankers, symbol
hydration, provider resolution, confidence predicate, tuning constants, and
prompts live in sibling modules (``retrieval``, ``symbols``, ``synthesis``,
``confidence``, ``config``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
import os
import time
from pathlib import Path

from sqlalchemy import delete, select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import AnswerCache
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._answer_context import (
    _MAX_CHARS_PER_HIT_EXCERPT,
)
from repowise.server.mcp_server._answer_context import (
    build_context_block as _build_context_block_v2,
)
from repowise.server.mcp_server._answer_context import (
    build_structured_prelude as _build_structured_prelude,
)
from repowise.server.mcp_server._answer_context import (
    fetch_relevant_decisions as _fetch_relevant_decisions,
)
from repowise.server.mcp_server._answer_context import (
    is_mechanism_question as _is_mechanism_question,
)
from repowise.server.mcp_server._answer_context import (
    is_why_question as _is_why_question,
)
from repowise.server.mcp_server._answer_pipeline import (
    apply_pagerank_bias as _apply_pagerank_bias,
)
from repowise.server.mcp_server._answer_pipeline import (
    degraded_legs as _degraded_legs,
)
from repowise.server.mcp_server._answer_pipeline import (
    demote_noise_hits as _demote_noise_hits,
)
from repowise.server.mcp_server._answer_pipeline import (
    expand_via_graph as _expand_via_graph,
)
from repowise.server.mcp_server._answer_pipeline import (
    expand_via_parent_page as _expand_via_parent_page,
)
from repowise.server.mcp_server._answer_pipeline import (
    hybrid_retrieve as _hybrid_retrieve,
)
from repowise.server.mcp_server._answer_pipeline import hydrate_hits as _hydrate_hits
from repowise.server.mcp_server._answer_pipeline import (
    retrieval_legs as _retrieval_legs,
)
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
from repowise.server.mcp_server._flow_path import expand_via_flow_path as _expand_via_flow_path
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    filter_dicts_by_key,
    is_excluded,
)
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._neighbor_rerank import (
    expand_via_neighbor_rerank as _expand_via_neighbor_rerank,
)
from repowise.server.mcp_server._symbol_lookup import (
    bare_name,
    parse_symbol_id,
    resolve_symbol_rows,
)
from repowise.server.mcp_server.tool_answer.confidence import (
    _answer_is_hedged,
    _frame_term_grounding,
    _has_unqualified_exclusivity_over_truncated,
    _is_value_question,
    _ungrounded_numbers,
    implicated_withheld_symbols,
)
from repowise.server.mcp_server.tool_answer.config import (
    _AGREEMENT_RANK_GAP,
    _AGREEMENT_TOP_RANK_MAX,
    _ANSWER_CACHE_TTL_DAYS,
    _ANSWER_SCHEMA_VERSION,
    _DOMINANCE_RATIO,
    _ENRICH_TOP_N_HITS,
    _GATED_EXCERPT_CHARS,
    _GATED_RETURN_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _INLINE_BODY_MAX_LINES,
    _INLINE_BODY_MAX_SYMBOLS,
    _PAGE_EXCERPT_HITS,
    _SYMBOL_AGREEMENT_TOP_RANK_MAX,
    _SYSTEM_PROMPT,
    _USER_TEMPLATE,
)
from repowise.server.mcp_server.tool_answer.data_shape import (
    _is_data_shape_question,
    mine_data_shape,
)
from repowise.server.mcp_server.tool_answer.episodes import (
    attach_episode as _attach_episode,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    _CANDIDATE_LIMIT,
    _apply_domain_penalty,
    _attach_page_excerpts,
    _candidate_justification,
    _intersection_boost,
    _rerank_by_coverage,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    serialize_candidates as _serialize_candidates,
)
from repowise.server.mcp_server.tool_answer.retrieval import (
    serialize_hits as _serialize_hits,
)
from repowise.server.mcp_server.tool_answer.symbols import (
    _anchor_symbol_hits,
    _concept_anchor_hits,
    _extract_question_identifiers,
    _extract_value_answer,
    _hydrate_candidate_defines,
    _hydrate_symbols_for_hits,
    _read_repo_text,
    _read_symbol_source,
    build_homonym_union_bodies,
    is_symbol_lookup_question,
    union_defers_to_synthesis,
    withheld_definitions,
)
from repowise.server.mcp_server.tool_answer.synthesis import (
    _hash_question,
    _resolve_provider_for_answer,
    synthesize,
)

_log = logging.getLogger("repowise.mcp.answer")

# The excerpt fetch and the prompt formatter each cap page content, and they
# live in different modules — which is how the formatter came to discard more
# than half of every excerpt the fetch had paid a database round-trip for.
# Checked here, at import, because this is the only module that sees both.
if _MAX_CHARS_PER_HIT_EXCERPT < _GATED_EXCERPT_CHARS:
    raise RuntimeError(
        "get_answer would truncate the page content it fetches: the prompt "
        f"formatter caps an excerpt at {_MAX_CHARS_PER_HIT_EXCERPT} chars "
        f"while the fetch asks for {_GATED_EXCERPT_CHARS}. Raise "
        "_MAX_CHARS_PER_HIT_EXCERPT or lower _GATED_EXCERPT_CHARS."
    )

# Always-synthesize flag. Default ON: synthesis runs for every retrieval and the
# post-synthesis grading cascade demotes confidence instead of the tool
# abstaining, so coverage matches a research assistant that answers every
# question (the pre-synthesis dominance gate abstained on ~58%). Set to a falsey
# value (0/off/false/no) to restore the legacy abstain-on-ambiguous behaviour.
_ALWAYS_SYNTHESIZE_ENV = "REPOWISE_ANSWER_ALWAYS_SYNTHESIZE"
_AGREEMENT_CONFIDENCE_ENV = "REPOWISE_ANSWER_AGREEMENT_CONFIDENCE"
# The keyless-only half of that signal (FTS + symbol in place of FTS + vector,
# which a keyless index can never produce). Its own flag on purpose: the pair
# above was tuned against the 99-question eval, whereas this substitutes a leg
# the fusion already prices at a third weight, so it must be reversible and
# A/B-able WITHOUT also switching off the measured half.
_SYMBOL_AGREEMENT_ENV = "REPOWISE_ANSWER_SYMBOL_AGREEMENT"
# Keep the exact_symbol union fast path from hijacking a "how does X work"
# mechanism question, whose real answer often lives in a different file than the
# named symbol's body.
_UNION_MECHANISM_DEFER_ENV = "REPOWISE_ANSWER_UNION_MECHANISM_DEFER"
# Require the answer's central named mechanism symbol to be grounded in served
# source before a mechanism/how answer may be stamped high.
_CLAIM_SUPPORT_GATE_ENV = "REPOWISE_ANSWER_CLAIM_SUPPORT_GATE"
# Let strong answer-grounding earn "high" on a non-dominant retrieval (a rank-1
# hit buried in a sibling cluster), not only a clear numeric dominance margin.
_EARN_HIGH_GROUNDING_ENV = "REPOWISE_ANSWER_EARN_HIGH_GROUNDING"


def _flag_on(env_name: str) -> bool:
    """A REPOWISE_* feature flag: on by default, off for {0,false,no,off}."""
    return os.environ.get(env_name, "").strip().lower() not in {"0", "false", "no", "off"}


# Test/eval hook: skip the answer cache entirely (both read and write). The
# cache keys on (repo, question) only — not on feature-flag state — so an A/B
# eval that flips the REPOWISE_ANSWER_* flags between arms would otherwise read
# the first arm's cached answers. Off by default; set truthy in the eval harness.
_DISABLE_CACHE_ENV = "REPOWISE_ANSWER_DISABLE_CACHE"


def _cache_disabled() -> bool:
    return os.environ.get(_DISABLE_CACHE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


# Opt-in: strip re-read evidence from high-confidence answers. A high answer's
# contract is "cite this, do not re-read the source" — so the symbol bodies,
# quotes, flow_path, and candidate evidence are payload the consumer was told it
# does not need, re-dragged through the agent's context every turn. Dropping them
# at high (and ONLY at high, and ONLY for mainline synthesis) shrinks that
# payload. Off by default; only safe once high-confidence answers are reliable
# enough that the prose + citation suffices, so the agent rarely needs a dropped
# body (else it calls get_symbol and adds a round-trip).
#
# Two carve-outs keep the evidence where it IS the answer: grounded fast paths
# (their inlined body is the whole answer) and why-questions — a "because X" is
# justified by exactly the code_rationale / quotes this strips, so a lean
# why-answer loses the grounding its rationale stands on.
_LEAN_HIGH_ENV = "REPOWISE_ANSWER_LEAN_HIGH"

# Re-read evidence stripped from a lean high answer. NOT stripped: answer,
# citations, confidence, retrieval_quality, fallback_targets, note, _meta.
_LEAN_HIGH_DROP_KEYS = ("symbol_bodies", "quotes", "flow_path", "best_guesses", "code_rationale")


def _lean_high() -> bool:
    return os.environ.get(_LEAN_HIGH_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _apply_lean_high(payload: dict, question: str) -> dict:
    """Strip re-read evidence from a mainline high-confidence answer, in place.

    No-op unless the flag is on and confidence is high. Two carve-outs keep the
    body where it IS the answer: grounded fast paths (extracted / exact_symbol /
    symbol_body / data_shape, which carry a ``grounding`` key) and why-questions
    (whose rationale is grounded in the stripped evidence — see module note).
    """
    if not _lean_high() or payload.get("confidence") != "high" or payload.get("grounding"):
        return payload
    if _is_why_question(question):
        return payload
    for k in _LEAN_HIGH_DROP_KEYS:
        payload.pop(k, None)
    return payload


def _always_synthesize() -> bool:
    """Whether to always synthesize (default) or keep the legacy abstain gate."""
    return os.environ.get(_ALWAYS_SYNTHESIZE_ENV, "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _agreement_confidence_enabled() -> bool:
    """Whether retriever-agreement lifts confidence (default) or pure ratio rules.

    A falsey value restores the exact prior RRF-compressed ratio/gap behaviour,
    for A/B measurement and instant reversibility.
    """
    return os.environ.get(_AGREEMENT_CONFIDENCE_ENV, "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _symbol_agreement_enabled() -> bool:
    """Whether a keyless index may read agreement from FTS + the symbol leg.

    Independently reversible from :func:`_agreement_confidence_enabled`, which
    governs the measured FTS + vector pair. Turning this off restores the state
    where a keyless index simply cannot earn the agreement lift.
    """
    return os.environ.get(_SYMBOL_AGREEMENT_ENV, "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _agreement_dominant(hits: list[dict], *, vector_leg_keyless: bool = False) -> bool:
    """True when the top hit is the confident pick by retriever AGREEMENT.

    RRF fusion compresses scores: a page both retrievers rank #1 barely
    outscores one they rank #2 (ratio ~1.017), so the numeric dominance ratio
    calls the *most* confident retrieval "non-dominant" and demotes it. This
    reads the per-source ranks instead: when two retrievers put the SAME page at
    (or within a rank of) the top, that consensus is a stronger ground-truth
    signal than any RRF score margin.

    Conservative. Requires the top hit to be found by BOTH retrievers near the
    top of each, to rank no lower than the runner-up in either source, and the
    runner-up to be meaningfully weaker (found by only one retriever, or clearly
    lower-ranked in a source). Otherwise returns False and the caller falls back
    to the pure ratio/gap gate. Agreement can only LIFT — the demotion gates
    still apply.

    ``vector_leg_keyless`` swaps the vector leg for the symbol leg. On an index
    with no semantic vectors the vector leg is skipped outright, so ``_vec_rank``
    is never written for any question and a fixed FTS+vector pair makes this
    signal permanently unreachable — every keyless answer is then graded by the
    pure ratio gate, which is exactly the gate this function exists because it
    mis-reads. The symbol leg runs on every index and records ``_sym_rank``.

    **The caller must pass the retrieval leg's own status, not infer it from
    the hits.** By the time this runs, ``hits`` is capped to the top 5 out of a
    much larger fused pool, so "no hit carries a ``_vec_rank``" is *not*
    evidence the leg was skipped: a keyed index whose vector leg timed out,
    errored, was scope-filtered, or was simply outranked by five FTS-and-symbol
    hits presents identically. Substituting on that inference would fire exactly
    when evidence is weakest and manufacture "high" confidence from it.

    The symbol pair is held to a stricter rank than the vector pair. FTS and the
    symbol leg are not independent — the wiki page FTS indexes contains the
    public symbol table the symbol leg matches on — so their agreeing is closer
    to one lexical match observed twice than to two retrievers concurring. The
    fusion beside this already prices that leg at a third of the others'
    (``_SYMBOL_LEG_RRF_K`` 180 vs ``_RRF_K`` 60, after a same-named React
    component displaced the right file on the 99-question eval). Requiring an
    exact rank-0 tie keeps the weaker signal from carrying the stronger claim.

    Note the consequence: at ``top_rank_max = 0`` the runner-up comparison below
    can no longer reject anything, because two hits cannot share rank 0 within
    one leg. The symbol pair therefore reduces exactly to "the top hit is #1 in
    FTS and #1 in the symbol leg", which is the intended rule; the shared gap
    check is retained for the vector pair, where it does constrain.
    """
    if len(hits) < 2:
        return False
    if vector_leg_keyless:
        second_field = "_sym_rank"
        top_rank_max = _SYMBOL_AGREEMENT_TOP_RANK_MAX
    else:
        second_field = "_vec_rank"
        top_rank_max = _AGREEMENT_TOP_RANK_MAX
    top = hits[0]
    top_a = top.get("_fts_rank")
    top_b = top.get(second_field)
    # Top must be a consensus pick: found by both retrievers, near the top of
    # each. A one-retriever top hit is exactly the ambiguous case we must NOT
    # lift.
    if top_a is None or top_b is None:
        return False
    if top_a > top_rank_max or top_b > top_rank_max:
        return False
    second = hits[1]
    sec_a = second.get("_fts_rank")
    sec_b = second.get(second_field)
    # Runner-up found by only one retriever → the consensus top clearly wins.
    if sec_a is None or sec_b is None:
        return True
    # Runner-up found by both: the top must rank at least as high in BOTH
    # sources (no source disagrees) and strictly ahead in at least one.
    if top_a <= sec_a and top_b <= sec_b:
        return (sec_a - top_a) >= _AGREEMENT_RANK_GAP or (
            sec_b - top_b
        ) >= _AGREEMENT_RANK_GAP
    return False


def _build_best_guesses(hits: list[dict]) -> list[dict]:
    """Decision-shaped candidate list: per-file justification, score, excerpt.

    The evidence an ambiguous-retrieval reply carries so the agent can pick ONE
    file to verify instead of skimming five. Shared by the legacy abstain path
    and the always-synthesize low/medium fold-in.
    """
    return [
        {
            "file": h.get("target_path"),
            "why_relevant": _candidate_justification(h),
            "score": round(h.get("score", 0.0), 3),
            # Absent rather than null. It was `null` in all six wire samples
            # measured 2026-08-11 — a penalty applies to a minority of hits, so
            # the common row paid 22 characters to say nothing happened.
            **({"domain_penalty": h["_domain_penalty"]} if h.get("_domain_penalty") else {}),
            **({"excerpt": h["excerpt"]} if h.get("excerpt") else {}),
        }
        for h in hits[:_GATED_RETURN_HITS]
        if h.get("target_path")
    ]


def _trim_served_payload(payload: dict) -> dict:
    """Every size cut that runs on the way OUT, on both the fresh and cache paths.

    Serve-time rather than build-time, and that is the whole point. A cut
    applied where the payload is assembled reaches only fresh answers: a cache
    row written by an older build keeps the old shape until
    ``_ANSWER_SCHEMA_VERSION`` moves, and bumping that invalidates every user's
    answer cache — re-synthesis, i.e. real provider spend — to change the size
    of a block. Measured: after capping ``candidates`` at build time only, a
    re-measured ``get_answer`` came back byte-identical, because the tree's
    cache answered. Trimming on the way out fixes old and new rows alike and
    costs nobody a re-synthesis.

    Anything that only REMOVES redundancy belongs here. Anything that changes
    what an answer says does not, and still owes a schema bump.
    """
    _cap_candidates(payload)
    _drop_duplicated_guess_excerpts(payload)
    return payload


def _cap_candidates(payload: dict) -> dict:
    """Hold ``candidates`` to :data:`_CANDIDATE_LIMIT` rows on the way out."""
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and len(candidates) > _CANDIDATE_LIMIT:
        payload["candidates"] = candidates[:_CANDIDATE_LIMIT]
    return payload


def _drop_duplicated_guess_excerpts(payload: dict) -> dict:
    """Drop ``best_guesses[].excerpt`` where ``retrieval[]`` already carries it.

    Both blocks slice the same 1,500-character page excerpt for the same file,
    and when both are present the guess copy is byte-for-byte redundant —
    measured at 4,714 characters, 21.3% of one low-confidence payload, 3 of 3
    guesses duplicated.

    **Conditional, and the condition matters.** ``retrieval`` is
    confidence-gated and shrinks to nothing as the prose gets more
    trustworthy; the legacy abstain path ships ``retrieval: []`` outright. On
    those responses the guess excerpt is the only content in the payload, not a
    duplicate of anything, and a sample measured here carried 4,667 characters
    of it. So the drop is keyed on the duplicate actually being present, which
    makes it lossless rather than merely cheap — and keeps every ``excerpt``
    mentioned by ``note`` / ``next_action_hint`` on the paths that mention it.
    """
    guesses = payload.get("best_guesses")
    if not guesses:
        return payload
    # Substring, not equality: the two blocks cut their slabs independently and
    # the retrieval one is the longer of the two where they differ.
    carried = [r["excerpt"] for r in (payload.get("retrieval") or []) if r.get("excerpt")]
    if not carried:
        return payload
    for guess in guesses:
        excerpt = guess.get("excerpt")
        if excerpt and any(excerpt in c for c in carried):
            del guess["excerpt"]
    return payload


def _json_default(obj):
    """Serialize the non-JSON types retrieval hits carry (``_sources`` sets).

    Before this fallback existed, EVERY cache write failed on the sets the
    hybrid retriever attaches to hits — silently, under the old blanket
    suppress. The cache never stored a single post-hybrid-pipeline answer.
    """
    if isinstance(obj, (set, frozenset)):
        # str-key the sort: a serializer whose whole job is "never fail the
        # cache write" must not raise TypeError on a mixed-type set.
        return sorted(obj, key=str)
    return str(obj)


def _cache_entry_expired(created_at) -> bool:
    """True when an answer-cache row is older than the hard TTL."""
    if created_at is None:
        return False
    from datetime import UTC, datetime, timedelta

    ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts) > timedelta(days=_ANSWER_CACHE_TTL_DAYS)


def _is_readable_path(target: str) -> bool:
    """Whether a fallback_target is a file the agent can actually Read.

    Non-file graph nodes (community/SCC nodes, architectural layers) can ride in
    on retrieval hits with a ``target_path`` like ``"scc-607"`` or
    ``"layer:application"``: internal ids with no path separator and no file
    extension. An agent handed one in ``fallback_targets`` will try to Read it and
    dead-end, so keep only path-shaped entries (2026-07-10 dogfood finding).
    """
    t = (target or "").strip()
    if not t:
        return False
    if "/" in t or "\\" in t:
        return True
    dot = t.rfind(".")
    ext = t[dot + 1 :] if dot != -1 else ""
    return bool(ext) and ext.isalnum() and len(ext) <= 6


async def _first_resolvable_id(
    ids: list[str], ctx, repository, exclude_spec
) -> str | None:
    """The first id ``get_symbol`` would actually answer, or None if none would.

    The note and ``next_action_hint`` below interpolate a ``symbol_id`` straight
    out of the scanner. The scanner is a regex over source lines, so it can name
    something that is not a symbol, and an answer must never advertise a next
    action that dead-ends.

    ``get_symbol`` has THREE outcomes and only one of them is a failure: an
    indexed row, a live-grep fallback when the name is in the file but not the
    index, and nothing at all. Treating the live-grep case as unresolvable is a
    mistake already made once while measuring this — it read mui as "90%
    unresolvable" when the truth was "90% no index row, all still usable" — so
    the live-grep outcome counts as resolved here too.

    Deliberately conservative in the other direction: an id whose file cannot be
    read is KEPT, because an unreadable file is absence of evidence, not
    evidence the id is fabricated. Only a positive read that does not contain
    the name, with no index row either, disqualifies one.
    """
    repo_root = Path(str(ctx.path)) if getattr(ctx, "path", None) else None
    for sid in ids:
        file_part, name_part = parse_symbol_id(sid)
        if not file_part or not name_part:
            continue
        if is_excluded(file_part, exclude_spec):
            # get_symbol refuses excluded paths outright, index row or not.
            continue
        text = _read_repo_text(repo_root, file_part)
        if text is None or bare_name(name_part) in text:
            return sid
        # The name is provably absent from the live file, so the live-grep leg
        # cannot fire. An indexed row is the only way get_symbol still answers.
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                if await resolve_symbol_rows(session, repository.id, sid):
                    return sid
    return None


def _gather_code_rationale(ctx, hits: list[dict], fallback_targets: list[str], question: str):
    """Mine in-code rationale comments for a low-confidence answer.

    The wiki/decision corpus failed to ground the question; the "why" may be a
    plain code comment instead (the unbiased A/B's one durable loss). Scan the
    already-relevant files — anchored/matched-symbol files lead, with a near-
    line boost on their definition, then fallback_targets fill the rest — for
    comment blocks carrying a rationale marker overlapping the question.
    Best-effort: returns [] on any failure, never raises into the tool path.
    """
    repo_root = getattr(ctx, "path", None)
    if not repo_root:
        return []
    candidates: list[str] = []
    near_lines: dict[str, int] = {}
    for h in hits or []:
        path = h.get("target_path")
        if not path:
            continue
        # A concept-anchored file leads: it was selected precisely because its
        # comment explains the question, and the grep match line is the best
        # near-line boost we have.
        if h.get("_concept_anchored"):
            candidates.append(path)
            cl = h.get("_concept_near_line")
            if cl and path not in near_lines:
                near_lines[path] = cl
        for s in (h.get("_anchor_symbols") or []) + [
            s for s in (h.get("symbols") or []) if s.get("_matched")
        ]:
            candidates.append(path)
            sl = s.get("start_line")
            if sl and path not in near_lines:
                near_lines[path] = sl
    candidates.extend(p for p in (fallback_targets or []) if p)
    try:
        return _mine_rationale(repo_root, candidates, question, near_lines=near_lines)
    except Exception:  # best-effort enrichment, never break the response
        return []


def _drop_already_surfaced(rationale: list[dict], *surfaced: list[dict]) -> list[dict]:
    """Drop mined rationale comments already shown elsewhere in the response.

    The same comment can reach the payload twice — once as material already in
    the response (a ``symbol_bodies`` block whose body contains the comment, a
    quote, a line-ranged citation, or a legacy ``code_comment`` decision) and
    once as a live-mined ``code_rationale`` entry. Suppress the duplicate:
    drop any mined comment whose ``(path, line-range)`` overlaps an entry already
    surfaced. Entries without a ``(path, lines)`` pair are ignored.
    """
    occupied: list[tuple[str, int, int]] = []
    for entries in surfaced:
        for e in entries or []:
            path = e.get("path")
            lines = e.get("lines")
            if path and isinstance(lines, (list, tuple)) and len(lines) == 2:
                occupied.append((path, lines[0], lines[1]))
    if not occupied:
        return rationale
    kept: list[dict] = []
    for r in rationale:
        path = r.get("path")
        lines = r.get("lines")
        if (
            path
            and isinstance(lines, (list, tuple))
            and len(lines) == 2
            and any(p == path and not (lines[1] < s or lines[0] > e) for p, s, e in occupied)
        ):
            continue
        kept.append(r)
    return kept


def _gather_body_candidates(
    hits: list[dict], answer_text: str
) -> list[tuple[int, int, int, str, dict]]:
    """Rank the definitions to inline in ``symbol_bodies``, most-relevant first.

    Returns ``(tier, kind_rank, start_line, path, symbol)`` tuples, pre-sorted so
    the leading entries are the bodies the agent is most likely to want:

      * Tier 0 — the exact symbol the question named, resolved by symbol
        anchoring (survives the fuzzy hydration cap a parent class name floods).
      * Tier 1 — a question-matched hydrated symbol the answer names.

    Within a tier a function/method outranks a class container (so "explain the
    extract_all method of DecisionExtractor" serves extract_all, not the
    1,300-line class head), then document order. Only definitions the answer
    text actually names qualify; constants stay in ``quotes``.
    """
    candidates: list[tuple[int, int, int, str, dict]] = []
    for h in hits[:_ENRICH_TOP_N_HITS]:
        path = h.get("target_path")
        if not path:
            continue
        for s in h.get("_anchor_symbols") or []:
            name = s.get("name")
            if not name or name not in answer_text:
                continue
            kind = s.get("kind")
            kind_rank = 0 if kind in ("function", "method") else 1
            candidates.append((0, kind_rank, s.get("start_line") or 0, path, s))
        for s in h.get("symbols") or []:
            name = s.get("name")
            if not name or len(name) < 3 or not s.get("_matched"):
                continue
            if name not in answer_text:
                continue
            kind = s.get("kind")
            if kind not in ("function", "method", "class", "interface"):
                continue
            kind_rank = 0 if kind in ("function", "method") else 1
            candidates.append((1, kind_rank, s.get("start_line") or 0, path, s))
    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    return candidates


def _build_data_shape_payload(grounded: dict, t0: float, repository) -> dict:
    """Shape a grounded data-shape result into a get_answer response.

    ``grounded`` is :func:`mine_data_shape`'s return. Cite the exact source
    lines the fields were lifted from; a docstring shape is authoritative
    (confidence high, no verification Read), a usage-mined shape is medium.
    """
    ident = grounded["identifier"]
    fields = grounded["fields"]
    sources = grounded["sources"]
    also_accessed = grounded.get("also_accessed") or []
    citations = sorted({s["file"] for s in sources})
    field_list = ", ".join(f"`{f}`" for f in fields)
    doc_src = next((s for s in sources if s["kind"] == "docstring"), None)
    if grounded["grounding"] == "docstring":
        where = f"{doc_src['file']}:{doc_src['line']}" if doc_src else citations[0]
        if also_accessed:
            # The doc lists the declared shape, but consumers read alias key(s)
            # it omits (a legacy fallback). Surface them: telling the agent "no
            # Read needed" while hiding a key it must handle would be a
            # confidently-incomplete answer.
            alias_list = ", ".join(f"`{a['field']}`" for a in also_accessed)
            first_alias = also_accessed[0]
            answer = (
                f"Each entry in `{ident}` has {len(fields)} documented field(s): "
                f"{field_list} (documented shape at {where}). Consumers also read "
                f"{alias_list} as a fallback (e.g. {first_alias['file']}:"
                f"{first_alias['line']}) - an alias the docstring omits, so handle "
                f"{alias_list} too if you touch this."
            )
            note = (
                "Grounded in the documented field shape, plus alias key(s) "
                "consumers read beside a documented field that the docstring "
                "omits (see data_shape.also_accessed). The documented fields are "
                "authoritative; the aliases are real keys the code defends against."
            )
        else:
            answer = (
                f"Each entry in `{ident}` has {len(fields)} field(s): {field_list}. "
                f"This is the documented shape at {where}; cite it directly, no "
                "verification Read needed."
            )
            note = (
                "Grounded in the documented field shape mined from source (the "
                "quoted keys in the docstring/comment at the cited line). "
                "data_shape.sources lists every field's origin line."
            )
    else:
        first = sources[0]
        answer = (
            f"Each entry in `{ident}` is accessed with {len(fields)} key(s): "
            f"{field_list}. These are the keys consumers actually pull off the "
            f"parsed value (e.g. {first['file']}:{first['line']}); this is mined "
            "from usage, not a declared schema, so verify if you need the full set."
        )
        note = (
            "Grounded in the key accesses mined from consumer source (no "
            "documented shape was found). Medium confidence: these are the keys "
            "the code reads, which may be a subset of the stored fields."
        )
    payload: dict = {
        "answer": answer,
        "citations": citations,
        "confidence": grounded["confidence"],
        "grounding": "data_shape",
        "data_shape": {
            "identifier": ident,
            "fields": fields,
            "sources": sources,
            **({"also_accessed": also_accessed} if also_accessed else {}),
        },
        "fallback_targets": citations,
        "retrieval": [],
        "note": note,
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_answer_hint(grounded["confidence"], len(citations)),
            repository=repository,
            targets=citations,
        ),
    }
    return payload


def _with_candidates(payload: dict, resolved_pool: list[dict]) -> dict:
    """Attach the ranked shortlist to a payload that is about to be returned.

    ``get_answer`` has several early returns that fire *after* retrieval has
    run: the qualified-miss guard, answer-by-union, the value-extraction fast
    path, the legacy abstain, and both degraded paths. Each was written as a
    complete reply in its own terms — the union hands back every definition of
    the named symbol, the extractor hands back the literal assignment line —
    and each set ``retrieval`` to ``[]`` and returned.

    That is right about ``retrieval``, which is re-read evidence for a synthesised
    answer, and wrong about what the caller is left holding. ``resolved_pool``
    already exists at every one of these sites: the full ranked file list, built
    before the 5-hit synthesis cap, at no further cost. Discarding it means a
    caller whose question tripped one of these gates gets a narrower reply than
    one whose question did not, and gets it *because* we recognised their question
    more precisely. Measured on the 70 ContextBench dev instances, the gates that
    fire after retrieval account for 20 firings and 15 answers that named no gold
    file, every one of them with a ranked pool in hand.

    So the shortlist travels with every reply. This adds to a payload and takes
    nothing away: no gate stops firing, no predicate moves, and the special reply
    each gate exists to give is returned unchanged. It is deliberately NOT the
    fix of loosening a gate — finding A11 measured that at -6.4 recall@5.
    """
    candidates = _serialize_candidates(resolved_pool)
    if candidates:
        payload["candidates"] = candidates
    return payload


def _degraded_payload(
    *,
    reason: str,
    note: str,
    hits: list[dict],
    fallback_targets: list[str],
    repository,
    t0: float,
    resolved_pool: list[dict] | None = None,
) -> dict:
    """Shape a synthesis-less get_answer response.

    Both ways synthesis can be missing (no provider resolvable, the call
    failed) return the same payload: retrieval hits the agent can act on, plus
    a loud marker. ``degraded`` is mirrored into ``_meta`` because consumers
    read freshness and health signals from there. The failure path used to
    set only the top-level key, so a caller watching ``_meta`` saw a normal
    empty answer.

    ``answer`` states what the payload IS rather than being left empty. Only
    the synthesis step is missing here. Retrieval ran, ranked the corpus and
    succeeded, and its result is the most useful part of a normal reply. An
    empty ``answer`` beside it reads as a failed call rather than a partial
    one, and a reader who takes it at face value discards a working result and
    starts over. The sentence is assembled from what the payload actually
    carries, so it can never claim more than it has, and no prose about the
    question itself is invented, that being precisely the part that needs a
    provider.
    """
    served = len(hits)
    if served:
        summary = (
            f"No synthesized prose ({reason}), but retrieval succeeded and this "
            f"payload is usable: {served} ranked "
            f"{'hit' if served == 1 else 'hits'} in `retrieval`, the files to open "
            "in `fallback_targets`, and the wider ranked shortlist in `candidates`. "
            "Read those rather than starting a fresh search."
        )
    else:
        summary = (
            f"No synthesized prose ({reason}), and retrieval matched nothing for "
            "this question. Rephrase with an identifier or path from the codebase, "
            "or search directly."
        )

    return _with_candidates(
        {
            "answer": summary,
            "citations": [],
            "confidence": "low",
            "degraded": reason,
            "fallback_targets": fallback_targets,
            "retrieval": _serialize_hits(hits),
            "note": note,
            "_meta": {
                **_build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint("low", len(hits)),
                    repository=repository,
                    targets=fallback_targets,
                ),
                "degraded": reason,
            },
        },
        resolved_pool if resolved_pool is not None else hits,
    )


@mcp.tool()
async def get_answer(
    question: str,
    scope: str | None = None,
    repo: str | None = None,
) -> dict:
    """Synthesised answer with citations and a calibrated trust signal.

    The single entry point for questions: "how does X work" / "where is Y" /
    "why is Z". It runs the full hybrid retrieval internally (no prior
    search_codebase call needed) and answers in one round-trip.
    confidence=high is content-grounded (value + citation-source + frame
    gates): cite it directly, no verification Read needed. A "why" answer
    whose named mechanism is absent from the retrieved source is downgraded
    to medium (the rationale may be conflated). Low confidence returns
    best_guesses with one-line justifications instead of an empty answer.
    retrieval_quality separately rates the retrieval that fed synthesis.
    When the answer names a function/method/class, ``symbol_bodies`` carries
    its full live body — read that instead of a follow-up get_symbol.
    ``episodes``, when present, is a dated fact recorded about this checkout
    that bears on the question — evidence beside the answer, not a correction
    of it. Weigh it against the answer; ``still_true`` says how current it is.

    Args:
        question: developer question.
        scope: optional path-prefix filter (e.g. "src/pkg/").
        repo: usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_answer")

    t0 = time.perf_counter()
    ctx = await _resolve_repo_context(repo)
    exclude_spec = _get_exclude_spec(ctx.path)

    if not question or not question.strip():
        return {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "fallback_targets": [],
            "retrieval": [],
            "error": "question is required",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id

    # --- Data-shape fast path ----------------------------------------------
    # "what fields does each entry in <blob> contain" is answered by mining the
    # field set straight from source (a documented {...} shape, else consistent
    # key accesses) instead of gating to a best_guesses pointer list — the exact
    # payload that triggers the agent's Read/get_symbol drill. Runs before the
    # cache and retrieval: it's deterministic from live source, cheap, and reads
    # the field set directly (retrieval scatters across every file that touches
    # the blob and misses the one file that documents it). Returns None (falls
    # through) unless the fields are genuinely grounded, so it can never invent a
    # shape.
    ds_ids = _extract_question_identifiers(question)
    if _is_data_shape_question(question, ds_ids):
        grounded = await asyncio.to_thread(mine_data_shape, getattr(ctx, "path", None), ds_ids)
        if grounded is not None:
            return _build_data_shape_payload(grounded, t0, repository)

    # --- Cache lookup --------------------------------------------------------
    # Scope: ignore the (rare) `scope` argument in the cache key for now;
    # scoped queries are uncommon and including scope would balloon hit rate
    # variance. We hash on (repo_id, normalized_question) only.
    qhash = _hash_question(question)
    cache_disabled = _cache_disabled()
    cached = None
    if not cache_disabled:
        async with get_session(ctx.session_factory) as session:
            res = await session.execute(
                select(AnswerCache).where(
                    AnswerCache.repository_id == repo_id,
                    AnswerCache.question_hash == qhash,
                )
            )
            cached = res.scalar_one_or_none()
    if cached is not None:
        with contextlib.suppress(Exception):
            payload = _json.loads(cached.payload_json)
            # Schema bypass: payloads from a pre-rework code path don't carry
            # the fields the current consumer expects (retrieval_quality,
            # best_guesses, calibrated confidence). Returning them masks every
            # subsequent improvement until the cache happens to expire. Bypass
            # silently so the next write upgrades the row.
            cached_version = payload.get("_schema_version", 1)
            schema_stale = cached_version < _ANSWER_SCHEMA_VERSION
            # Bypass-on-hedged: if the cached answer hedged, the retrieval +
            # symbol pipeline has since been upgraded (question-aware symbol
            # promotion, source-body excerpts). Give synthesis another shot
            # with the new context rather than pinning the bad answer.
            hedged_cache = _answer_is_hedged(payload.get("answer", ""))
            # Bypass-on-empty: older versions cached gated (empty-answer)
            # payloads, so a retrieval miss got pinned until TTL and every
            # later improvement to the miss path was invisible. The write
            # side no longer caches empty answers; this read-side check
            # retires rows that predate that fix.
            empty_cache = not (payload.get("answer") or "").strip()
            # A row cached before exclude_patterns changed may reference a
            # now-excluded file — in its fields or its prose. Re-synthesize
            # rather than scrub the fields and leave the prose dangling.
            cached_paths = [
                *(payload.get("citations") or []),
                *(payload.get("fallback_targets") or []),
                # "path" is the serialized key; "target_path" survives in
                # rows cached before the clean retrieval view existed.
                *(h.get("path") or h.get("target_path") for h in (payload.get("retrieval") or [])),
                *(g.get("file") for g in (payload.get("best_guesses") or [])),
            ]
            excluded_cache = any(is_excluded(p, exclude_spec) for p in cached_paths)
            # Freshness: a row synthesised against a previous index may cite
            # moved code or stale values. The write path stamps the repo's
            # head commit into the persisted payload; a mismatch (or a row
            # past the hard TTL, for pre-stamping rows and gitless repos)
            # forces re-synthesis.
            current_commit = getattr(repository, "head_commit", None)
            cached_commit = payload.get("_indexed_commit")
            stale_commit = bool(
                cached_commit and current_commit and cached_commit != current_commit
            )
            expired = _cache_entry_expired(cached.created_at)
            if schema_stale:
                _log.info(
                    "Bypassing cache entry at schema v%s (current v%s)",
                    cached_version,
                    _ANSWER_SCHEMA_VERSION,
                )
            elif hedged_cache:
                _log.info("Bypassing hedged cache entry for re-synthesis")
            elif empty_cache:
                _log.info("Bypassing cached empty-answer (gated) entry")
            elif excluded_cache:
                _log.info("Bypassing cache entry referencing a now-excluded path")
            elif stale_commit:
                _log.info(
                    "Bypassing cache entry from commit %s (repo now at %s)",
                    cached_commit,
                    current_commit,
                )
            elif expired:
                _log.info("Bypassing cache entry past the %d-day TTL", _ANSWER_CACHE_TTL_DAYS)
            else:
                # Cache-internal fields never reach the consumer (response
                # keys must not start with "_" except _meta).
                payload.pop("_indexed_commit", None)
                payload.pop("_schema_version", None)
                payload["_meta"] = _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    cached=True,
                    hint=_answer_hint(
                        payload.get("confidence", "low"),
                        len(payload.get("retrieval", [])),
                    ),
                    repository=repository,
                    targets=[p for p in cached_paths if isinstance(p, str) and p],
                )
                _apply_lean_high(payload, question)
                _trim_served_payload(payload)
                # Serve-time, on this path as well as the fresh one: the
                # episode is read on every call and never cached into an
                # answer, so a disagreement cannot be frozen into a row and
                # served after the episode has been superseded.
                await _attach_episode(
                    payload,
                    question=question,
                    repo_path=getattr(ctx, "path", None),
                    repo_name=getattr(repository, "name", None),
                )
                return payload

    # --- Retrieval pipeline ------------------------------------------------
    # Stages live in ``_answer_pipeline`` so each can evolve without
    # rereading the orchestrator: hybrid retrieval (FTS + vector + RRF) →
    # hydration → coverage rerank → domain penalty → intersection boost →
    # PageRank bias → 1-hop graph expansion. The orchestrator only sequences
    # them and decides when to stop (cap at 5 for the response payload).
    hits = await _hybrid_retrieve(question, ctx)
    hits = await _hydrate_hits(hits, ctx, scope=scope)

    # Drop excluded files right after hydration (which attaches target_path) so
    # they never enter ranking, citations, or fallback_targets.
    hits = filter_dicts_by_key(hits, "target_path", exclude_spec)

    # Identifiers the question names explicitly — drives symbol anchoring
    # (below) and question-aware symbol promotion (during hydration).
    question_ids = _extract_question_identifiers(question)

    # Term-coverage re-rank before any graph-aware bias so conjunctive
    # matches survive the merge.
    hits = _rerank_by_coverage(hits, question)
    # Domain heuristic: down-weight cross-domain hits (e.g. UI files for a
    # clearly backend question). Cheap tie-breaker, never a hard filter.
    _apply_domain_penalty(hits, question)
    # Intersection-retrieval boost for relational questions (multi-entity).
    # Pages at the intersection of two split-FTS halves get a 2× bonus.
    with contextlib.suppress(Exception):
        await _intersection_boost(question, hits, ctx)
    # PageRank bias: nudge architecturally central files above peripheral
    # ones at the same retrieval score. Damped + normalised within the
    # candidate set so it's a tie-breaker, not a wholesale reordering.
    with contextlib.suppress(Exception):
        await _apply_pagerank_bias(hits, ctx)
    # Graph expansion: 1-hop walk from the top hits to rescue near-misses
    # where retrieval landed in the right module but on the wrong file
    # (consumer instead of orchestrator). Adds up to 3 neighbors with a
    # damped score, then re-sorts.
    with contextlib.suppress(Exception):
        hits = await _expand_via_graph(hits, ctx)
    # Re-filter: graph expansion can pull excluded neighbors back in (before the
    # cap, so an excluded neighbor can't occupy a top-5 slot).
    hits = filter_dicts_by_key(hits, "target_path", exclude_spec)
    # Symbol anchoring: when the question names an indexed function / method /
    # class, force its defining file into the candidate set as a dominant hit.
    # Fuzzy retrieval misses deep-path definitions even when the symbol is
    # indexed; this makes "explain X" one-shot-complete instead of degrading
    # to best_guesses on plausible-but-wrong neighbors.
    homonyms: dict = {"union": {}, "qualified_miss": []}
    if question_ids:
        with contextlib.suppress(Exception):
            _anchor_root = Path(str(ctx.path)) if getattr(ctx, "path", None) else None
            async with get_session(ctx.session_factory) as session:
                hits, homonyms = await _anchor_symbol_hits(
                    session,
                    repo_id,
                    question_ids,
                    hits,
                    repo_root=_anchor_root,
                    session_factory=ctx.session_factory,
                )
    # Concept anchoring: when a why/value question pins a literal number to a
    # described behaviour (no named symbol), grep source COMMENTS for the file
    # that justifies the number and anchor it as a dominant hit. Rescues the
    # retrieval-miss class where the rationale lives in a code comment fuzzy
    # retrieval did not rank.
    if _is_why_question(question) or _is_value_question(question):
        with contextlib.suppress(Exception):
            hits = await _concept_anchor_hits(getattr(ctx, "path", None), question, hits)
    # Flow-path expansion: when the question anchors 2+ endpoints (a named
    # symbol's file, a module it names), lead with the dependency/call path
    # between them. Plain 1-hop expansion (above) rescues "right module wrong
    # file" ranking misses; it does NOT reach a far endpoint 2-4 hops away that
    # the question names but retrieval never ranked. This threads that path over
    # imports + projected calls edges and injects its files so both endpoints
    # surface in one call. Runs before the cap so an injected endpoint can take a
    # top-5 slot.
    flow_paths: list[list[str]] = []
    with contextlib.suppress(Exception):
        async with get_session(ctx.session_factory) as session:
            hits, flow_paths = await _expand_via_flow_path(
                session, repo_id, hits, question, question_ids
            )
    # Neighborhood re-rank: the sibling to flow-path expansion for the flow
    # questions it can't reach — the ones whose gold file is never *named*. Seeds
    # from the top hits, walks 1-2 hops out over the same graph, and re-ranks the
    # reached neighborhood by fused embedding+lexical relevance so a far endpoint
    # that lost the corpus-wide retrieval but wins within its own subsystem gets
    # a top-5 slot. Additive and gated to flow-shaped questions; a no-op
    # otherwise. Runs before the cap so an injected file can land in the top-5.
    with contextlib.suppress(Exception):
        async with get_session(ctx.session_factory) as session:
            hits = await _expand_via_neighbor_rerank(session, repo_id, hits, question, ctx)
    # Parent-concept surfacing: on a subsystem-shaped question ("overview of X",
    # "what subsystem does Y belong to", "where would I add a Z"), lead with the
    # concept/rollup page that documents the whole subsystem instead of the more
    # specific child pages retrieval ranks above it. Structural + query-shape
    # only; a no-op on every other question, so file/implementation queries keep
    # today's ranking. Runs before the cap so the parent can take a top-5 slot.
    with contextlib.suppress(Exception):
        hits = await _expand_via_parent_page(hits, question, ctx)
    # Demote retrieval noise (decision records on non-why questions, test file
    # pages on non-test questions) below real pages before the cap, so it can't
    # occupy a top-5 slot and feed synthesis. Stable and non-dropping; runs after
    # all anchoring/expansion (which inject file/symbol pages, never noise) so it
    # only reorders what those stages left in place.
    hits = _demote_noise_hits(hits, question, is_why=_is_why_question(question))
    # Everything retrieval resolved, in rank order, before the cap. Synthesis
    # keeps its 5-hit budget, which is a context-window decision and the
    # right one, but the files below the cut are still the best answer to
    # "where do I look next", and they used to be discarded. ``candidates``
    # (built after synthesis) hands them over at one line each.
    resolved_pool = list(hits)
    # Always cap retrieval hits at 5 for the response payload.
    hits = hits[:5]

    # Enrich each file_page hit with its top-N WikiSymbol rows. Question-
    # aware: identifiers extracted from the question promote matching
    # symbols and attach a source-body excerpt — the difference between a
    # hedged answer on a specific-method question and a grounded one.
    if hits:
        with contextlib.suppress(Exception):
            async with get_session(ctx.session_factory) as session:
                await _hydrate_symbols_for_hits(
                    session, repo_id, hits, ctx, question_ids=question_ids
                )
                # And the shortlist BELOW the synthesis cap: `candidates` names
                # those files and, until now, said nothing about any of them.
                # Runs here, sharing the open session, and against
                # `resolved_pool` rather than `hits` because the whole point is
                # the files the cap discarded. Suppressed with the block above:
                # a missing `_defines` costs a `defines` key, never an answer.
                await _hydrate_candidate_defines(
                    session, repo_id, resolved_pool, question_ids=question_ids
                )

    # --- Qualified-miss guard ----------------------------------------------
    # The question qualified a symbol (``Parent.leaf``) but the exact-name scan
    # found the leaf only under OTHER parents. Return not-found rather than
    # synthesizing from a same-named symbol elsewhere: a precise query must
    # never degrade to a confidently-wrong answer (CodeGraph #173).
    if homonyms.get("qualified_miss"):
        missed = homonyms["qualified_miss"]
        return _with_candidates(
            {
                "answer": "",
                "citations": [],
                "confidence": "low",
                "note": (
                    f"No indexed definition matches the qualified name(s) {missed}. "
                    "The base name is defined elsewhere, but not under the "
                    "class/module you named, so this is not returning a same-named "
                    "symbol from another file, to avoid a confidently-wrong answer. "
                    'Re-check the qualifier, or call search_codebase mode="symbol" '
                    "on the base name to see every definition. The files retrieval "
                    "ranked for this question are in candidates."
                ),
                "fallback_targets": [],
                "retrieval": [],
                "_meta": _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint("low", 0),
                    repository=repository,
                    targets=[],
                ),
            },
            resolved_pool,
        )

    # --- Answer-by-union (homonym exact-name lookup) -----------------------
    # The question named a symbol with N>=2 defs no qualifier disambiguates
    # (``_severity_for`` x 4). Instead of bailing to a best_guesses pointer list
    # (the exact thing that triggers the agent's get_symbol/get_context drill),
    # inline the UNION of the candidate bodies (char-budgeted, Read-parity) so
    # the agent picks the one it wants from material already in-hand. This is
    # the fix for the retrieval-MISS class: those defs are never in the fuzzy
    # candidate set, so the exact-name scan is the only thing that surfaces them.
    # Defer to synthesis when the union is incidental: a prose question that
    # merely mentions a many-def generic method (``to_dict``, ``provider_name``)
    # would otherwise dump every unrelated body as a confidence=high answer,
    # burying what was actually asked. A bare symbol lookup, or a small genuine
    # parallel-impl set (``_severity_for`` x4), still answers by union.
    union_groups = homonyms.get("union") or {}
    if union_groups and union_defers_to_synthesis(question, question_ids, union_groups):
        union_groups = {}
    # A mechanism/"how" question that merely NAMES an indexed symbol (e.g. "how
    # does X verify its bounds?") must not short-circuit to a dump of that
    # symbol's bodies: the mechanism it asks about often lives in another file the
    # union path never retrieves, yielding a confidently-wrong "here are the
    # definitions" non-answer. Defer to synthesis regardless of def count.
    # Naming/lookup questions ("what does X return", "where is Y") are untouched
    # and still answer by union.
    if union_groups and _flag_on(_UNION_MECHANISM_DEFER_ENV) and _is_mechanism_question(question):
        union_groups = {}
    if union_groups:
        repo_root = Path(str(ctx.path)) if getattr(ctx, "path", None) else None
        union_bodies, more_defs = build_homonym_union_bodies(repo_root, union_groups)
        if union_bodies:
            names = sorted(union_groups)
            total = sum(len(v) for v in union_groups.values())
            cited = sorted({b["path"] for b in union_bodies})
            # This payload returns BEFORE synthesis, so none of the confidence
            # gates below ever see it: it is served in no-LLM mode and it used
            # to hardcode confidence="high" with "no verification Read" even
            # when a body arrived truncated. "this is the complete set" is also
            # an exclusivity claim, generated by us rather than by a model, and
            # it is true of the DEFINITION SET while saying nothing about
            # whether each body was served whole.
            #
            # The dependency test the synthesis gate uses CANNOT work here, and
            # keying on it made this gate dead code. This path is reached only
            # for naming/lookup questions about the homonym, so the question
            # names the SERVED symbol by construction while the withheld symbols
            # are its inner members, and there is no answer prose to inspect
            # either. Here the bodies simply ARE the answer, so truncation alone
            # is the right condition: a body the consumer cannot see is exactly
            # what confidence should reflect. This is the one place the blunt cap
            # the brief proposed is correct.
            union_truncated = any(b.get("truncated") for b in union_bodies)
            _union_confidence = "medium" if union_truncated else "high"
            note = (
                f"{total} definition(s) of {', '.join(names)} exist (exact-name "
                f"index scan; this is the complete set of DEFINITIONS). "
                f"{len(union_bodies)} inlined below in symbol_bodies as live "
                "source"
            )
            note += (
                "; use them directly, no verification Read."
                if not union_truncated
                else ". At least one body was truncated: see "
                     "symbol_bodies[].withheld_symbols for what was not served, "
                     "and call get_symbol with the continuation before relying "
                     "on behaviour you cannot see."
            )
            if more_defs:
                note += (
                    f" {len(more_defs)} more are in more_definitions; call "
                    "get_symbol with the listed id, do NOT Read."
                )
            note += (
                " If the question was about something other than these definitions, "
                "candidates holds the files retrieval ranked for it."
            )
            payload: dict = {
                "answer": (
                    f"`{', '.join(names)}` has {total} definition(s) in this repo; "
                    "all are inlined in symbol_bodies below. They are distinct "
                    "implementations, so pick the one for your context."
                ),
                "citations": cited,
                "confidence": _union_confidence,
                "grounding": "exact_symbol",
                "symbol_bodies": union_bodies,
                "fallback_targets": [b["path"] for b in union_bodies],
                "retrieval": [],
                "note": note,
                "_meta": _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint(_union_confidence, len(union_bodies)),
                    repository=repository,
                    targets=cited,
                ),
            }
            if more_defs:
                payload["more_definitions"] = more_defs
            return _with_candidates(payload, resolved_pool)
        # Bodies unreadable (no repo root / files gone) — fall through to the
        # normal retrieval/gate path rather than returning an empty union.

    fallback_targets = [
        h["target_path"]
        for h in hits
        if h.get("target_path") and _is_readable_path(h["target_path"])
    ]

    if not hits:
        # Wrapped like every other post-retrieval return even though the pool is
        # necessarily empty here (``resolved_pool`` is ``hits`` before the cap, so
        # no hits means no pool). Keeping the invariant "every return after
        # retrieval goes through ``_with_candidates``" is what stops the next
        # reordering of this function quietly re-opening the hole.
        return _with_candidates(
            {
                "answer": "",
                "citations": [],
                "confidence": "low",
                "fallback_targets": [],
                "retrieval": [],
                "note": (
                    "No wiki hits for this question. Rephrase around the code "
                    'concept, or use search_codebase (mode="symbol" for an '
                    'identifier, mode="path" for a file name); if the question '
                    "names a file, call get_context on it directly. Grep only "
                    "if those come back empty too."
                ),
                "_meta": _build_meta(
                    timing_ms=(time.perf_counter() - t0) * 1000,
                    hint=_answer_hint("low", 0),
                    repository=repository,
                    targets=[],
                ),
            },
            resolved_pool,
        )

    # Attach real page content to the top hits, once, for every retrieval —
    # before anything downstream branches on how good the retrieval looks.
    #
    # This used to run only when retrieval was NOT dominant, and dominance is
    # what earns high confidence: the more certain retrieval was, the less
    # prose the model was given, so confident answers were the ones built from
    # symbol names alone. Whatever replaces the code below, keep this
    # unconditional. Two call sites under different conditions is what made
    # that inversion possible, and the cost of enriching a hit that a later
    # fast path never reads is one indexed SELECT over at most five rows.
    hits_without_page_content = await _attach_page_excerpts(hits, ctx)
    if hits_without_page_content:
        _log.warning(
            "get_answer: %d of %d top hits have no page content; those hits "
            "reach synthesis as a one-line summary only",
            hits_without_page_content,
            min(len(hits), _PAGE_EXCERPT_HITS),
        )

    # --- Retrieval dominance -----------------------------------------------
    # ``dominant`` = retrieval clearly pointed at ONE page (the top hit
    # outscores the rest). It no longer decides WHETHER to synthesize — under
    # the always-synthesize default, synthesis runs for every retrieval so
    # coverage matches a research assistant that answers every question (the
    # pre-synthesis gate abstained on ~58%). It now feeds the confidence grade
    # as a CEILING (a non-dominant retrieval is "answered, but verify", never
    # "high") and gates the ambiguous-retrieval evidence folded into the reply.
    #
    # Two-tier test: at high retrieval quality (both scores excellent) close
    # ratios are expected, so use an absolute gap; at lower quality the ratio
    # gate flags genuinely ambiguous retrievals. Coverage (fraction of query
    # terms in the top hit) biases ranking but is intentionally NOT a gate:
    # natural-language questions rarely have all content terms in one page
    # (typical 0.15-0.25), so a coverage threshold over-fires. Default dominant
    # for a lone hit (nothing to be ambiguous against).
    always_synthesize = _always_synthesize()
    # Agreement dominance recovers the "both retrievers rank this #1" signal
    # that RRF fusion compresses out of the numeric score. Computed once and
    # OR'd into every place the ratio/gap gate decides dominance, so it can
    # only LIFT a retrieval — never demote one the ratio already trusts.
    # Read the vector leg's own recorded status rather than inferring it from
    # `hits`, which is capped to 5 by here: "no _vec_rank in the top 5" is also
    # what a timed-out, errored, scope-filtered or simply outranked vector leg
    # looks like, and those must NOT fall back to the symbol leg.
    agreement_dominant = (
        _agreement_dominant(
            hits,
            vector_leg_keyless=(
                _symbol_agreement_enabled() and _retrieval_legs().get("vector") == "keyless"
            ),
        )
        if _agreement_confidence_enabled()
        else False
    )
    dominant = True
    if len(hits) >= 2:
        top_score = hits[0].get("score", 0.0)
        second_score = hits[1].get("score", 0.0) or 1e-9
        if top_score >= 3.0:
            dominant = (top_score - second_score) >= 0.5
        else:
            dominant = (top_score / second_score) >= _DOMINANCE_RATIO
        dominant = dominant or agreement_dominant

    if not always_synthesize and not dominant:
        # Legacy abstain path (REPOWISE_ANSWER_ALWAYS_SYNTHESIZE=off): retrieval
        # is ambiguous, so skip synthesis and hand back ranked excerpts +
        # best_guesses for the agent to ground in.
        #
        # The excerpts those best_guesses carry were attached above. Agent-
        # transcript evidence (context-tool bench, 2026-07-17): a pointers-only
        # gated payload sends the agent into an 8-15 call Grep/Read spree that
        # costs more than a bare agent — it paid for the tool call and still had
        # to acquire all content natively. Excerpts turn the miss path into
        # "pick one candidate, verify with at most one Read".
        best_guesses = _build_best_guesses(hits)
        # Mine source comments for rationale the wiki/decision corpus missed —
        # turns "go Read these 5 files" into a cited why.
        code_rationale = _gather_code_rationale(ctx, hits, fallback_targets, question)
        has_excerpts = any("excerpt" in g for g in best_guesses)
        gated: dict = {
            "answer": "",
            "citations": [],
            "confidence": "low",
            "retrieval_quality": "weak",
            "best_guesses": best_guesses,
            "next_action_hint": (
                (
                    f"Start from the excerpt of {best_guesses[0]['file']} — "
                    "it scored highest; Read the file only to verify "
                    "details the excerpt does not settle."
                    if has_excerpts
                    else f"Read {best_guesses[0]['file']} first — it scored "
                    "highest but retrieval was ambiguous, so verify "
                    "before answering."
                )
                if best_guesses
                else (
                    'Retry search_codebase with mode="symbol" or '
                    'mode="path" on the key terms; Grep only if those '
                    "miss too."
                )
            ),
            "fallback_targets": fallback_targets,
            "retrieval": [],
            "note": (
                "Multiple plausible candidates — synthesis skipped to "
                "avoid anchoring on a wrong frame. Each best_guess entry "
                "names why that file is in the running"
                + (", and its excerpt carries that page's actual content." if has_excerpts else ".")
            ),
        }
        if code_rationale:
            gated["code_rationale"] = code_rationale
            gated["note"] += (
                " code_rationale carries rationale comments mined from the "
                "candidate source — they may already answer the question."
            )
        gated["_meta"] = _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint=_answer_hint("low", len(hits)),
            repository=repository,
            targets=fallback_targets,
        )
        return _with_candidates(gated, resolved_pool)

    # Confidence is the only axis we gate on. We deliberately do NOT add a
    # second gate keyed on question shape (e.g. relational questions
    # containing connectives like "between", "and", "from"). Relational vs
    # non-relational is the wrong axis to gate on: the hard relational
    # failures already surface as low-dominance retrievals and are caught
    # by the gate above, while a shape-based gate over-fires on confidently
    # dominant relational questions and pushes cost back onto the agent's
    # own reasoning loop.

    # --- Value-extraction fast path ----------------------------------------
    # Value-shaped question + a question-matched constant in the top hits →
    # the verbatim assignment line (read live by the hydrator) IS the
    # answer. Today this class of question costs a multi-call drill-down
    # chain and synthesis sometimes invents the number; the fast path is one
    # call, zero LLM cost, and cannot hallucinate. Not cached: extraction is
    # cheap and must always reflect the current source.
    if _is_value_question(question) and question_ids:
        extraction = _extract_value_answer(hits, question_ids)
        if extraction is not None:
            top_score_fp = hits[0].get("score", 0.0) if hits else 0.0
            answer_text = extraction["answer"]
            if extraction.get("value_source"):
                answer_text += "\n\n" + extraction["value_source"]
            return _with_candidates(
                {
                    "answer": answer_text,
                    "citations": [extraction["file"]],
                    "confidence": "high",
                    "retrieval_quality": (
                        "high" if top_score_fp >= _HIGH_CONFIDENCE_SCORE_FLOOR else "partial"
                    ),
                    "grounding": "extracted",
                    "fallback_targets": fallback_targets,
                    "retrieval": [],
                    "note": (
                        "Extracted verbatim from the live source line — no LLM "
                        "synthesis involved. Cite directly; no verification "
                        "Read needed. candidates holds the files retrieval ranked, "
                        "for the wider question the value sits inside."
                    ),
                    "_meta": _build_meta(
                        timing_ms=(time.perf_counter() - t0) * 1000,
                        hint=_answer_hint("high", len(hits)),
                        repository=repository,
                        targets=[extraction["file"], *fallback_targets],
                    ),
                },
                resolved_pool,
            )

    # --- Synthesis (LLM) ---------------------------------------------------
    provider = _resolve_provider_for_answer(getattr(ctx, "path", None))
    if provider is None:
        # Retrieval-only mode (no provider). Return the hits so the agent can
        # at least skip the search_codebase step — but mark the degradation
        # loudly: an arm/user should never need to diff payload shapes to
        # notice synthesis is unplugged.
        _log.warning(
            "get_answer running WITHOUT synthesis: no LLM provider resolvable "
            "(set REPOWISE_PROVIDER + its API key, or any supported API key)."
        )
        return _degraded_payload(
            reason="no-llm-provider",
            note=(
                "DEGRADED: no LLM provider configured (set REPOWISE_PROVIDER "
                "+ API key). "
                "Returning retrieval hits only — Read the listed files to answer."
            ),
            hits=hits,
            fallback_targets=fallback_targets,
            repository=repository,
            t0=t0,
            resolved_pool=resolved_pool,
        )

    # Decision fusion (why-shaped questions only) + structured prelude. Both
    # layers are gated on signal: no ADRs for the top hits → no decisions
    # block, no symbols / commits / decisions → no prelude. Empty layers are
    # dropped before formatting, so the prompt never carries hollow scaffolding.
    top_paths = [h["target_path"] for h in hits if h.get("target_path")]
    decisions: list[dict] = []
    if _is_why_question(question) and top_paths:
        with contextlib.suppress(Exception):
            decisions = await _fetch_relevant_decisions(ctx, repo_id, top_paths)
    prelude = ""
    with contextlib.suppress(Exception):
        prelude = await _build_structured_prelude(hits, decisions, ctx, repo_id)

    user_prompt = _USER_TEMPLATE.format(
        question=question.strip(),
        n=len(hits),
        context=_build_context_block_v2(hits, prelude=prelude, decisions=decisions),
    )

    # The call budgets itself against what this provider actually needs. A
    # remote API answers in single-digit seconds; an agent-CLI subprocess or a
    # local model needs minutes, and the old flat 30s cancelled every one of
    # those before it could return (#1119).
    answer_text, failure_note = await synthesize(
        provider,
        _SYSTEM_PROMPT,
        user_prompt,
        session_factory=getattr(ctx, "session_factory", None),
        repo_id=repo_id,
    )
    if failure_note is not None:
        return _degraded_payload(
            reason="synthesis-failed",
            note=failure_note,
            hits=hits,
            fallback_targets=fallback_targets,
            repository=repository,
            t0=t0,
            resolved_pool=resolved_pool,
        )

    citations = [
        h["target_path"] for h in hits if h["target_path"] and h["target_path"] in answer_text
    ]
    if not citations:
        # Fall back to top-2 retrieval paths so the agent always has something to verify.
        citations = fallback_targets[:2]

    # Line-grounded quotes: for symbols the answer actually names, attach the
    # verbatim source line(s) the hydrator read live from disk. An agent can
    # publish a cited claim backed by a quote without any verification Read —
    # the quote IS the verification.
    quotes: list[dict] = []
    for h in hits[:_ENRICH_TOP_N_HITS]:
        for s in h.get("symbols") or []:
            name = s.get("name")
            # Require a name long enough that substring containment is
            # meaningful — a 1-2 char constant (``T``, ``e``) would "appear"
            # in almost any answer and attach an irrelevant quote.
            if not name or len(name) < 3 or name not in answer_text:
                continue
            src = s.get("source_excerpt") or s.get("signature") or ""
            if not src:
                continue
            quote_lines = src.splitlines()[:3]
            start = s.get("start_line") or 0
            quotes.append(
                {
                    "path": h.get("target_path"),
                    "lines": [start, start + len(quote_lines) - 1],
                    "quote": "\n".join(quote_lines),
                }
            )
            if len(quotes) >= 5:
                break
        if len(quotes) >= 5:
            break

    # Inline symbol bodies: for the multi-line definitions (function / method
    # / class) the answer actually names, surface the full body the hydrator
    # already read live for synthesis. This collapses the get_answer ->
    # get_symbol drill-down — the agent that asked "how does X work" gets X's
    # body in the same call instead of a follow-up read. Constants stay in
    # `quotes` (their body IS the one-line assignment); only definitions with
    # a real body earn a block. `source` is the live body sliced at the
    # indexed bounds; it is NOT bounds-verified, so the field stays distinct
    # from get_symbol's `verified` contract. When the indexed body is longer
    # than the hydrator's line cap, a `continuation` names the exact range
    # read for the remainder (mirrors get_symbol).
    _body_candidates = _gather_body_candidates(hits, answer_text)

    symbol_bodies: list[dict] = []
    _seen_bodies: set[tuple[str, str]] = set()
    # True once a tier-0 body (the exact symbol the question named, resolved by
    # symbol anchoring) is inlined. Its full live body IS the ground truth, so a
    # response carrying it is content-grounded even when synthesis hedges — the
    # confidence gate below reads this to avoid the "low, go Read" label that
    # contradicts a payload already holding the answer (2026-07-11 dogfood).
    served_named_body = False
    repo_root = Path(str(ctx.path)) if getattr(ctx, "path", None) else None
    for _tier, _kind_rank, start, path, s in _body_candidates:
        if len(symbol_bodies) >= _INLINE_BODY_MAX_SYMBOLS:
            break
        name = s["name"]
        if (path, name) in _seen_bodies:
            continue
        sym_end = s.get("end_line") or 0
        # Re-read a fuller body than the synthesis excerpt: this block is for
        # the agent, so a docstring-heavy def shouldn't spend its whole window
        # on docstring and truncate the logic the question asked about. Falls
        # back to the hydrator's excerpt if the re-read fails.
        body = _read_symbol_source(
            repo_root, path, start, sym_end, max_lines=_INLINE_BODY_MAX_LINES
        ) or s.get("source_excerpt")
        if not body:
            continue
        served = body.count("\n") + 1
        end_served = start + served - 1
        sym_end = sym_end or end_served
        entry: dict = {
            "path": path,
            "name": name,
            "lines": [start, end_served],
            "source": body,
        }
        if sym_end > end_served:
            entry["truncated"] = True
            entry["continuation"] = f"{path}:{end_served + 1}-{sym_end}"
            # Say WHAT was withheld, not just that something was. A bare
            # truncated flag plus a get_symbol pointer was followed zero times
            # across the agent runs measured, so the consumer needs the names in
            # hand to decide whether it is missing anything it cares about, and
            # to continue inside this tool rather than falling back to Read.
            withheld = withheld_definitions(repo_root, entry["continuation"])
            if withheld:
                entry["withheld_symbols"] = withheld
        symbol_bodies.append(entry)
        _seen_bodies.add((path, name))
        if _tier == 0:
            served_named_body = True

    # Compute confidence from the dominance ratio (top hit vs second hit).
    # The dominance ratio is a more reliable separator than absolute BM25
    # thresholds, which tend to label most retrievals "high" indiscriminately.
    if len(hits) >= 2:
        _top = hits[0].get("score", 0.0)
        _second = hits[1].get("score", 0.0) or 1e-9
        _ratio = _top / _second
    else:
        _ratio = float("inf") if hits else 0.0
    _top_score = hits[0].get("score", 0.0) if hits else 0.0
    # Agreement lifts the RRF-compressed ratio: a consensus top hit (both
    # retrievers rank it at/near #1) grades dominant even though its fused
    # score barely outscores the runner-up. The score floor still applies, and
    # is naturally cleared — a rank-0-in-both hit scores ~6 after RRF scaling.
    _dominant_grade = _ratio >= _DOMINANCE_RATIO or agreement_dominant

    # Strong answer-grounding can EARN "high" on a NON-dominant retrieval. RRF
    # compresses sibling scores, so a rank-1 hit buried in a cluster of related
    # files never "dominates" numerically and was capped at medium even when the
    # synthesised answer is fully grounded in served source. Earn high when
    # EITHER the question's named symbol body is served in-hand (tier-0 anchor),
    # OR every distinctive mechanism term the answer names is grounded in the
    # retrieval corpus AND a cited hit carries real symbol bodies. Conservative:
    # a single ungrounded mechanism term disqualifies (that is the fabricated-
    # mechanism signal), and the score floor still applies — this lifts
    # non-dominant *well grounded* answers, never weakly-retrieved ones. The
    # demotion gates below (hedge, value, claim-support) still pull an earned
    # high back down.
    earn_high = False
    if _flag_on(_EARN_HIGH_GROUNDING_ENV) and _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        _cited = set(citations)
        _cited_has_body = any(h.get("symbols") for h in hits if h.get("target_path") in _cited)
        _fu, _fg = _frame_term_grounding(answer_text, question, hits)
        grounding_strong = _cited_has_body and _fg >= 1 and not _fu
        earn_high = served_named_body or grounding_strong

    if (_dominant_grade or earn_high) and _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        confidence = "high"
    elif _dominant_grade:
        # Dominant but weak — the right file relative to its siblings, but
        # the signal isn't strong enough to trust the synthesised answer
        # without verification. Downgrade so the consumer Reads the source.
        confidence = "medium"
    else:
        confidence = "medium"

    # Second gate: downgrade when the LLM's own answer admits insufficiency.
    # Retrieval dominance only tells us we indexed the right file; it does
    # not mean the synthesized text is usable. Shipping a hedged answer with
    # confidence="high" misleads the consumer AND drags the full retrieval
    # payload (~10k chars) through the conversation cache for no benefit.
    hedged = _answer_is_hedged(answer_text)
    if hedged:
        # A hedge means the synthesised PROSE is weak — but when the exact
        # symbol the question named is inlined in symbol_bodies (tier-0 anchor,
        # full live body), the answer's ground truth is already in-hand. Labeling
        # that "low" contradicts the payload and fires the "go Read" hint the
        # body makes unnecessary, so the agent bails to Read when it never needed
        # to. Hold such a response at medium; the note below redirects the agent
        # from the hedged prose to the served body.
        confidence = "medium" if served_named_body else "low"

    # Third gate — identifier-citation gate: when the question explicitly
    # names identifiers (classes / methods / snake_case / CamelCase) and
    # NONE of the top retrieval hits contain any of those identifiers as a
    # hydrated symbol, retrieval may be pointing at plausible-but-wrong
    # files (same module family, similar vocabulary). Downgrade high→medium
    # so the consumer Reads the `fallback_targets`. Only applies when the
    # question actually names identifiers — mechanism-descriptive questions
    # (no symbol names) are unaffected.
    if confidence == "high" and question_ids:
        top_n = [h for h in hits[:_ENRICH_TOP_N_HITS] if h.get("symbols")]
        has_match = any(s.get("_matched") for h in top_n for s in (h.get("symbols") or []))
        if not has_match:
            confidence = "medium"

    # Fourth gate — value grounding: on value-shaped questions (default /
    # threshold / limit / how many), every number the answer asserts must
    # appear somewhere in the material retrieval actually contained. A
    # number synthesis produced from thin air is a factual error delivered
    # with authority — the single worst calibration failure, because the
    # consumer was told not to verify. Cap at low and say why.
    ungrounded_values: list[str] = []
    if not hedged and _is_value_question(question):
        ungrounded_values = _ungrounded_numbers(answer_text, hits)
        if ungrounded_values:
            confidence = "low"

    # Fifth gate — citation-source gate: a high-confidence answer must cite
    # at least one page that contributed actual source material (hydrated
    # symbols with signatures/bodies), not just file summaries. Summary-only
    # grounding is how plausible-but-wrong syntheses get through.
    if confidence == "high":
        cited = set(citations)
        if not any(h.get("symbols") for h in hits if h.get("target_path") in cited):
            confidence = "medium"

    # Sixth gate — claim-support / frame grounding: a high-confidence answer
    # must name its mechanism in terms the cited material actually contains. The
    # dominance gate is generous on repo-internal questions (an anchored symbol +
    # a dominant hit clear it), so a synthesis that conflates two mechanisms —
    # right file, wrong reason/function — rides through at high confidence. The
    # tell is a distinctive code-like term (a class / function / module the
    # answer names AS the mechanism) that appears nowhere in everything retrieval
    # showed: the "right file, wrong function inside it" failure. When such terms
    # are not outweighed by grounded ones, downgrade high→medium so the consumer
    # verifies instead of trusting.
    #
    # The original gate fired only on "why" questions, but the same failure
    # occurs on "how" questions that name the mechanism in the ANSWER, not the
    # question — the "right file, wrong function inside it" case. Broaden to
    # mechanism/how questions too (behind the claim-support flag). Value questions
    # have their own numeric gate above; naming/lookup questions legitimately just
    # echo the named symbol, so they are excluded.
    frame_unsupported: list[str] = []
    _claim_scope = _is_why_question(question) or (
        _flag_on(_CLAIM_SUPPORT_GATE_ENV) and _is_mechanism_question(question)
    )
    if confidence == "high" and not hedged and _claim_scope:
        frame_unsupported, _grounded_terms = _frame_term_grounding(answer_text, question, hits)
        if frame_unsupported and len(frame_unsupported) >= _grounded_terms:
            confidence = "medium"
        else:
            frame_unsupported = []

    # Seventh gate — completeness scope over truncated bodies: when prose asserts
    # an unqualified exclusivity claim ("entirely", "the sole", "the only") while
    # any cited symbol body arrived truncated: true, the answer asserts a global
    # property from a sample it knows is incomplete (#1444).
    # Guard: only fires at high (like gates 3 / 5 / 6) so it cannot stack with
    # other downgrades and push a single response two levels for one problem.
    exclusivity_over_truncated = False
    if confidence == "high" and not hedged:
        exclusivity_over_truncated = _has_unqualified_exclusivity_over_truncated(
            answer_text, symbol_bodies
        )
        if exclusivity_over_truncated:
            confidence = "medium"

    # Eighth gate — a WITHHELD symbol the response depends on. The seventh gate
    # needs an exclusivity token in the prose as well as truncation, and measured
    # across four independent runs of the reference defect the token appears in
    # only one of them: the other three are equally incomplete, equally "high",
    # and it stays silent. It is also inert by construction in no-LLM mode, where
    # there is no prose to hold a token.
    #
    # This gate keys on the dependency instead. It fires when a symbol in the
    # withheld range is named by the QUESTION (every mode) or referenced as code
    # by the ANSWER (LLM mode). Truncation alone is deliberately NOT enough:
    # 22% of truncations withhold nothing the response leans on, and `high` is
    # worth keeping when it is earned.
    withheld_implicated = implicated_withheld_symbols(
        question, answer_text, symbol_bodies
    )
    if confidence == "high" and withheld_implicated:
        confidence = "medium"

    # Ninth gate — the LOOKUP half of the eighth, and a routing hole rather
    # than a threshold problem. Gate 8 asks whether the question names a
    # WITHHELD symbol; on a bare-name lookup it provably never can, because the
    # question names the symbol that was SERVED and the withheld names are that
    # symbol's own inner members. #1451 closed exactly this shape on the union
    # path with a cap on truncation alone, on the grounds that where the caller
    # asked for a symbol the bodies ARE the answer. But a name with a single
    # definition never reaches the union path — `_anchor_symbol_hits`
    # short-circuits at `len(cands) == 1` before `homonyms["union"]` is built —
    # so it lands here with the same shape and, until now, no cap.
    #
    # Measured on the 2026-08-12 corpus: `ModelAdmin` (django) served 120 of
    # 1,753 lines at `high`, 93% of the cited body withheld; `useSlider` (mui)
    # 120 of 558, 78% withheld. Two trees, two languages. The dominance ratios
    # (1.67x, 1.29x) are not the cause and are not touched.
    #
    # Deliberately narrow. It needs the question to read as a symbol lookup
    # rather than prose, AND the truncated body to be the very symbol the
    # question named. On a prose question the body is evidence
    # for a claim rather than the answer itself, and truncation alone is a poor
    # signal there (22% of truncations withhold nothing the response leans on),
    # which is why gate 8 keeps the dependency test for that population.
    # Kept as the entry rather than a flag so the note can quote the real served
    # range and continuation instead of describing the cut in the abstract.
    #
    # `truncated` alone is not trustworthy enough to demote on. It is set by
    # comparing the INDEXED end_line against what was read, while the read
    # clamps to the end of the live file — so a symbol whose stored end
    # overshoots (an unsupported language or a syntax error leaves
    # `check_symbol_bounds` unverified, and a file that shrank since indexing
    # does it too) is served WHOLE and still flagged. Requiring the served span
    # to have hit the line cap says the cut was ours, which is the only case
    # where something was really withheld.
    named_body_cut = next(
        (
            b
            for b in symbol_bodies
            if b.get("truncated")
            and b.get("continuation")
            and b.get("name") in question_ids
            and b["lines"][1] - b["lines"][0] + 1 >= _INLINE_BODY_MAX_LINES
        ),
        None,
    )
    lookup_body_truncated = (
        confidence == "high"
        and named_body_cut is not None
        and is_symbol_lookup_question(question, question_ids)
    )
    if lookup_body_truncated:
        confidence = "medium"

    # Non-dominant ceiling: ambiguous retrieval is the calibration cost of
    # always synthesizing — the answer may be right, but with no single dominant
    # page it must never read "high" (cite without verifying). Cap at medium even
    # if all six gates passed. (A non-dominant retrieval already scores <high via
    # the ratio, so this is usually a no-op; it is explicit so the
    # always-synthesize contract — "answered, but verify" — is self-documenting.)
    # Exception: an answer that EARNED high via strong grounding (named symbol
    # body in-hand, or every mechanism term grounded in a cited body) is not
    # "cite without verifying" — the source IS in the payload — so the
    # non-dominance ceiling does not apply to it.
    if not dominant and not earn_high and confidence == "high":
        confidence = "medium"

    # retrieval_quality is a separate signal from confidence. Where confidence
    # says "how much should you trust the synthesised text", retrieval_quality
    # says "how good was the retrieval that fed it". The agent uses confidence
    # to decide whether to re-read; retrieval_quality to decide whether to
    # call search_codebase again with a refined query.
    if _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR and _dominant_grade:
        retrieval_quality = "high"
    elif _dominant_grade:
        retrieval_quality = "partial"
    else:
        retrieval_quality = "weak"

    if hedged:
        # Hedged answers: keep the retrieval payload lean but non-empty. The
        # consumer has been told to read the source, but the ranked hits are
        # exactly what tells it WHICH source — and a flow endpoint or a surfaced
        # subsystem page that only lives in this block would otherwise vanish
        # from the response entirely (it is not in citations, which are drawn
        # from the prose). Lean form (no per-hit key_symbols dump) keeps the
        # prompt-cache cost the empty payload was protecting.
        payload = {
            "answer": answer_text,
            "citations": citations,
            "confidence": confidence,
            "retrieval_quality": retrieval_quality,
            "fallback_targets": fallback_targets[:5],
            "retrieval": _serialize_hits(hits, limit=5, lean_symbols=True),
            "note": (
                "Synthesis hedged: the LLM could not ground the question in "
                "the indexed wiki. Read one of fallback_targets to answer."
            ),
        }
        # Even on a hedge, hand over any question-named symbol bodies we
        # resolved — the agent can read the body directly instead of the
        # fallback_targets file, which is the whole point of anchoring.
        if symbol_bodies:
            payload["symbol_bodies"] = symbol_bodies
            if served_named_body:
                # The exact symbol the question named is inlined below as live
                # source. That is the answer; the hedge is about the surrounding
                # prose, not the body. Say so, and mark the response grounded so
                # the agent cites the body instead of re-reading the file.
                payload["grounding"] = "symbol_body"
                payload["note"] = (
                    "Synthesis hedged on the prose, but symbol_bodies carries "
                    "the full live body of the symbol(s) you named — cite that "
                    "directly, no verification Read needed."
                )
            else:
                payload["note"] = (
                    "Synthesis hedged, but symbol_bodies carries the live body "
                    "of the symbol(s) you named — read that to answer."
                )
        # The hedge often means the rationale isn't in the wiki at all — it's a
        # code comment. Mine the candidate source for it before sending the
        # agent off to Read.
        code_rationale = _gather_code_rationale(ctx, hits, fallback_targets, question)
        # A comment already visible in symbol_bodies must not surface twice.
        code_rationale = _drop_already_surfaced(code_rationale, symbol_bodies)
        if code_rationale:
            payload["code_rationale"] = code_rationale
            payload["note"] += (
                " code_rationale carries rationale comments mined from the "
                "cited source — they may already answer the question."
            )
    else:
        # Confidence-conditional retrieval block: the block exists so the
        # agent can ground when the answer alone isn't trustworthy. At high
        # confidence the citations + answer suffice — carrying five enriched
        # hits through the conversation cache buys nothing. At medium the
        # agent verifies the top candidates: two truncated hits, no symbol
        # enrichment for graph-expansion neighbors. Low keeps a grounding
        # block, but lean: the top hits with snippets, symbols pipeable but
        # stripped of docstrings/excerpts — the full per-hit key_symbols dump
        # was the largest block by volume and went mostly unused on a
        # low-confidence answer (2026-07-11 dogfood).
        if confidence == "high":
            retrieval_view: list[dict] = []
        elif confidence == "medium":
            retrieval_view = _serialize_hits(
                hits, limit=2, summary_chars=160, symbols_for_expanded=False
            )
        else:
            retrieval_view = _serialize_hits(hits, limit=_GATED_RETURN_HITS, lean_symbols=True)
        payload = {
            "answer": answer_text,
            "citations": citations,
            "confidence": confidence,
            "retrieval_quality": retrieval_quality,
            "fallback_targets": fallback_targets,
            "retrieval": retrieval_view,
        }
        if quotes:
            payload["quotes"] = quotes
        if symbol_bodies:
            payload["symbol_bodies"] = symbol_bodies
        if ungrounded_values:
            payload["note"] = (
                f"Value-grounding gate: the answer asserts {ungrounded_values} "
                "but none of these appear in any retrieved excerpt — the "
                "value(s) may be synthesised. Read "
                f"{fallback_targets[0] if fallback_targets else 'the cited file'} "
                "to confirm before citing a number."
            )
            if fallback_targets:
                payload["next_action_hint"] = (
                    f"Read {fallback_targets[0]} and verify the asserted value(s) "
                    f"{ungrounded_values} against the live source."
                )
        elif frame_unsupported:
            # The synthesised answer leaned on a mechanism term retrieval never
            # showed, so the real mechanism likely lives in code the wiki /
            # decision corpus never captured. Mine the candidate source for it
            # — the same lever the gated/hedged paths use — so the downgrade
            # ships a lead, not just a warning.
            code_rationale = _gather_code_rationale(ctx, hits, fallback_targets, question)
            code_rationale = _drop_already_surfaced(code_rationale, symbol_bodies, quotes)
            if code_rationale:
                payload["code_rationale"] = code_rationale
            payload["note"] = (
                f"Claim-support gate: the answer names {frame_unsupported} as the "
                "mechanism, but that term is absent from every retrieved excerpt "
                "— it may be conflated with a different function/file. Downgraded "
                "to medium; verify against "
                f"{fallback_targets[0] if fallback_targets else 'the cited source'}"
                + (" or the code_rationale comments below." if code_rationale else ".")
            )
            payload["next_action_hint"] = (
                f"Verify the mechanism before citing: the asserted term(s) "
                f"{frame_unsupported} are not in the retrieved material."
            )
        elif exclusivity_over_truncated:
            # Note names the axis of doubt (what to be uncertain about), not the
            # check that triggered it — so a reader can tell which kind of doubt
            # this is without consulting the source code.
            payload["note"] = (
                "Answer may not cover every relevant site: a cited symbol's body "
                "was truncated and the answer makes an unqualified causal claim. "
                "Other functions may also participate; call get_symbol for the "
                "full body or verify against "
                f"{fallback_targets[0] if fallback_targets else 'the cited source'}."
            )
            if fallback_targets:
                payload["next_action_hint"] = (
                    f"Read {fallback_targets[0]} to verify whether other functions "
                    "participate beyond what the truncated symbol body shows."
                )
        elif withheld_implicated:
            # A withheld entry whose name matches the body it was found in is
            # the ENCLOSING symbol — served up to the cut and continuing past
            # it — not a symbol that never arrived. Calling that "not served"
            # is wrong about the payload directly above the note, and sends the
            # caller to get_symbol for a body they already hold most of:
            # `NewCmdRoot` lines 53-173 are in the payload and only 174-221 are
            # missing. The accurate pointer is the `continuation` the entry
            # already carries, which fetches just the missing part and is a
            # valid get_symbol id in its own right ("path.py:174-221").
            # Measured: 7 instances on 5 of 6 corpus trees.
            _implicated = set(withheld_implicated)
            _continuing = [
                b
                for b in symbol_bodies
                if b.get("continuation")
                and b.get("name") in _implicated
                and any(s.get("name") == b.get("name") for s in (b.get("withheld_symbols") or []))
            ]
            _continuing_names = {b["name"] for b in _continuing}
            _absent = [n for n in withheld_implicated if n not in _continuing_names]
            # Only advertise an id get_symbol can actually answer. The scanner
            # that produced these is a regex over source lines, so it can name
            # something that is not a symbol, and the id does not stay in a list
            # of eight — it becomes the next action the payload tells the agent
            # to take. When none resolves the names are still reported; only the
            # dead pointer is withheld.
            _hint_id = await _first_resolvable_id(
                [
                    s["symbol_id"]
                    for b in symbol_bodies
                    for s in (b.get("withheld_symbols") or [])
                    if s.get("name") in set(_absent)
                ],
                ctx,
                repository,
                exclude_spec,
            )
            _parts = []
            if _absent:
                _parts.append(
                    "Part of the code this answer depends on was not served: "
                    f"{', '.join(_absent)}."
                    + (
                        " Continue in this tool with "
                        f"get_symbol id='{_hint_id}' before relying on the mechanism."
                        if _hint_id
                        else ""
                    )
                )
            # Qualify by path only when the same name was cut in more than one
            # file, so the common case stays readable and the ambiguous case
            # does not ship two sentences that look identical.
            _dupe = len({_b["name"] for _b in _continuing}) < len(_continuing)
            for _b in _continuing:
                _who = f"{_b['name']} ({_b['path']})" if _dupe else _b["name"]
                _parts.append(
                    f"{_who} was served through line {_b['lines'][1]}; the rest "
                    f"of its body is at {_b['continuation']}."
                )
            payload["note"] = " ".join(_parts)
            payload["next_action_hint"] = (
                f"call get_symbol id='{_hint_id}' for the withheld body"
                if _hint_id
                else (
                    f"call get_symbol id='{_continuing[0]['continuation']}' for the rest "
                    f"of {_continuing[0]['name']}"
                    if _continuing
                    else "request the withheld body before citing"
                )
            )
        elif lookup_body_truncated:
            # The ninth gate fired: the caller asked for a symbol by name and
            # its body did not fit. Without this the demotion would ship with
            # no note at all, since the high-confidence branch below is now
            # unreachable for it.
            payload["note"] = (
                f"You asked for {named_body_cut['name']} and its body did not fit: "
                f"lines {named_body_cut['lines'][0]}-{named_body_cut['lines'][1]} are "
                f"served and it continues at {named_body_cut['continuation']}. Held at "
                "medium because on a symbol lookup the part you cannot see is part of "
                "the answer."
            )
            payload["next_action_hint"] = (
                f"call get_symbol id='{named_body_cut['continuation']}' for the rest of "
                f"{named_body_cut['name']}"
            )
        elif confidence == "high":
            # The rationale deliberately no longer cites "the answer is direct
            # (no hedging)". _SYSTEM_PROMPT instructs the model not to hedge, so
            # scoring the absence of hedging as evidence FOR confidence is
            # circular: the pipeline mandates directness and then reads its own
            # mandate back as a signal. Dominance is an independent measurement;
            # directness is not.
            _tail = (
                "Cite this answer; do not re-read the source unless a specific "
                "detail is missing."
                if not any(b.get("truncated") for b in symbol_bodies)
                # Never tell the consumer to skip re-reading when the payload
                # itself admits it withheld part of a cited body. The withheld
                # names are in `symbol_bodies[].withheld_symbols`.
                else "Some cited bodies were truncated; see "
                     "symbol_bodies[].withheld_symbols for what was not served."
            )
            payload["note"] = (
                "High confidence: top retrieval result clearly dominates "
                f"(dominance ratio {_ratio:.2f}x, top score {_top_score:.2f}). "
                + _tail
            )

        # Concept anchoring put a comment-justified file at the top, so synthesis
        # may now run high - but the agent asked a "why is X = <number>" question
        # and the literal rationale is the comment we already mined. Surface it so
        # the win is the answer AND the cited comment in one call (no re-read),
        # unless a gate above already attached code_rationale.
        if "code_rationale" not in payload and any(h.get("_concept_anchored") for h in hits):
            concept_rationale = _gather_code_rationale(ctx, hits, fallback_targets, question)
            concept_rationale = _drop_already_surfaced(concept_rationale, symbol_bodies, quotes)
            if concept_rationale:
                payload["code_rationale"] = concept_rationale

    # Ambiguous-retrieval evidence (always-synthesize). The questions that used
    # to abstain (no dominant page) now carry synthesized PROSE — but the
    # retrieval was genuinely ambiguous, so ship the same evidence the old
    # abstain path did: best_guesses (per-file justification + excerpts) and
    # mined code_rationale, plus an honest caveat. This is the "answered, but
    # verify against these candidates" reply that replaced the empty pointer
    # list. Guarded so it never touches the dominant / high-confidence paths.
    if not dominant:
        payload.setdefault("best_guesses", _build_best_guesses(hits))
        if "code_rationale" not in payload:
            _cr = _gather_code_rationale(ctx, hits, fallback_targets, question)
            _cr = _drop_already_surfaced(_cr, symbol_bodies, quotes)
            if _cr:
                payload["code_rationale"] = _cr
        _caveat = (
            "Retrieval was ambiguous (no single dominant page), so this was "
            f"synthesized across several candidates and held at {confidence} "
            "confidence — verify against best_guesses"
            + (" or the code_rationale comments." if payload.get("code_rationale") else ".")
        )
        payload["note"] = (payload["note"] + " " + _caveat) if payload.get("note") else _caveat
        if payload.get("best_guesses"):
            payload.setdefault(
                "next_action_hint",
                f"Verify against {payload['best_guesses'][0]['file']} — it scored "
                "highest, but retrieval was ambiguous across the top candidates.",
            )

    # Flow-path lead: when the question anchored 2+ endpoints, surface the
    # dependency/call chain the answer traverses so the agent sees the path in
    # the same call instead of reconstructing it hop by hop.
    if flow_paths:
        payload["flow_path"] = [" -> ".join(p) for p in flow_paths[:2]]

    # Where to look next, always. ``retrieval`` shrinks as confidence rises
    # (correctly: it is re-read evidence, and a trustworthy answer needs less
    # of it), but that left the highest-confidence answers naming no file at
    # all, which is the one thing an agent always has a use for. This block is
    # navigation rather than evidence: the ranked shortlist, one path per line,
    # ungated. It costs ~800 characters against a ~10k response.
    candidates = _serialize_candidates(resolved_pool)
    if candidates:
        payload["candidates"] = candidates

    # Persist to cache (upsert). Best-effort: cache failures must never block
    # the response — but they must be LOGGED, not suppressed. A plain INSERT
    # under a blanket suppress violated uq_answer_cache_q on every
    # bypass-and-resynthesize round and failed silently, so hedged/stale rows
    # were never upgraded. Delete-then-insert in one transaction is the
    # dialect-agnostic upsert; the stamped _indexed_commit drives the
    # read-side freshness check.
    if answer_text and not cache_disabled:
        cache_payload = dict(payload)
        cache_payload["_schema_version"] = _ANSWER_SCHEMA_VERSION
        commit_now = getattr(repository, "head_commit", None)
        if commit_now:
            cache_payload["_indexed_commit"] = commit_now
        try:
            async with get_session(ctx.session_factory) as session:
                await session.execute(
                    delete(AnswerCache).where(
                        AnswerCache.repository_id == repo_id,
                        AnswerCache.question_hash == qhash,
                    )
                )
                row = AnswerCache(
                    repository_id=repo_id,
                    question_hash=qhash,
                    question=question.strip(),
                    payload_json=_json.dumps(cache_payload, default=_json_default),
                    provider_name=getattr(provider, "provider_name", "") or "",
                    model_name=getattr(provider, "model_name", "") or "",
                )
                session.add(row)
                await session.commit()
        except Exception as exc:
            _log.warning("get_answer cache write failed: %s", exc)

    payload["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=_answer_hint(confidence, len(hits)),
        repository=repository,
        targets=[*citations, *fallback_targets],
    )
    # Finding A18. Each retrieval leg is best-effort so one slow backend cannot
    # block an answer, which is right, but it made a lexical-only answer
    # indistinguishable from a whole one: nothing failed, nothing was logged
    # where a caller could see it, and ``embedder_live`` stayed true because a
    # configured embedder is live whether or not this call beat its 8s budget.
    # Named only when a leg actually fell over, so a healthy response pays
    # nothing for it.
    if degraded := _degraded_legs(_retrieval_legs()):
        payload["_meta"]["retrieval_degraded"] = degraded
    _apply_lean_high(payload, question)
    # After the cache write above and after lean-high (which can remove
    # ``best_guesses`` outright), so the cut sees the finished payload and the
    # cached row keeps the shape its schema version promises.
    _trim_served_payload(payload)
    # After the cache write above, deliberately. ``cache_payload`` is a shallow
    # copy taken before this point, so the episode reaches the caller and never
    # the cache row — which is why adding it needs no _ANSWER_SCHEMA_VERSION
    # bump: a row written before this change and one written after are the same
    # bytes, and bumping would invalidate every user's cache for a field that
    # is not in it.
    await _attach_episode(
        payload,
        question=question,
        repo_path=getattr(ctx, "path", None),
        repo_name=getattr(repository, "name", None),
    )
    return payload
