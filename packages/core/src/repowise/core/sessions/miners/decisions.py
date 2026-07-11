"""Session-sourced decisions: mine durable choices out of agent transcripts.

Transcripts are the highest-grade decision source there is: "no, don't use
approach A, it broke prod, use B" is a real decision with real rationale, and
without this miner it evaporates when the session ends. This module turns
those moments into ``decision_records`` rows via three stages:

1. **Deterministic gates** (:func:`mine_events`) over the normalized
   :class:`~repowise.core.sessions.Event` stream, no LLM involved:

   - *user correction*: an interrupt with guidance, or a pushback-leading
     user message ("no, ...", "don't ...", "instead ...");
   - *explicit choice*: a sentence carrying both a decision verb and a
     causal cue (the :data:`CAUSAL_MARKERS` vocabulary) near file-touching
     tool activity;
   - *dead end*: repeated failures of one command/target followed by success
     with a different one (the delta is the lesson).

   Each candidate carries verbatim transcript quotes plus the files in play
   from surrounding tool activity.

2. **One batched LLM structuring pass** per ``repowise update``
   (config-gated ``decisions.session_mining``, default on): candidates to
   ``{title, decision, rationale, affected_files, source_quote}``. Every
   produced field is then grounded against the verbatim quotes with the
   shared :func:`~repowise.core.analysis.decisions.provenance.verify_quote`
   logic (see :func:`_gate_structured`), so a fluent-but-invented rationale
   never becomes institutional memory.

3. **Observation-counted promotion** through the staging sidecar
   (:class:`~repowise.core.sessions.staging.SessionStagingStore`): a decision
   observed in 2+ distinct sessions promotes to ``active`` with
   ``source="session"``; a user correction promotes on one observation.
   Promoted decisions ride the normal ``bulk_upsert_decisions`` path, so
   semantic dedup, evidence rows, node links, get_why, and the CLAUDE.md
   Standing-decisions block all come for free.

Privacy: transcripts never leave the machine. Mining is local and the only
thing stored is distilled decision text about the codebase, with verbatim
quotes as evidence. Kill switch: ``decisions.session_mining: false`` in
``.repowise/config.yaml``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.decisions.extractor import ExtractedDecision
from repowise.core.analysis.decisions.provenance import (
    compute_confidence,
    rank_for_source,
    verify_quote,
)
from repowise.core.analysis.decisions.rationale_comments import CAUSAL_MARKERS
from repowise.core.distill.corrections import command_anchor
from repowise.core.sessions import ClaudeCodeAdapter, Event
from repowise.core.sessions.cursor import iter_new_events
from repowise.core.sessions.staging import SessionStagingStore

logger = structlog.get_logger(__name__)

__all__ = [
    "SessionCandidate",
    "apply_injection_feedback",
    "mine_events",
    "mine_session_decisions",
    "session_mining_enabled",
]

# ---------------------------------------------------------------------------
# Gate vocabularies, precision-first on purpose. A missed decision costs one
# session of memory; a false one pollutes the record for every future session.
# ---------------------------------------------------------------------------

#: A user message opening with one of these reads as pushback on what the
#: agent just did or proposed. Matched at the start of the message only.
PUSHBACK_LEADS: tuple[str, ...] = (
    "no,",
    "no.",
    "no ",
    "nope",
    "don't",
    "dont ",
    "do not",
    "stop ",
    "stop.",
    "wait",
    "not like that",
    "that's wrong",
    "thats wrong",
    "that is wrong",
    "undo",
    "revert",
    "instead",
    "never ",
    "actually,",
    "actually ",
)

#: A sentence needs one of these to read as a choice being made (paired with
#: a :data:`CAUSAL_MARKERS` cue for the stated reason). Word-bounded so e.g.
#: "beca**use** it" never reads as the verb "use". Deliberately excludes
#: "instead of" / "rather than": those are already causal cues, and letting
#: one phrase satisfy both conditions turned every narrated trade-off in
#: assistant prose into a candidate (dogfood: 796 hits, mostly noise).
DECISION_VERB_RE = re.compile(
    r"\b(?:use|using|went with|go(?:ing)? with|stick with|switch(?:ed)? to|chose"
    r"|decided|always|never|must)\b|decision:"
)

#: Consecutive failures of one anchor before it counts as a dead end.
DEAD_END_FAILURES = 3
#: Tool events after the failure streak within which the successful
#: different-anchor call must land.
_DEAD_END_LOOKAHEAD = 8

#: Tool events after a candidate during which touched files still attach to
#: it (a correction is usually followed by the agent acting on it).
_FORWARD_FILE_EVENTS = 10
#: Trailing tool-touched files kept as "files in play" context.
_TRAILING_FILES = 8

_QUOTE_CAP = 600
_MAX_QUOTES_PER_EVENT = 2

_EXIT_CODE_RE = re.compile(r"^Error: Exit code (\d+)")
_FILE_INPUT_KEYS = ("file_path", "path", "notebook_path")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class SessionCandidate:
    """One gate hit: a moment where a durable decision may have been made."""

    kind: str  # user_correction | explicit_choice | dead_end
    quotes: list[str]
    files: list[str] = field(default_factory=list)
    session_id: str | None = None
    ts: float | None = None

    @property
    def hash(self) -> str:
        """Content identity for staging dedup (kind + normalized quotes)."""
        norm = " ".join(" ".join(q.lower().split()) for q in self.quotes)
        return hashlib.sha256(f"{self.kind}|{norm}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Stage 1: deterministic gates over one session's event stream
# ---------------------------------------------------------------------------


def _clip(text: str, cap: int = _QUOTE_CAP) -> str:
    text = text.strip()
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _event_files(event: Event) -> list[str]:
    """File paths named by this event's tool inputs."""
    files: list[str] = []
    for use in event.tool_uses:
        for key in _FILE_INPUT_KEYS:
            value = use.input.get(key)
            if isinstance(value, str) and value.strip():
                files.append(value)
                break
    return files


