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
   (config-gated ``decisions.sources.session``, off by default): candidates to
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
quotes as evidence. The lane ships off; ``repowise decision source set
session --on`` (or ``decisions.sources.session: true`` in
``.repowise/config.yaml``) turns it back on.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import deque
from collections.abc import Container, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.decisions.discovery.spans import SpanCollector
from repowise.core.analysis.decisions.extractor import ExtractedDecision
from repowise.core.analysis.decisions.kinds import classify_kind
from repowise.core.analysis.decisions.lifecycle import AGREEMENT_KIND, bundles_decisions
from repowise.core.analysis.decisions.policy import DEFAULT_HARNESSES, resolve_policy
from repowise.core.analysis.decisions.provenance import (
    completeness,
    compute_confidence,
    rank_for_source,
    verify_quote,
)
from repowise.core.analysis.decisions.rationale_comments import CAUSAL_MARKERS
from repowise.core.analysis.decisions.scope import (
    bind_scope_files,
    resolve_module_nodes,
    session_scope_basis,
)
from repowise.core.distill.corrections import command_anchor
from repowise.core.precedent.transcript_episodes import (
    TranscriptEpisodeRecorder,
    record_transcript_episodes,
)
from repowise.core.sessions import INTENT_TURNS, Event, get_adapter
from repowise.core.sessions.adapters.registry import DEFAULT_ADAPTER, registered_adapters
from repowise.core.sessions.cursor import iter_new_events
from repowise.core.sessions.events import (
    FILE_INPUT_KEYS,
    event_file_touches,
    is_prose_user_text,
    relative_files,
)
from repowise.core.sessions.staging import DISCOVERY_KIND, SessionStagingStore

logger = structlog.get_logger(__name__)

__all__ = [
    "SessionCandidate",
    "apply_injection_feedback",
    "mine_events",
    "mine_session_decisions",
    "promotion_decisions",
    "session_mining_enabled",
]

# ---------------------------------------------------------------------------
# Gate vocabularies, precision-first on purpose. A missed decision costs one
# session of memory; a false one pollutes the record for every future session.
# ---------------------------------------------------------------------------

#: A sentence opening with one of these reads as pushback on what the agent
#: just did or proposed. Matched at the start of any *sentence*, not only of
#: the message: corrections often arrive as one sentence inside a longer brief,
#: and missing them matters because :func:`apply_injection_feedback` reads
#: silence as "followed".
#:
#: ``actually`` is deliberately absent: mid-message it is mostly narrative
#: ("actually produces.") rather than pushback.
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
)

#: A sentence needs one of these to read as a choice being made (paired with
#: a :data:`CAUSAL_MARKERS` cue for the stated reason). Word-bounded so e.g.
#: "beca**use** it" never reads as the verb "use". Excludes "instead of" /
#: "rather than": they are causal cues already, and one phrase satisfying both
#: conditions turns every narrated trade-off into a candidate.
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

#: Wall-clock ceiling on one run's transcript sweep. The corpus grows with how
#: much the user has worked, not with the repository, and a first read starts
#: every cursor at byte 0. Stopping is safe, not lossy: cursors are per file and
#: saved after the loop, so the next run resumes where this one stopped.
SWEEP_BUDGET_S = 5.0

