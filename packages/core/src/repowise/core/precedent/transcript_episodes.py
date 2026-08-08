"""Transcript episodes — one agent session, kept whole, bound to what it touched.

The third and last episode tier, and the only one that is **not shareable**:
structural and git episodes are facts about a repository and travel with it,
while a transcript exists on one laptop. It is labelled ``transcript`` in every
row it writes so a reader can decline it, and no value derived here feeds a
stored score another surface reads.

**Why a whole session rather than a fact mined out of one.** Distillation and
retrieval are different systems with opposite answers on this corpus: mining
sessions for durable records yields ~0.06 per session, while *finding a session
again* months later answered 68% of questions at rank 1. So nothing here is
extracted. The row is the session, and the session's own words are the body.

**Why the body is prose and not the transcript.** "Verbatim" is not affordable
against the raw file and does not need to be. Measured on this machine's corpus
(1,507 sessions, 1,372 MB): what a person and the model actually *said* is
16.6 MB — **1.2%** of the bytes. The rest is tool results, which are file
dumps and command output that the code index already serves better than a
quotation could. Of the 19 questions behind the 68%, 17 are still answerable
from prose alone and 0 are answerable from a session's opening request, which
is what rules out storing a pointer plus an excerpt: that is a label, not a
retrieval system.

**Why this costs no second read.** :func:`TranscriptEpisodeRecorder.observe`
tees the event stream the decision miner is already pulling through the
cursor — every event is yielded onward untouched and folded on the way past.
A second sweep over transcripts would be the same defect as a second ``git
log``, and the cursor makes it worse than wasteful: whichever pass ran first
would leave the second one nothing to read.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.precedent.store import (
    _IN_CHUNK,
    TIER_TRANSCRIPT,
    Episode,
    EpisodeStore,
)
from repowise.core.sessions.events import event_files, is_prose_user_text, relative_files

_log = logging.getLogger(__name__)

#: The one kind this tier writes. A session is not further classified: the
#: classification is what distillation would have been.
KIND_SESSION = "session"

#: Bound on one row's body. A resource ceiling, not a shape: a session over it
#: is kept and marked rather than dropped. Measured consequence on this
#: machine's heaviest project — p90 is 84 KB, so this holds 98% of sessions
#: whole and truncates the tail that is a transcript of a marathon.
MAX_BODY_BYTES = 128 * 1024

#: Above this many files, the session stops locating anything and is recorded
#: **repo-wide** — kept, with no node set — rather than skipped or truncated.
#: Truncating would leave a scope narrower than the body it describes; skipping
#: would lose the session outright, which is right for a sweeping commit and
#: wrong for a long working session. Chosen above the observed distribution
#: rather than against it: 0 of 80 sampled sessions here reach it, so it is a
#: guard that has never fired on the repository it was written in.
MAX_EPISODE_NODES = 100

#: Read at most this many rows back per merge statement. Taken from the store
#: rather than restated: two independent 500s that must agree forever are one
#: edit away from a ``ValueError`` on the index path.
_MERGE_CHUNK = _IN_CHUNK

#: What turns are joined with. Named because the cap has to account for it.
_JOIN = "\n\n"


@dataclass
class _Fold:
    """What one transcript contributed, accumulated as its events go past."""

    subject: str
    session_id: str | None = None
    first_ts: float | None = None
    last_ts: float | None = None
    turns: int = 0
    parts: list[str] = field(default_factory=list)
    body_bytes: int = 0
    files: list[str] = field(default_factory=list)

    @property
    def read_something(self) -> bool:
        return self.turns > 0 or bool(self.files)


def _is_dialog(event: Any) -> bool:
    """Whether this event is the conversation rather than the plumbing.

    Sidechain lines are a subagent's own stream and belong to whatever that
    agent was doing; meta lines are harness bookkeeping the user never saw. A
    post-compaction summary is kept on purpose despite being synthetic: it is
    the only trace left in the file of everything before the compaction, so
    dropping it silently deletes the first half of a long session.

    User turns go through :func:`is_prose_user_text`, the rule the decision
    miner already applies, rather than a second one written here. It is what
    keeps slash-command wrappers and injected reminder blocks out: labelling a
    sample of real episodes found one opening **12 of 20 bodies**, so without
    it the first thing a reader sees is usually harness plumbing.
    """
    if not getattr(event, "text", ""):
        return False
    if event.sidechain or event.is_meta:
        return False
    return is_prose_user_text(event) if event.kind == "user" else True


class TranscriptEpisodeRecorder:
    """Folds one episode per session out of a stream somebody else is reading.

    Presence is registered when a transcript is *shown* to the recorder, not
    when it yields events: a session already fully consumed by the cursor
    yields nothing and must still count as present, or the next write would
    read its silence as a source that had gone away.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = Path(repo_root)
        self._folds: dict[str, _Fold] = {}
        self._present: list[str] = []

    def note_present(self, paths: Iterable[Path]) -> None:
        """Register transcripts as existing without reading them.

        Presence and reading are different claims: a run may stop reading early
        on a large first sweep, but every transcript it *listed* still has a
        session behind it. Called with the whole discovery result, so a partial
        sweep never records its own unfinished work as sources that have gone
        away.
        """
        for path in paths:
            self._present.append(self._subject(path))

    def observe(self, path: Path, events: Iterable[Any]) -> Iterator[Any]:
        """Yield *events* through untouched, folding what an episode needs.

        Registration happens here rather than inside the generator body: a
        generator function runs no statement until it is iterated, so a
        transcript the caller opens and abandons would silently drop out of
        the presence set and take its episode with it.
        """
        subject = self._subject(path)
        self._present.append(subject)
        fold = self._folds.setdefault(subject, _Fold(subject=subject))
        return self._fold_through(fold, events)

    def _fold_through(self, fold: _Fold, events: Iterable[Any]) -> Iterator[Any]:
        for event in events:
            yield event  # first, so a consumer's exception cannot cost the read
            try:
                self._absorb(fold, event)
            except Exception:  # pragma: no cover - defensive
                _log.debug("transcript_episodes.absorb_failed", exc_info=True)

    def _absorb(self, fold: _Fold, event: Any) -> None:
        fold.session_id = fold.session_id or event.session_id
        ts = event.ts
        if ts:
            fold.first_ts = ts if fold.first_ts is None else min(fold.first_ts, ts)
            fold.last_ts = ts if fold.last_ts is None else max(fold.last_ts, ts)
        fold.files.extend(event_files(event))
        if not _is_dialog(event):
            return
        text = event.text.strip()
        if not text:
            return
        fold.turns += 1
        # The separator this turn will be joined with counts against the cap
        # too: leaving it out makes the bound wrong by two bytes per turn,
        # which is small and still means the stated cap is not the real one.
        sep = len(_JOIN.encode("utf-8")) if fold.parts else 0
        room = MAX_BODY_BYTES - fold.body_bytes - sep
        if room <= 0:
            return
        chunk = f"{event.kind}: {text}"
        raw = chunk.encode("utf-8", "replace")
        if len(raw) > room:
            # Cut the turn rather than letting it carry the body past the cap.
            # Stopping *before* an oversized turn instead would make the cap a
            # suggestion: one long message can be tens of kilobytes on its own.
            chunk = raw[:room].decode("utf-8", "ignore")
            raw = chunk.encode("utf-8", "replace")
        fold.parts.append(chunk)
        fold.body_bytes += len(raw) + sep

    def _subject(self, path: Path) -> str:
        """Identity of the session: its transcript, as a stable string.

        The transcript rather than the session id, because presence has to be
        answerable from the directory listing alone — the id is inside the file
        and a file past its cursor is never opened. Absolute, and that is
        correct rather than sloppy: this tier is per-machine by definition, and
        a repository that moves should lose the episodes pointing at where it
        used to be.
        """
        return str(path).replace("\\", "/")

    @property
    def present_subjects(self) -> list[str]:
        """Every transcript shown to this recorder, in first-seen order."""
        return list(dict.fromkeys(self._present))

    def pending(self) -> list[_Fold]:
        """Folds that read something and so have an episode to write."""
        return [f for f in self._folds.values() if f.read_something]