def _interrupt_guidance(text: str) -> str:
    """The user's own words in an interrupt event, marker lines dropped."""
    from repowise.core.sessions import INTERRUPT_MARKER

    lines = [ln for ln in text.splitlines() if INTERRUPT_MARKER not in ln]
    return "\n".join(lines).strip()


def _is_prose_user_text(event: Event) -> bool:
    """A message the user actually typed, not harness plumbing."""
    if event.kind != "user" or event.sidechain or event.is_meta or event.is_compact_summary:
        return False
    if event.tool_results:
        return False
    text = event.text.strip()
    # Command output wrappers, system reminders, and pasted XML-ish blocks
    # start with a tag; none of them are the user speaking.
    return bool(text) and not text.startswith("<")


def _correction_quote(event: Event) -> str | None:
    """The verbatim correction text, or None when the gate does not fire."""
    if event.interrupted:
        guidance = _interrupt_guidance(event.text)
        return _clip(guidance) if len(guidance) >= 8 else None
    low = event.text.strip().lower()
    if len(low) >= 12 and low.startswith(PUSHBACK_LEADS):
        return _clip(event.text)
    return None


def _choice_sentences(text: str) -> list[str]:
    """Sentences that state a choice and its reason, verbatim."""
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not 30 <= len(sentence) <= 400:
            continue
        low = sentence.lower()
        if DECISION_VERB_RE.search(low) and any(m in low for m in CAUSAL_MARKERS):
            out.append(sentence)
            if len(out) == _MAX_QUOTES_PER_EVENT:
                break
    return out


def _tool_failure(payload: Any, is_error: bool) -> str | None:
    """The failure text when this result records one, else None."""
    if isinstance(payload, str) and _EXIT_CODE_RE.match(payload):
        return payload
    if is_error and isinstance(payload, str):
        return payload
    return None