_EXIT_CODE_RE = re.compile(r"^Error: Exit code (\d+)")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class SessionCandidate:
    """One gate hit: a moment where a durable decision may have been made."""

    kind: str  # user_correction | explicit_choice | dead_end
    quotes: list[str]
    files: list[str] = field(default_factory=list)
    #: Which of ``files`` this candidate saw changed rather than only read.
    #: Ordering input only, never staged; staged rows are never rewritten, so
    #: the order must be right the first time.
    edited: set[str] = field(default_factory=set)
    session_id: str | None = None
    ts: float | None = None

    @property
    def hash(self) -> str:
        """Content identity for staging dedup (kind + normalized quotes).

        Deliberately session-independent: ``add_raw`` is INSERT OR IGNORE on
        this, so one quote is structured by the LLM once however many sessions
        produced it.

        **Known ceiling.** ``raw_candidates`` keeps only the first
        ``session_id`` per hash, so a correction repeated in later sessions
        (typically a standing rule) yields no
        :meth:`~SessionStagingStore.correction_quotes` for them, and a
        structured row is never pruned, so this persists. It fails safe: those
        sessions become unjudgeable, not falsely "followed". The upgrade is a
        raw-to-session association, like the promoted row's ``sessions`` list,
        rather than a session-scoped hash.
        """
        norm = " ".join(" ".join(q.lower().split()) for q in self.quotes)
        return hashlib.sha256(f"{self.kind}|{norm}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Stage 1: deterministic gates over one session's event stream
# ---------------------------------------------------------------------------


def _clip(text: str, cap: int = _QUOTE_CAP) -> str:
    text = text.strip()
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _interrupt_guidance(text: str) -> str:
    """The user's own words in an interrupt event, marker lines dropped."""
    from repowise.core.sessions import INTERRUPT_MARKER

    lines = [ln for ln in text.splitlines() if INTERRUPT_MARKER not in ln]
    return "\n".join(lines).strip()


def _pushback_sentences(text: str) -> list[str]:
    """Sentences that open with a pushback lead, verbatim, capped at two.

    Scans sentences so a correction buried in a longer brief still counts (see
    :data:`PUSHBACK_LEADS`). Returns the sentences alone: ``contradicts()``
    compares them against a single decision statement, and the clause is a
    better input than the whole brief.

    **Two, not one.** Declarative sentences ("No releases yet.") look identical
    to directive rules ("No em dashes.") to any cheap rule, so they are
    accepted as residue; keeping only the first match would let a declarative
    opener discard the real correction behind it.
    """
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if len(sentence) >= 12 and sentence.lower().startswith(PUSHBACK_LEADS):
            out.append(_clip(sentence))
            if len(out) == _MAX_QUOTES_PER_EVENT:
                break
    return out


def _correction_quotes(event: Event) -> list[str]:
    """The verbatim correction text, or empty when the gate does not fire."""
    if event.interrupted:
        guidance = _interrupt_guidance(event.text)
        return [_clip(guidance)] if len(guidance) >= 8 else []
    return _pushback_sentences(event.text)


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
    for key in FILE_INPUT_KEYS:
        value = use_input.get(key)
        if isinstance(value, str) and value.strip():
            basename = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
            return f"{name}:{basename}"
    return name.lower()


def _repo_relative_touches(
    touches: list[tuple[str, str]], repo_root: Any
) -> list[tuple[str, str]]:
    """``(path, intent)`` with each path repo-relative POSIX, outsiders dropped.

    Transcripts record absolute paths; the index matches repo-relative ones,
    so paths are normalized here, the single point a touch enters the miner.
    """
    out: list[tuple[str, str]] = []
    for path, intent in touches:
        relative = relative_files([path], repo_root)
        if relative:
            out.append((relative[0], intent))
    return out


def mine_events(
    events: Iterable[Event],
    repo_root: Any,
    *,
    edit_tools: Container[str] = frozenset(),
) -> list[SessionCandidate]:
    """Run the deterministic candidate gates over one session's events.

    *repo_root* is the resolved repository root; only events whose ``cwd``
    sits inside it count (same scoping as the distill miners), and every file
    a candidate carries is stated relative to it. Pure and streaming: state
    is bounded regardless of transcript size.

    A file touched outside the root is dropped: the index cannot resolve it,
    and a scope of unresolvable paths is worse than an empty one.

    *edit_tools* is the producing adapter's edit vocabulary. It orders each
    candidate's files, changed ones first, because a decision is about the
    code that moved.
    """
    repo_prefix = str(repo_root).lower().rstrip("\\/")
    candidates: list[SessionCandidate] = []
    #: (path, intent) for the recent file-touching calls, so a candidate opened
    #: here knows which of the files in play were being changed at the time.
    trailing_files: deque[tuple[str, str]] = deque(maxlen=_TRAILING_FILES)
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
            touches = _repo_relative_touches(
                event_file_touches(event, edit_tools=edit_tools), repo_root
            )
            files = [path for path, _ in touches]
            trailing_files.extend(touches)
            changed = {path for path, intent in touches if intent == "edit"}
            for entry in open_candidates:
                entry[0].files.extend(f for f in files if f not in entry[0].files)
                entry[0].edited |= changed
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
                                files=list(dict.fromkeys(p for p, _ in trailing_files)),
                                edited={p for p, i in trailing_files if i == "edit"},
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

        if is_prose_user_text(event):
            quotes = _correction_quotes(event)
            if quotes:
                _add(
                    SessionCandidate(
                        kind="user_correction",
                        quotes=quotes,
                        files=list(dict.fromkeys(p for p, _ in trailing_files)),
                        edited={p for p, i in trailing_files if i == "edit"},
                        session_id=event.session_id,
                        ts=event.ts,
                    )
                )
                # Falls through to the choice gate: a brief that contains a
                # correction can also state explicit choices.

        # Explicit choices: user prose or main-thread assistant prose.
        if event.text and not event.is_meta and not event.is_compact_summary:
            if event.kind == "user" and not is_prose_user_text(event):
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
                        files=list(dict.fromkeys(p for p, _ in trailing_files)),
                        edited={p for p, i in trailing_files if i == "edit"},
                        session_id=event.session_id,
                        ts=event.ts,
                    )
                )

    for candidate in candidates:
        candidate.files.sort(key=lambda f: f not in candidate.edited)

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