def _nodes_for(
    fold: _Fold, repo_root: Path, prior: Sequence[str] = ()
) -> tuple[tuple[str, ...], int]:
    """(node set, files located) for this session.

    Tool inputs record what a call *reached for*, so they include paths that
    were guessed and never existed — measured at 8.5% of this corpus's nodes,
    two thirds of them directories that were listed rather than edited.
    Keeping only what resolves to a file today is the same discipline as
    dropping paths outside the repository, and it costs one ``stat`` per node.

    The two returns differ only past the ceiling, and the count is what lets a
    reader tell *touched nothing locatable* from *touched too much to locate* —
    both of which are an empty node set, and only one of which is a session
    with nothing to say.
    """
    seen = list(dict.fromkeys([*prior, *relative_files(fold.files, repo_root)]))
    kept = [n for n in seen if (repo_root / n).is_file()]
    if len(kept) > MAX_EPISODE_NODES:
        return (), len(kept)
    return tuple(kept), len(kept)


def _evidence(fold: _Fold, nodes: Sequence[str], located: int, birth_at: float, body: str) -> str:
    """The one-line label a reader sees beside the body.

    Truncation is read off the stored body rather than carried as a flag from
    the pass that caused it. A later run that finds nothing new has no reason
    to know the body was ever cut, and a flag from *that* pass would quietly
    retract the warning on every long session the moment it ended.
    """
    on = datetime.fromtimestamp(birth_at, tz=UTC).strftime("%Y-%m-%d")
    sid = (fold.session_id or "")[:8] or "unknown"
    if nodes:
        files = "1 file" if len(nodes) == 1 else f"{len(nodes)} files"
        scope = f"touched {files}"
    elif located:
        scope = f"touched {located} files, too many to bind a scope to"
    else:
        scope = "no located files"
    tail = "; body truncated" if len(body.encode("utf-8", "replace")) >= MAX_BODY_BYTES else ""
    return f"session {sid}, {on}, {scope}{tail}"