def _result_anchor(name: str, use_input: dict[str, Any]) -> str:
    """Identity a retried attempt shares: command anchor, or tool + file."""
    command = use_input.get("command")
    if isinstance(command, str) and command.strip():
        return command_anchor(command)
    for key in _FILE_INPUT_KEYS:
        value = use_input.get(key)
        if isinstance(value, str) and value.strip():
            basename = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
            return f"{name}:{basename}"
    return name.lower()


def mine_events(events: Iterable[Event], repo_prefix: str) -> list[SessionCandidate]:
    """Run the deterministic candidate gates over one session's events.

    *repo_prefix* is the lowercased resolved repo root; only events whose
    ``cwd`` sits inside it count (same scoping as the distill miners). Pure
    and streaming: state is bounded regardless of transcript size.
    """
    candidates: list[SessionCandidate] = []
    trailing_files: deque[str] = deque(maxlen=_TRAILING_FILES)
    #: Candidates still collecting forward files, with their remaining budget.
    open_candidates: list[list[Any]] = []  # [candidate, remaining_tool_events]
    #: tool_use id -> (tool name, input) awaiting its result.
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    #: (anchor, consecutive failure count, last failure text, last input repr)
    streak: list[Any] = ["", 0, "", ""]
    #: An anchor that just hit the failure threshold, awaiting the pivot.
    open_dead_end: list[Any] | None = None  # [anchor, error, attempt, budget]

    def _add(candidate: SessionCandidate) -> None:
        candidates.append(candidate)
        open_candidates.append([candidate, _FORWARD_FILE_EVENTS])

    for event in events:
        cwd = (event.cwd or "").lower().rstrip("\\/")
        if cwd and not cwd.startswith(repo_prefix):
            continue

        if event.kind == "assistant" and event.tool_uses:
            files = _event_files(event)
            for f in files:
                trailing_files.append(f)
            for entry in open_candidates:
                entry[0].files.extend(f for f in files if f not in entry[0].files)
                entry[1] -= 1
            open_candidates = [e for e in open_candidates if e[1] > 0]
            for use in event.tool_uses:
                pending[use.id] = (use.name, use.input)
            # Results normally arrive within a couple of events; anything
            # older is an orphan (cancelled call) and must not accumulate.
            while len(pending) > 200:
                pending.pop(next(iter(pending)))

        if event.tool_results:
            for result in event.tool_results:
                record = pending.pop(result.tool_use_id, None)
                if record is None:
                    continue
                name, use_input = record
                anchor = _result_anchor(name, use_input)
                failure = _tool_failure(result.payload, result.is_error)
                if failure is not None:
                    if streak[0] == anchor:
                        streak[1] += 1
                    else:
                        streak[:] = [anchor, 1, "", ""]
                    streak[2] = failure
                    command = use_input.get("command")
                    streak[3] = (
                        command
                        if isinstance(command, str)
                        else f"{name} {json.dumps(use_input, ensure_ascii=False)}"
                    )
                    if streak[1] >= DEAD_END_FAILURES:
                        open_dead_end = [anchor, streak[2], streak[3], _DEAD_END_LOOKAHEAD]
                else:
                    if open_dead_end is not None and anchor != open_dead_end[0]:
                        attempt = _clip(str(open_dead_end[2]), 300)
                        error = _clip(
                            str(open_dead_end[1]).splitlines()[0] if open_dead_end[1] else "", 300
                        )
                        pivot_command = use_input.get("command")
                        pivot = _clip(
                            pivot_command
                            if isinstance(pivot_command, str)
                            else f"{name} {json.dumps(use_input, ensure_ascii=False)}",
                            300,
                        )
                        _add(
                            SessionCandidate(
                                kind="dead_end",
                                quotes=[q for q in (attempt, error, pivot) if q],
                                files=list(dict.fromkeys(trailing_files)),
                                session_id=event.session_id,
                                ts=event.ts,
                            )
                        )
                        open_dead_end = None
                    if streak[0] == anchor or (
                        open_dead_end is not None and anchor == open_dead_end[0]
                    ):
                        # It worked eventually: a retry loop, not a dead end.
                        streak[:] = ["", 0, "", ""]
                        open_dead_end = None
                if open_dead_end is not None:
                    open_dead_end[3] -= 1
                    if open_dead_end[3] <= 0:
                        open_dead_end = None
            continue

        if _is_prose_user_text(event):
            quote = _correction_quote(event)
            if quote is not None:
                _add(
                    SessionCandidate(
                        kind="user_correction",
                        quotes=[quote],
                        files=list(dict.fromkeys(trailing_files)),
                        session_id=event.session_id,
                        ts=event.ts,
                    )
                )
                continue

        # Explicit choices: user prose or main-thread assistant prose.
        if event.text and not event.is_meta and not event.is_compact_summary:
            if event.kind == "user" and not _is_prose_user_text(event):
                continue
            if event.kind == "assistant" and event.sidechain:
                continue
            if event.kind not in ("user", "assistant"):
                continue
            sentences = _choice_sentences(event.text)
            if sentences:
                _add(
                    SessionCandidate(
                        kind="explicit_choice",
                        quotes=[_clip(s) for s in sentences],
                        files=list(dict.fromkeys(trailing_files)),
                        session_id=event.session_id,
                        ts=event.ts,
                    )
                )

    # A choice with no code in play is a conversation, not a decision record.
    return [c for c in candidates if c.kind != "explicit_choice" or c.files]