def session_mining_enabled(repo_config: dict[str, Any] | None) -> bool:
    """Whether the transcript miner may run at all.

    Resolved from the shared policy, which still honours the legacy
    ``decisions.session_mining`` boolean.
    """
    return resolve_policy(repo_config).policy.source_enabled("session")


def harnesses_for(repo_path: Path) -> tuple[str, ...]:
    """Harnesses this repo reads transcripts from, from its own config.

    The fallback for a caller that holds no resolved policy; callers that hold
    one pass it, so the setting is not read twice.
    """
    from repowise.core.repo_config import load_repo_config

    try:
        policy = resolve_policy(load_repo_config(repo_path)).policy
    except Exception:
        return DEFAULT_HARNESSES
    return registered_harnesses(policy.harnesses)


def registered_harnesses(names: Sequence[str]) -> tuple[str, ...]:
    """*names* that name a registered adapter, never empty.

    An unregistered name is dropped, not raised on, so a config from a newer
    repowise cannot stop indexing. Never empty, because an empty reader list
    would be indistinguishable from a repository with no sessions.
    """
    known = set(registered_adapters())
    return tuple(name for name in names if name in known) or DEFAULT_HARNESSES


def _sweep_harness(
    harness: str,
    *,
    repo_root: Path,
    projects_root: Path | None,
    store: SessionStagingStore,
    recorder: TranscriptEpisodeRecorder,
    collector: SpanCollector | None,
    budget: float,
    now: float | None,
) -> dict[str, int]:
    """Read one harness's new transcript lines, and report what it did.

    Counts are kept apart so a reader that stopped reading shows *read* zero
    beside a non-zero *discovered*.
    """
    adapter = get_adapter(harness)
    # This miner needs user prose, assistant prose, tool uses and results:
    # everything the conversation carries, minus the fat non-dialog lines.
    prefilter = adapter.prefilter(INTENT_TURNS)
    deadline = time.monotonic() + budget
    discovered = adapter.discover(repo_root, projects_root=_root_for(harness, projects_root))
    counts = {"discovered": len(discovered), "read": 0, "found": 0, "staged": 0, "deferred": 0}
    # Discovered transcripts are present whether or not this run reads them.
    recorder.note_present(discovered)
    for index, path in enumerate(discovered):
        if time.monotonic() > deadline:
            # Safe to stop: see SWEEP_BUDGET_S.
            counts["deferred"] = len(discovered) - index
            break
        try:
            events = iter_new_events(adapter, path, store.cursors, prefilter=prefilter)
            stream = recorder.observe(path, events)
            if collector is not None:
                stream = collector.observe(stream)
            for candidate in mine_events(
                stream, repo_root, edit_tools=adapter.edit_tool_names
            ):
                counts["found"] += 1
                if store.add_raw(
                    hash_=candidate.hash,
                    kind=candidate.kind,
                    quotes=candidate.quotes,
                    files=candidate.files,
                    session_id=candidate.session_id,
                    harness=harness,
                    now=now,
                ):
                    counts["staged"] += 1
            counts["read"] += 1
        except OSError:
            continue
    return counts