def _merged_body(prior_body: str, prior_birth_at: float | None, fold: _Fold) -> str:
    """Prior body plus this run's prose, capped, oldest first.

    A live session is read across several indexes, and the store's upsert
    overwrites rather than appends, so without this the row would end up
    holding only whichever slice of the conversation the last run happened to
    see.

    The guard handles the cursor not being monotonic: ``iter_new_events``
    restarts at byte 0 when a transcript has been truncated or rotated, so
    "everything appended since last time" can turn out to be everything,
    again, and appending it writes the conversation twice.

    **Content cannot decide this on its own, and trying is a bug.** Transcripts
    repeat turns verbatim constantly ("Running the tests.", "Done."), so any
    containment test — anchored or not — eventually reads a genuinely new turn
    as a replay of an old one and silently drops it. What separates the two is
    *when*: a replay re-reads the session's opening turn, so it starts no later
    than the birth already recorded, while new turns are later by construction.
    Time says whether a rewind happened; content says what to keep.
    """
    fresh = _JOIN.join(fold.parts)
    if not prior_body:
        return fresh
    rewound = (
        fold.first_ts is not None
        and prior_birth_at is not None
        and fold.first_ts <= prior_birth_at
    )
    if rewound:
        if fresh.startswith(prior_body):
            return fresh  # a replay that has since gone further
        if prior_body.startswith(fresh):
            return prior_body  # a replay of what is already held
    sep = len(_JOIN.encode("utf-8"))
    room = MAX_BODY_BYTES - len(prior_body.encode("utf-8", "replace")) - sep
    if room <= 0:
        return prior_body
    add = fresh.encode("utf-8", "replace")[:room].decode("utf-8", "ignore")
    return f"{prior_body}{_JOIN}{add}"


def derive_transcript_episodes(
    recorder: TranscriptEpisodeRecorder,
    repo_root: Path,
    prior: dict[str, dict] | None = None,
) -> list[Episode]:
    """Episodes for every session this run read. Pure; opens nothing."""
    prior = prior or {}
    out: list[Episode] = []
    for fold in recorder.pending():
        try:
            row = prior.get(fold.subject) or {}
            nodes, located = _nodes_for(fold, repo_root, row.get("nodes") or ())
            body = _merged_body(row.get("body") or "", row.get("birth_at"), fold)
            if not body.strip():
                continue
            birth_at = row.get("birth_at") or fold.first_ts or fold.last_ts
            if not birth_at:
                continue
            out.append(
                Episode(
                    tier=TIER_TRANSCRIPT,
                    kind=KIND_SESSION,
                    subject=fold.subject,
                    body=body,
                    evidence=_evidence(fold, nodes, located, birth_at, body),
                    nodes=nodes,
                    # No birth commit: a session is dated, not committed, so
                    # its currency is a question the git query cannot answer.
                    birth_commit=None,
                    birth_at=float(birth_at),
                )
            )
        except Exception:  # pragma: no cover - defensive
            _log.debug("transcript_episodes.derive_failed", exc_info=True)
    return out


def record_transcript_episodes(repo_path: Path | str, recorder: TranscriptEpisodeRecorder) -> int:
    """Persist this run's transcript episodes. Best-effort; never raises.

    A repository that has never been indexed has no ``.repowise`` directory and
    gets no store created underneath it, exactly as the other two tiers.
    """
    try:
        root = Path(repo_path).resolve()
        if not (root / ".repowise").is_dir():
            return 0
        subjects = recorder.present_subjects
        pending = recorder.pending()
        if not subjects and not pending:
            # Nothing was even discovered: this run cannot vouch for the tier's
            # membership, so it must not annotate it. Same rule as a git run that
            # walked no window.
            return 0
        with EpisodeStore.open_for_repo(root) as store:
            prior = _prior_rows(store, [f.subject for f in pending])
            episodes = derive_transcript_episodes(recorder, root, prior)
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind=KIND_SESSION,
                episodes=episodes,
                present_subjects=subjects,
            )
        return len(episodes)
    except Exception:  # pragma: no cover - defensive
        _log.debug("transcript_episodes.record_failed", exc_info=True)
        return 0


def _prior_rows(store: EpisodeStore, subjects: Sequence[str]) -> dict[str, dict]:
    """Existing rows for exactly the sessions about to be rewritten.

    Scoped to those subjects rather than read whole: the tier is one row per
    session with a body measured in tens of kilobytes, so loading all of it to
    update a handful would pull the entire corpus's prose through memory on
    the index path.
    """
    rows: dict[str, dict] = {}
    for start in range(0, len(subjects), _MERGE_CHUNK):
        batch = subjects[start : start + _MERGE_CHUNK]
        for row in store.list_episodes(
            tier=TIER_TRANSCRIPT, kind=KIND_SESSION, subjects=batch
        ):
            rows[row["subject"]] = row
    return rows