# ---------------------------------------------------------------------------
# Stage 2: one batched LLM structuring pass, substring-gated
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an architectural decision extractor. You extract durable, "
    "codebase-level decisions from coding-agent session transcripts. "
    "Return only valid JSON. Never invent rationale not present in the source."
)

SESSION_MINING_PROMPT = """\
Below are excerpts from coding-agent sessions in one repository. Each \
candidate is a moment where a durable decision about this codebase may have \
been made: the user correcting the agent, an explicit choice with a stated \
reason, or a failed approach replaced by a working one.

{candidates_block}

For each candidate that records a DURABLE decision or rule (something every \
future session should follow), return a JSON object:
{{
  "candidate": <the candidate number>,
  "title": "short imperative title",
  "decision": "what to do (or avoid), grounded in the excerpt",
  "rationale": "why, only if the excerpt states it",
  "affected_files": ["subset of the candidate's files this governs"],
  "source_quote": "one sentence copied verbatim from the excerpt"
}}

Skip candidates that are one-off task instructions, session-specific \
guidance, questions, or venting. Return a JSON array; [] if none qualify.
"""

#: Cap on raw candidates structured per update; the remainder stays staged
#: and is picked up by the next update's pass.
MAX_STRUCTURED_PER_UPDATE = 60
_LLM_CHUNK = 12

# This miner needs user prose, assistant prose, tool uses, and results; in
# Claude Code all of them are "user"/"assistant" entries. Both compact and
# spaced JSON spellings are matched; what this skips is the fat non-dialog
# lines (file-history snapshots, queue operations, system hooks).
_PREFILTER_TOKENS = (
    '"type":"user"',
    '"type": "user"',
    '"type":"assistant"',
    '"type": "assistant"',
)


def _prefilter(raw: str) -> bool:
    return any(tok in raw for tok in _PREFILTER_TOKENS)


def session_mining_enabled(repo_config: dict[str, Any] | None) -> bool:
    """Resolve the ``decisions.session_mining`` config gate (default on)."""
    cfg = repo_config or {}
    decisions_cfg = cfg.get("decisions") or {}
    if not isinstance(decisions_cfg, dict):
        return True
    return decisions_cfg.get("session_mining", True) is not False