def _root_for(harness: str, projects_root: Path | None) -> Path | None:
    """The transcript-root override, per harness.

    The override is a sandbox: no harness may read the real home directory
    when one is given. The default harness keeps the root itself; every other
    harness gets a subdirectory, absent unless the caller made one.
    """
    if projects_root is None:
        return None
    return projects_root if harness == DEFAULT_ADAPTER else projects_root / harness


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
    # Iterate the mined list rather than the model's: same set either way,
    # and the mined order is the one carrying what the session edited.
    files = [f for f in raw["files"] if f in claimed] if isinstance(claimed, list) else []
    if not files and raw["kind"] != "user_correction":
        # A choice/dead end is about the code in play; a correction with no
        # named files is a repo-wide rule, and linking it to whatever was open
        # would govern the wrong code.
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


def _staged_files(structured: dict[str, Any], row: dict[str, Any]) -> list[str]:
    """The files a staged row claims, distinguishing empty from absent.

    An empty list is deliberate (a repo-wide correction; see
    ``_gate_structured``). Only a row with no structured claim falls back to
    the gate hits staging accreted.
    """
    claimed = structured.get("affected_files")
    return list(claimed) if claimed is not None else list(row["files"])


def promotion_decisions(
    row: dict[str, Any],
    repo_root: Path,
    *,
    indexed: Container[str] | None = None,
) -> list[ExtractedDecision]:
    """decision_records-ready members for one promotable staging row.

    One member per observing session (capped) so each session becomes its own
    evidence row via the ``bulk_upsert_decisions`` accretion path.

    Every member lands ``proposed``, first promotion included. Recurrence
    across sessions is evidence that a candidate is worth reviewing, not an
    acceptance event: authority comes from a person confirming the record.
    ``first_promotion`` still gates re-emission in the staging store, so a
    recurring candidate accretes evidence without re-proposing itself.

    *indexed* is the indexed file set. Binding, classification and the split
    flag are applied here rather than at staging, so older staged rows are
    judged on their way out instead of staying stale.
    """
    structured = row["structured"]
    # Both session lanes store source="session"; the staging kind is what tells
    # a reviewer which one raised the candidate.
    lane = DISCOVERY_KIND if row.get("kind") == DISCOVERY_KIND else "session"
    files = bind_scope_files(
        relative_files(_staged_files(structured, row), repo_root),
        indexed,
    )
    modules = resolve_module_nodes(files)
    # Staging carries a decision and a rationale and no more, so a promoted
    # record is thin by construction and is scored as such.
    confidence = compute_confidence(
        rank_for_source("session"),
        row["observations"],
        structured.get("verification", "unverified"),
        filled_fields=completeness(
            decision=structured.get("decision"),
            rationale=structured.get("rationale"),
        ),
    )
    sessions = row["sessions"][-_MAX_EVIDENCE_SESSIONS:] or [None]
    # The discovery lane's grounding-time answer is kept and only ever raised,
    # so the two lanes cannot disagree by which ran first.
    needs_split = bool(structured.get("needs_split")) or bundles_decisions(
        structured.get("decision", "") or ""
    )
    kind = classify_kind(
        row["title"],
        structured.get("decision", ""),
        structured.get("rationale", "") or "",
        source="session",
    )
    # The files stay on the record; whether they are a claim about those files
    # is a separate question, and this is where both answers are known.
    scope_basis = session_scope_basis(files, is_agreement=kind == AGREEMENT_KIND)
    return [
        ExtractedDecision(
            title=row["title"],
            decision=structured.get("decision", ""),
            rationale=structured.get("rationale", ""),
            affected_files=files,
            affected_modules=modules,
            scope_basis=scope_basis,
            source="session",
            evidence_commits=[sid] if sid else [],
            confidence=confidence,
            status="proposed",
            kind=kind,
            source_quote=structured.get("source_quote", ""),
            verification=structured.get("verification", "unverified"),
            lane=lane,
            needs_split=needs_split,
        )
        for sid in sessions
    ]


# ---------------------------------------------------------------------------
# Usage feedback v1: were injected decisions followed or contradicted?
# ---------------------------------------------------------------------------