def _candidates_block(raws: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, raw in enumerate(raws):
        files = ", ".join(raw["files"][:8]) or "(none recorded)"
        quotes = "\n".join(raw["quotes"])
        parts.append(
            f"--- Candidate {i} ({raw['kind']}) ---\n"
            f"Files in play: {files}\n"
            f"Transcript excerpt:\n{quotes}\n"
        )
    return "\n".join(parts)


def _parse_structured(content: str) -> list[dict[str, Any]]:
    """Parse the LLM response into candidate-indexed objects (tolerant)."""
    content = content.strip()
    if content.startswith("```"):
        content = "\n".join(
            line for line in content.splitlines() if not line.strip().startswith("```")
        )
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = [data]
    return [item for item in data if isinstance(item, dict)]


_PUNCT_RE = re.compile(r"[^\w\s]")


def _gate_structured(item: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any] | None:
    """Ground one structured candidate in its verbatim transcript quotes.

    The product guarantee, adapted to this source: ``source_quote`` must
    verify (exact or fuzzy) against the excerpt, since that quote is the evidence
    a human reviews. The ``decision`` is by design a normalization of
    informal transcript language ("dont" becomes "do not"), so it is held to
    a punctuation-stripped content-word overlap with the excerpt rather than
    the full 0.6 token gate, and a ``rationale`` that fails the same check
    is dropped (never invented "why") without killing the candidate.
    Returns the gated dict, or None when the candidate is rejected.
    """
    title = str(item.get("title") or "").strip()
    decision = str(item.get("decision") or "").strip()
    if not title or not decision:
        return None
    source_text = "\n".join(raw["quotes"])
    verification = verify_quote(str(item.get("source_quote") or ""), source_text)
    if verification == "unverified":
        return None
    plain_source = _PUNCT_RE.sub(" ", source_text)
    plain_decision = _PUNCT_RE.sub(" ", decision)
    if verify_quote(plain_decision, plain_source, fuzzy_threshold=0.3) == "unverified":
        return None  # a wild claim riding a valid quote is still rejected
    rationale = str(item.get("rationale") or "").strip()
    if rationale:
        plain_rationale = _PUNCT_RE.sub(" ", rationale)
        if verify_quote(plain_rationale, plain_source, fuzzy_threshold=0.5) == "unverified":
            rationale = ""
    claimed = item.get("affected_files")
    files = [f for f in claimed if f in raw["files"]] if isinstance(claimed, list) else []
    if not files and raw["kind"] != "user_correction":
        # A choice/dead end is about the code in play; a correction with no
        # named files is a repo-wide rule, and linking it to whatever files
        # happened to be open would govern the wrong code (dogfood: the
        # em-dash rule pinned to an unrelated docs file).
        files = raw["files"]
    return {
        "title": title,
        "decision": decision,
        "rationale": rationale,
        "source_quote": str(item.get("source_quote") or "").strip(),
        "verification": verification,
        "affected_files": files,
    }


# ---------------------------------------------------------------------------
# Stage 3: promotion into decision_records dicts
# ---------------------------------------------------------------------------

_MAX_EVIDENCE_SESSIONS = 5


def _relative_files(files: list[str], repo_root: Path) -> list[str]:
    """Repo-relative POSIX paths; files outside the repo are dropped."""
    import os.path

    out: list[str] = []
    for f in files:
        try:
            rel = os.path.relpath(f, str(repo_root))
        except (ValueError, OSError):
            continue
        if not rel.startswith(".."):
            out.append(rel.replace("\\", "/"))
    return list(dict.fromkeys(out))


def _promotion_decisions(row: dict[str, Any], repo_root: Path) -> list[ExtractedDecision]:
    """decision_records-ready members for one promotable staging row.

    One member per observing session (capped) so each session becomes its own
    evidence row via the ``bulk_upsert_decisions`` accretion path. The first
    promotion lands ``active``; later observation-driven re-emissions land
    ``proposed``, which adds evidence but can never overwrite a status a
    human (or the evolution judge) set deliberately.
    """
    structured = row["structured"]
    status = "active" if row["first_promotion"] else "proposed"
    files = _relative_files(structured.get("affected_files") or row["files"], repo_root)
    modules = sorted({f.rsplit("/", 1)[0] for f in files if "/" in f})
    confidence = compute_confidence(
        rank_for_source("session"),
        row["observations"],
        structured.get("verification", "unverified"),
    )
    sessions = row["sessions"][-_MAX_EVIDENCE_SESSIONS:] or [None]
    return [
        ExtractedDecision(
            title=row["title"],
            decision=structured.get("decision", ""),
            rationale=structured.get("rationale", ""),
            affected_files=files,
            affected_modules=modules,
            source="session",
            evidence_commits=[sid] if sid else [],
            confidence=confidence,
            status=status,
            source_quote=structured.get("source_quote", ""),
            verification=structured.get("verification", "unverified"),
        )
        for sid in sessions
    ]


# ---------------------------------------------------------------------------
# Usage feedback v1: were injected decisions followed or contradicted?
# ---------------------------------------------------------------------------

#: An injection is judged only after this long: the showing session must have
#: had time to react (or end) before "no contradiction" reads as "followed".
INJECTION_EVAL_MIN_AGE_SECONDS = 3600.0

#: Staleness levels mirroring run_update_evolution's amended/reaffirmed moves.
_CONTRADICTED_STALENESS = 0.6
_FOLLOWED_STALENESS = 0.2


async def apply_injection_feedback(
    db_session: Any,
    repository_id: str,
    repo_path: Path,
    *,
    now: float | None = None,
) -> dict[str, int]:
    """Judge shown-decision injections against what the session actually did.

    For every injection row the augment hooks recorded (see the staging
    sidecar's ``injections`` table), check the same session's mined user
    corrections: a correction that contradicts the shown decision (the
    :func:`~repowise.core.analysis.decisions.evolution.contradicts` heuristic)
    marks it contradicted and bumps staleness so the evolution machinery
    surfaces the drift; otherwise the guidance counts as followed and
    staleness relaxes, the same reaffirm move ``run_update_evolution`` makes.

    Deliberately binary for v1 (followed / contradicted); the
    followed-vs-ignored split and relevance decay are the validation-gated
    backlog item that rides on this data. Returns
    ``{"followed": n, "contradicted": n}``.
    """
    import time

    from sqlalchemy import select

    from repowise.core.analysis.decisions.evolution import contradicts
    from repowise.core.persistence.models import DecisionRecord

    ts = now if now is not None else time.time()
    summary = {"followed": 0, "contradicted": 0}

    store = SessionStagingStore.open_default(Path(repo_path).resolve())
    try:
        injections = store.unevaluated_injections(before=ts - INJECTION_EVAL_MIN_AGE_SECONDS)
        if not injections:
            return summary

        decision_ids = list({inj["decision_id"] for inj in injections})
        rows = await db_session.execute(
            select(DecisionRecord).where(
                DecisionRecord.id.in_(decision_ids),
                DecisionRecord.repository_id == repository_id,
            )
        )
        records = {rec.id: rec for rec in rows.scalars().all()}

        quotes_by_session: dict[str, list[str]] = {}
        verdicts: dict[str, bool] = {}  # decision_id -> contradicted anywhere
        for inj in injections:
            rec = records.get(inj["decision_id"])
            if rec is None:
                # The decision no longer exists in this repo's records; drop
                # the row so it is not re-examined forever.
                store.mark_injection_evaluated(inj["session_id"], inj["decision_id"])
                continue
            session_id = inj["session_id"]
            if session_id not in quotes_by_session:
                quotes_by_session[session_id] = store.correction_quotes(session_id)
            decision_text = f"{rec.title}. {rec.decision}"
            contradicted = any(
                contradicts(decision_text, quote)[0] for quote in quotes_by_session[session_id]
            )
            verdicts[rec.id] = verdicts.get(rec.id, False) or contradicted
            store.mark_injection_evaluated(session_id, inj["decision_id"])

        from datetime import UTC, datetime

        for decision_id, contradicted in verdicts.items():
            rec = records[decision_id]
            if contradicted:
                rec.staleness_score = max(rec.staleness_score, _CONTRADICTED_STALENESS)
                summary["contradicted"] += 1
            else:
                rec.staleness_score = min(rec.staleness_score, _FOLLOWED_STALENESS)
                summary["followed"] += 1
            rec.updated_at = datetime.now(UTC)

        store.commit()
        await db_session.flush()
    finally:
        store.close()

    if summary["followed"] or summary["contradicted"]:
        logger.info("session_mining.injection_feedback", **summary)
    return summary


async def mine_session_decisions(
    repo_path: Path,
    *,
    provider: Any,
    projects_root: Path | None = None,
    max_structured: int = MAX_STRUCTURED_PER_UPDATE,
    now: float | None = None,
) -> list[ExtractedDecision]:
    """Mine, structure, and promote session decisions for one repo.

    Reads only transcript lines appended since the last run (cursors live in
    the staging DB and only advance in the same commit that stages what was
    read), runs the batched LLM pass over pending candidates, and returns the
    decisions that qualify for promotion, ready for the caller's normal
    ``bulk_upsert_decisions`` path. Best-effort at the file level; a failed
    LLM call leaves candidates staged for the next update.
    """
    repo_root = Path(repo_path).resolve()
    repo_prefix = str(repo_root).lower().rstrip("\\/")
    adapter = ClaudeCodeAdapter()

    store = SessionStagingStore.open_default(repo_root)
    try:
        # Stage new gate hits from transcript lines appended since last run.
        staged = 0
        for path in adapter.discover(repo_root, projects_root=projects_root):
            try:
                events = iter_new_events(adapter, path, store.cursors, prefilter=_prefilter)
                for candidate in mine_events(events, repo_prefix):
                    if store.add_raw(
                        hash_=candidate.hash,
                        kind=candidate.kind,
                        quotes=candidate.quotes,
                        files=candidate.files,
                        session_id=candidate.session_id,
                        now=now,
                    ):
                        staged += 1
            except OSError:
                continue
        store.prune(now=now)
        store.cursors.save()  # commits the staged raws atomically with the cursors

        # One batched structuring pass over whatever is pending (this run's
        # hits plus any backlog a previous failed call left behind).
        pending = store.pending_raws(max_structured)
        structured_count = 0
        processed = 0
        for start in range(0, len(pending), _LLM_CHUNK):
            chunk = pending[start : start + _LLM_CHUNK]
            prompt = SESSION_MINING_PROMPT.format(candidates_block=_candidates_block(chunk))
            try:
                response = await provider.generate(
                    _SYSTEM_PROMPT, prompt, max_tokens=2000, temperature=0.2
                )
            except Exception as exc:
                logger.warning("session_mining.llm_failed", error=str(exc))
                break  # pending raws stay staged; next update retries
            processed += len(chunk)
            by_index = {}
            for item in _parse_structured(response.content):
                idx = item.get("candidate")
                if isinstance(idx, int) and 0 <= idx < len(chunk):
                    by_index[idx] = item
            for i, raw in enumerate(chunk):
                gated = _gate_structured(by_index[i], raw) if i in by_index else None
                if gated is None:
                    store.mark_raw_rejected(raw["hash"])
                    continue
                store.upsert_structured(
                    raw["hash"],
                    kind=raw["kind"],
                    title=gated["title"],
                    structured=gated,
                    quotes=raw["quotes"],
                    files=gated["affected_files"],
                    session_id=raw["session_id"],
                    now=now,
                )
                structured_count += 1
            store.commit()

        # Promotion: observation-qualified decisions, ready for upsert.
        decisions: list[ExtractedDecision] = []
        for row in store.promotable():
            decisions.extend(_promotion_decisions(row, repo_root))
            store.mark_emitted(row["key"], observations=row["observations"], now=now)
        store.commit()

        logger.info(
            "session_mining.done",
            staged=staged,
            structured=structured_count,
            pending_backlog=max(0, len(pending) - processed),
            promoted=len(decisions),
        )
        return decisions
    finally:
        store.close()