#: An injection is judged only after this long: the showing session must have
#: had time to react (or end) before "no contradiction" reads as "followed".
INJECTION_EVAL_MIN_AGE_SECONDS = 3600.0


async def _records_by_alias(
    db_session: Any, repository_id: str, alias_ids: list[str]
) -> dict[str, Any]:
    """Live records for retired ids, keyed by the **retired** id.

    Keyed by the alias so the caller can look a sidecar row up under the id it
    stored. One hop: re-keying repoints existing aliases rather than chaining
    (``decision_aliases.decision_id`` is in ``_DEPENDENT_COLUMNS``), and where
    a merge chains ``A -> B -> C``, ``B`` is live and its text is what was shown.
    """
    if not alias_ids:
        return {}

    from sqlalchemy import select

    from repowise.core.persistence.models import DecisionAlias, DecisionRecord

    rows = await db_session.execute(
        select(DecisionAlias.alias_id, DecisionRecord)
        .join(DecisionRecord, DecisionRecord.id == DecisionAlias.decision_id)
        .where(
            DecisionAlias.alias_id.in_(alias_ids),
            DecisionAlias.repository_id == repository_id,
            DecisionRecord.repository_id == repository_id,
        )
    )
    return {alias_id: rec for alias_id, rec in rows.all()}


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
    marks it contradicted; otherwise the guidance counts as followed. The
    verdict lives on the injection row only; it must not touch the decision's
    ``staleness_score``, which measures whether the governed files moved.

    **A decision no session could have contradicted is not "followed".** A
    session with no mined correction is settled with no verdict (the
    ``no_verdict`` bucket), so the followed rate covers judgeable injections
    only.

    Deliberately binary (followed / contradicted). Returns
    ``{"followed": n, "contradicted": n, "unjudgeable": n}``, counted over
    **ledger rows** so it reconciles with
    :meth:`~SessionStagingStore.decision_feedback_totals`.
    """
    import time

    from sqlalchemy import select

    from repowise.core.analysis.decisions.evolution import contradicts
    from repowise.core.persistence.models import DecisionRecord

    ts = now if now is not None else time.time()
    summary = {"followed": 0, "contradicted": 0, "unjudgeable": 0}

    store = SessionStagingStore.open_default(Path(repo_path).resolve())
    try:
        # One-shot repairs of verdicts older versions awarded without evidence;
        # already-evaluated rows are otherwise never revisited.
        retired = store.retire_unjudgeable_verdicts()
        # **Order is load-bearing**: both repairs ride one `PRAGMA user_version`
        # and this one writes the higher number, so reversing them skips the
        # retirement for good.
        reopened = store.reopen_smeared_contradictions()
        # Commit even when nothing matched: the "already repaired" mark must
        # persist, or the repair re-arms and later fires on earned verdicts
        # once RAW_TTL_DAYS has pruned the corrections.
        store.commit()
        if retired or reopened:
            logger.info(
                "session_mining.injection_verdicts_repaired",
                retired=retired,
                reopened=reopened,
            )

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
        # A record's id moves with its scope and the sidecar is not rewritten,
        # so without the aliases earned feedback reads as "decision gone".
        records |= await _records_by_alias(
            db_session, repository_id, [d for d in decision_ids if d not in records]
        )

        quotes_by_session: dict[str, list[str]] = {}
        #: (session_id, decision_id, judgeable, this session's own verdict).
        #: **Both flags are per row**: the totals count rows, so a shared
        #: verdict would be multiplied by the number of sessions.
        judged: list[tuple[str, str, bool, bool]] = []
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
            quotes = quotes_by_session[session_id]
            decision_text = f"{rec.title}. {rec.decision}"
            contradicted = any(contradicts(decision_text, quote)[0] for quote in quotes)
            judged.append((session_id, inj["decision_id"], bool(quotes), contradicted))

        # Each row settles on its own session's corrections: a decision
        # contradicted in one session says nothing about a session that mined
        # different corrections, or none. Stored so `hook stats` can report it.
        for session_id, decision_id, judgeable, contradicted in judged:
            if not judgeable:
                # Settled with no verdict: nothing mined could have disagreed.
                store.mark_injection_evaluated(session_id, decision_id)
                summary["unjudgeable"] += 1
                continue
            store.mark_injection_evaluated(
                session_id,
                decision_id,
                verdict="contradicted" if contradicted else "followed",
            )
            summary["contradicted" if contradicted else "followed"] += 1

        store.commit()
    finally:
        store.close()

    if any(summary.values()):
        logger.info("session_mining.injection_feedback", **summary)
    return summary


async def mine_session_decisions(
    repo_path: Path,
    *,
    provider: Any | None,
    projects_root: Path | None = None,
    harnesses: Sequence[str] | None = None,
    max_structured: int = MAX_STRUCTURED_PER_UPDATE,
    collect_discovery_spans: bool = False,
    indexed: Container[str] | None = None,
    now: float | None = None,
) -> list[ExtractedDecision]:
    """Read this repo's new transcript lines once, and serve both consumers.

    Reads only transcript lines appended since the last run (cursors live in
    the staging DB and only advance in the same commit that stages what was
    read), runs the batched LLM pass over pending candidates, and returns the
    decisions that qualify for promotion, ready for the caller's normal
    ``bulk_upsert_decisions`` path. Best-effort at the file level; a failed
    LLM call leaves candidates staged for the next update.

    The same pass records one transcript episode per session and, with
    *collect_discovery_spans*, queues the prose the discovery lane consumes.
    Both ride this stream because the cursor advances as bytes are read: a
    second reader would find nothing left.

    *provider* may be ``None``: discovery, folding and staging are keyless;
    only structuring needs a model.

    *indexed* is the indexed file set, which bounds what a promoted record may
    claim to govern; without it scope is not bound.
    """
    repo_root = Path(repo_path).resolve()
    # The caller's resolved policy wins over re-reading config.
    names = registered_harnesses(harnesses) if harnesses is not None else harnesses_for(repo_path)
    # One recorder and one write: the episode writer infers absence by
    # negation, so a per-harness write would mark the others' sources gone.
    recorder = TranscriptEpisodeRecorder(repo_root)

    store = SessionStagingStore.open_default(repo_root)
    collector = SpanCollector(store, repo_root, now=now) if collect_discovery_spans else None
    try:
        # Stage new gate hits from transcript lines appended since last run.
        staged = 0
        deferred = 0
        yields: dict[str, dict[str, int]] = {}
        # Split the budget, or the first harness on a cold corpus starves the
        # rest every run.
        budget = SWEEP_BUDGET_S / len(names)
        for name in names:
            try:
                yields[name] = _sweep_harness(
                    name,
                    repo_root=repo_root,
                    projects_root=projects_root,
                    store=store,
                    recorder=recorder,
                    collector=collector,
                    budget=budget,
                    now=now,
                )
            except Exception as exc:
                # The cursor save below is shared, so one harness's failure
                # must not discard the others' progress.
                logger.warning("session_mining.harness_failed", harness=name, error=str(exc))
                continue
            staged += yields[name]["staged"]
            deferred += yields[name]["deferred"]
        store.prune(now=now)
        store.cursors.save()  # commits the staged raws atomically with the cursors

        # After the cursors commit: the episode store is a separate sidecar,
        # and must not describe bytes the cursor still counts as unread.
        episodes = record_transcript_episodes(repo_root, recorder)

        # One batched structuring pass over whatever is pending. Queried even
        # with no provider so the logged backlog is real on keyless runs.
        pending = store.pending_raws(max_structured)
        structured_count = 0
        processed = 0
        chunk_starts = [] if provider is None else range(0, len(pending), _LLM_CHUNK)
        for start in chunk_starts:
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
            if row["kind"] == DISCOVERY_KIND:
                continue  # the broad lane runs its own promotion, under its own rules
            decisions.extend(promotion_decisions(row, repo_root, indexed=indexed))
            store.mark_emitted(row["key"], observations=row["observations"], now=now)
        store.commit()

        logger.info(
            "session_mining.done",
            staged=staged,
            structured=structured_count,
            pending_backlog=max(0, len(pending) - processed),
            discovery_spans=collector.queued if collector else 0,
            # Per harness, so an empty corpus and an unread one differ; a
            # harness that did not run or failed has no key.
            yields=yields,
            promoted=len(decisions),
            episodes=episodes,
            transcripts_deferred=deferred,
        )
        return decisions
    finally:
        store.close()
