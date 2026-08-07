"""Did the agent act on what the hook said? — the efficacy classifier.

The augment hooks write one ledger row per firing (see
:class:`~repowise.core.sessions.staging.SessionStagingStore`), but a firing's
*outcome* is not visible from the hook: it lives in whatever the agent did on
the next few tool calls. Only the transcript has both halves, so this module
replays transcripts, pairs each emission with the tool calls that followed it,
and settles the ``acted`` column.

Modeled on ``augment_cmd/served_reads.py``, which asks the same shape of
question (did the agent Read content the MCP tools already served?) one event
at a time. The difference is the window: adoption of a *pointer* cannot be
judged from the next single event, so each firing is scored against the next
:data:`ACTION_WINDOW` tool calls.

Where the emissions live
------------------------
Claude Code records a hook firing as a ``type: "attachment"`` line whose
``attachment.type`` is ``hook_success``; ``stdout`` holds the JSON the hook
printed and ``durationMs`` the harness-measured wall time. It writes a second
``hook_additional_context`` line for the same firing, carrying the rendered
text — which is why a naive grep for ``[repowise]`` over raw transcript lines
counts every firing twice. Only ``hook_success`` is parsed here, so counts
here are per-emission.

What counts as acting, per surface
----------------------------------
============= =============== ==================================================
surface       category        acted when, within the window
============= =============== ==================================================
read          skeleton_nudge  a skeleton/structure call on the named file
                              (retired; historical rows only — see below)
read          skeleton_served n/a — the hook already did it (see below)
read          stale_read      n/a — a warning, not a pointer
search        triage          a named file is touched
search        rescue          the named file or symbol is touched
search        rescue_wide     same, scored apart (see below)
search        digest          a file the digest ranked is touched
search        digest_served   n/a, the hook already did it (see below)
fix_history   edit_notice     a test is run, or the file's history is inspected
============= =============== ==================================================

``rescue_wide`` is the same emitter under a different precondition (grep
returned a few results and the best indexed symbol is in a file none of them
name), and it is a **separate category** on purpose. ``rescue``'s 44% is the
highest action rate in the system and it was measured on the zero-result
population only; pooling a new population under the same key would move that
number without anything having changed about the surface it describes.

A ranged Read after a skeleton nudge is deliberately **not** acting: reading a
range of a file you just read in full is ordinary edit-prep and cannot be
attributed to the nudge. It is tracked separately as
:data:`AMBIGUOUS` evidence so the generous bound stays reportable.

The nudge itself no longer fires (see ``augment_cmd/read_state.py``). Its
judge stays because ``hook backfill`` still has to settle the firings already
in the corpus, and because the reason it was retired is worth keeping beside
the code that measured it.

The suspicion was that the judge above is too strict, crediting only a
structure call on a file the agent has just read in full, which the agent has
no reason to make. So three looser readings were replayed over 516 firings
before the surface was touched, and none of them found an effect. A structure
call on *any* file followed 11.4% of nudges, against an 11.9% unconditioned
base rate; ``get_context`` on a file not yet read, 2.9% against 3.4%. Both sit
at or below chance. Read as compliance instead, which is the shape
``read``/``reread`` uses (did the agent stop doing the thing, rather than
start doing another), 54.8% of nudges were followed within the window by a
second nudge, meaning another large indexed file read whole, a median of six
tool calls later.

The number that settles it is the dose. A session's *first* nudge was
followed by another 57.1% of the time, a later one 53.4%, so being told again
changed nothing. The judge was not the problem.

One thing the replay found that no rate would have: 53.3% of firings answered
a *ranged* Read. The unbounded-read gate belonged to the replacement, and the
nudge sat on the branch the replacement declines, so over half of it was
advice to be more targeted handed to a read that already was. That also means
the ``AMBIGUOUS`` bucket below was reading the ranged form backwards about as
often as not: for those firings the ranged read came *before* the nudge.

The *replacement* rows (``skeleton_served`` and its two recovery categories,
and ``digest_served``) are structurally unlike the rest: their text goes out as
``updatedToolOutput``, so it never appears as a transcript
``hook_additional_context`` line and this module's pattern pass will never
see it. That is fine and deliberate — the hook writes those rows directly and
Gate A is a ratio of row counts, not a question about what the agent did
next. There is no action to look for, because the hook took it. What those
surfaces return is measured in the savings ledger instead, not as an adoption
rate.

Surfaces with no recommended action (``stale_read``) and surfaces that emit
nothing at all (``read_enrich``'s read-after-served KPI) are never marked
acted and are excluded from action rates rather than counted as failures.
Decision-surface rows are judged elsewhere, against the decision records
themselves — see
:func:`~repowise.core.sessions.miners.decisions.apply_injection_feedback`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Tool calls after a firing that are still credited to it. Generous on
#: purpose — the measured action-distance histogram puts almost all of the
#: signal in the first three calls, so a wide window costs little and proves
#: the tail is genuinely empty rather than merely unmeasured.
ACTION_WINDOW = 40

#: Evidence string for a follow-up that is consistent with the hook but not
#: attributable to it. Recorded, never counted as acted.
AMBIGUOUS = "ranged_read"

#: Surfaces this module judges. ``decision`` rows key on decision-record ids
#: and are judged by the decision miner; ``read_enrich`` is a silent KPI with
#: no emission to act on.
CLASSIFIED_SURFACES = ("read", "search", "fix_history", "wrong_path")

#: (surface, category) pairs that carry no recommended action, so an unacted
#: firing is not a failure. Reported as a count, excluded from rates. The
#: stale-read notice belongs here because the behavior it asks for — stop
#: reasoning from a pre-edit excerpt — leaves no trace in the transcript.
NO_ACTION_EXPECTED = frozenset(
    {
        ("read", "stale_read"),
        ("read_enrich", "read_after_served"),
        # The skeleton replacement and its two recovery counters. Nothing is
        # asked of the agent, so an unacted row is not a failure — these are
        # Gate A's numerator and denominator, not an adoption rate.
        ("read", "skeleton_served"),
        ("read", "skeleton_recovered_full"),
        ("read", "skeleton_ranged"),
        # A *served* flood digest, for the same structural reason: it leaves as
        # updatedToolOutput, so the pattern pass below can never see it. The
        # appended ``search``/``digest`` rows are still scored normally, and the
        # two are kept apart so a cost and a saving are never averaged.
        ("search", "digest_served"),
    }
)

#: (surface, category) pairs whose emission has been removed. The judges below
#: stay so a transcript backfill still settles the rows already in the corpus,
#: but nothing new lands here, and a rate over a closed population is not an
#: adoption rate — ``read``/``reread`` reads 100% and ``read``/``skeleton_nudge``
#: 0.2% off populations that stopped growing, and both are indistinguishable
#: from a live surface until they are labelled apart. Historical, not current.
RETIRED_CATEGORIES = frozenset(
    {
        # Retired in the read hook; see ``augment_cmd/read_state.py``.
        ("read", "reread"),
        # Retired on the emission side after the dose measurement.
        ("read", "skeleton_nudge"),
    }
)

#: Firings whose recommended outcome is a *non*-action, scored as compliance
#: (did the agent avoid re-offending?) rather than adoption. Same ``acted``
#: column, and ``repowise hook stats`` labels these "respected" so the two are
#: never read as the same measurement.
#:
#: ``read``/``reread`` is retired — the notice no longer fires (see
#: ``augment_cmd/read_state.py``). The classifier stays so historical rows and
#: transcript backfills still settle correctly; nothing new lands here.
COMPLIANCE_CATEGORIES = frozenset({("read", "reread")})


def ledger_key(surface: str, category: str, text: str) -> str:
    """Efficacy-ledger id for one emission.

    Must stay identical to ``augment_cmd/_shared.py``'s ``_ledger_key`` — the
    hook path cannot import this package, so the two are mirrors. Keying on
    the emitted text is what lets a transcript firing find the row the live
    hook wrote for it.
    """
    digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{surface}:{category}:{digest}"


# ---------------------------------------------------------------------------
# Emission parsing
# ---------------------------------------------------------------------------

# One pattern per emission the hooks produce, matched against a single line of
# the emitted text. Each capture group named below feeds the classifier:
# ``target`` is the file or symbol the hook pointed at.
_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # Retired on the emission side; kept so a backfill still settles the rows
    # already in the corpus. Nothing new lands here.
    (
        "read",
        "skeleton_nudge",
        re.compile(r"^\[repowise\] A skeleton of (?P<target>\S+) is ~\d+ tokens vs ~\d+"),
    ),
    (
        "read",
        "reread",
        re.compile(r"^\[repowise\] You already read (?P<target>\S+) this session"),
    ),
    (
        "read",
        "stale_read",
        re.compile(r"^\[repowise\] (?P<target>\S+) changed \(Edit/Write\) after your previous"),
    ),
    (
        "fix_history",
        "edit_notice",
        re.compile(r"^\[repowise\] (?P<target>\S+) has been bug-fixed \d+x"),
    ),
    (
        "search",
        "rescue",
        re.compile(
            r"^\[repowise\] No literal match for `(?P<pattern>[^`]+)`\. "
            r"Closest indexed symbol: \S+ `(?P<symbol>[^`]+)` in (?P<target>[^\s:]+)"
        ),
    ),
    (
        "search",
        "rescue",
        re.compile(
            r"^\[repowise\] No literal match for `(?P<pattern>[^`]+)`\. "
            r"Wiki suggests `(?P<target>[^`]+)`"
        ),
    ),
    (
        "search",
        "rescue_wide",
        re.compile(
            r"^\[repowise\] `(?P<pattern>[^`]+)` matched \d+ files?, but not "
            r"(?P<target>[^\s:]+)(?::\d+)?, where indexed \S+ `(?P<symbol>[^`]+)`"
        ),
    ),
    (
        "search",
        "triage",
        re.compile(
            r"^\[repowise\] \d+\+ matches for `(?P<pattern>[^`]+)` across \d+ files\. "
            r"Most likely relevant"
        ),
    ),
    # The pre-#1292 triage header. Kept so `hook backfill` still settles the
    # 111 firings already in the corpus; nothing new lands here.
    (
        "search",
        "triage",
        re.compile(r"^\[repowise\] \d+\+ matches for `(?P<pattern>[^`]+)`\. Top files by"),
    ),
    (
        "search",
        "digest",
        re.compile(r"^\[repowise\] Search flood — compact digest"),
    ),
    # Deliberately one line, so ``parse_emission`` — which harvests loose path
    # tokens from continuation lines only — cannot turn the attempted path into
    # a target. The resolved path is the last field and is captured to end of
    # line rather than as ``\S+``: an indexed path can contain a space, and
    # truncating it there both stores a node id that does not exist and leaves
    # a prefix short enough to substring-match almost anything.
    (
        "wrong_path",
        "rescue",
        re.compile(
            r"^\[repowise\] \S+ is not in this tree\. "
            r"The only indexed \S+ is (?P<target>.+)$"
        ),
    ),
)

#: The attempted path from a wrong-path rescue, for the judge below. Read off
#: the emission rather than carried on :class:`Firing`, which has no field for
#: "what this firing was steering the agent away from".
_WRONG_PATH_ATTEMPTED = re.compile(r"^\[repowise\] (?P<attempted>\S+) is not in this tree\.")

#: A path-shaped token, for harvesting the file lists that follow a triage
#: header or ride inside a digest body. Both separators are accepted: triage
#: renders repo-relative POSIX node ids, but the digest groups whatever
#: spelling the grep output used, which on Windows is backslashed.
_FILEISH = re.compile(r"[\w.\-]+(?:[/\\][\w.\-]+)+\.\w{1,6}")


@dataclass(slots=True)
class Firing:
    """One hook emission, with what the agent did next."""

    surface: str
    category: str
    #: The emitted text, verbatim — the ledger key is its hash.
    text: str
    #: Files/symbols the emission pointed at, best first.
    targets: list[str] = field(default_factory=list)
    #: Search pattern the firing was about, when it names one.
    pattern: str = ""
    session_id: str = ""
    ts: float | None = None
    #: Harness-measured end-to-end wall time for the whole hook process. One
    #: process can emit several firings, so this is the cost of the firing's
    #: *batch*, not of the firing alone.
    duration_ms: int = 0
    #: Settled by :func:`classify`: True/False once judged, None when the
    #: surface has no action to take.
    acted: bool | None = None
    #: What settled it — an action name, :data:`AMBIGUOUS`, or "".
    evidence: str = ""
    #: Tool calls between the firing and the action.
    distance: int | None = None

    @property
    def key(self) -> str:
        return ledger_key(self.surface, self.category, self.text)


def parse_emission(text: str) -> list[Firing]:
    """Split one hook emission into its firings.

    A single response can carry several notices joined by newlines (a file
    that is both governed and much-fixed emits two), so each line is matched
    independently. A firing's ``text`` is the block it owns: its own line plus
    any indented continuation, which is where triage and the digest keep their
    file lists.
    """
    lines = text.splitlines()
    out: list[Firing] = []
    for i, line in enumerate(lines):
        for surface, category, pattern in _PATTERNS:
            m = pattern.match(line)
            if m is None:
                continue
            block = [line]
            for follow in lines[i + 1 :]:
                if follow.startswith("[repowise]") or not follow.strip():
                    break
                block.append(follow)
            groups = m.groupdict()
            targets = [t for t in (groups.get("target"), groups.get("symbol")) if t]
            targets.extend(
                p for p in _FILEISH.findall("\n".join(block[1:])) if p not in targets
            )
            out.append(
                Firing(
                    surface=surface,
                    category=category,
                    text="\n".join(block),
                    targets=[_normalize(t) for t in targets],
                    pattern=groups.get("pattern") or "",
                )
            )
            break
    return out


def _normalize(path: str) -> str:
    """Repo-relative POSIX spelling, as the ledger's node ids use.

    ``removeprefix`` rather than ``lstrip``, which strips a character *set*:
    it turned ``.github/workflows/ci.yml`` into ``github/...``, storing a node
    id no repository has and widening the target to a substring that matches
    more than it should.
    """
    return (
        path.replace("\\\\", "/").replace("\\", "/").removeprefix("./").rstrip(".,;:")
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

#: Tools that surface a file's structure instead of its bytes — acting on a
#: skeleton nudge means reaching for one of these.
_STRUCTURE_TOOLS = ("get_context", "get_symbol", "search_codebase")

#: Tools that answer "what does history say about this file" — acting on a
#: fix-history notice means reaching for one of these, or running a test.
_HISTORY_TOOLS = ("get_risk", "get_why", "get_change_risk", "get_health", "get_context")

_TEST_CMD = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|npm (run )?test|tox|nose)\b")
_HISTORY_CMD = re.compile(r"\bgit (log|blame|show|bisect)\b")


def classify(firing: Firing, following: list[tuple[str, str]]) -> Firing:
    """Settle ``acted`` for one firing against the tool calls that followed.

    *following* is ``(tool_name, serialized_input)`` in transcript order. It is
    clipped to :data:`ACTION_WINDOW` here rather than trusted from the caller —
    the window *is* the attribution claim, and silently widening it is how a
    hook gets credit for something forty tool calls of unrelated work later.
    Mutates and returns *firing* so callers can classify in place.
    """
    following = following[:ACTION_WINDOW]
    if (firing.surface, firing.category) in NO_ACTION_EXPECTED:
        firing.acted = None
        return firing

    if (firing.surface, firing.category) in COMPLIANCE_CATEGORIES:
        return _classify_compliance(firing, following)

    judge = {
        ("read", "skeleton_nudge"): _acted_skeleton,
        ("search", "triage"): _acted_target,
        ("search", "rescue"): _acted_rescue,
        ("search", "rescue_wide"): _acted_rescue,
        ("search", "digest"): _acted_target,
        ("fix_history", "edit_notice"): _acted_fix_history,
        ("wrong_path", "rescue"): _acted_wrong_path,
    }.get((firing.surface, firing.category))
    if judge is None:
        firing.acted = None
        return firing

    for distance, (name, raw) in enumerate(following):
        evidence = judge(firing, name, _normalize(raw), raw)
        if not evidence:
            continue
        firing.evidence = evidence
        if evidence == AMBIGUOUS:
            # Keep looking: a real action later in the window still counts,
            # and the ambiguous hit is only reported as the generous bound.
            continue
        firing.acted = True
        firing.distance = distance
        return firing
    firing.acted = False
    return firing


def _acted_skeleton(firing: Firing, name: str, norm: str, raw: str) -> str:
    """Structure call on the nudged file; a ranged re-read is ambiguous."""
    target = firing.targets[0] if firing.targets else ""
    if not target:
        return ""
    base = target.rsplit("/", 1)[-1]
    if "get_context" in name and "skeleton" in raw and base in norm:
        return "skeleton_call"
    if any(t in name for t in _STRUCTURE_TOOLS) and base in norm:
        return "structure_call"
    if name == "Read" and target in norm and ('"offset"' in raw or '"limit"' in raw):
        return AMBIGUOUS
    return ""


def _classify_compliance(firing: Firing, following: list[tuple[str, str]]) -> Firing:
    """Score a non-action notice: respected unless the agent re-offends.

    The re-read notice asks the agent to stop re-reading a file it already has
    in context. There is no positive action to detect — reaching for
    ``get_symbol`` instead is one compliant outcome, but so is simply moving
    on, which is what usually happens. So the measurement is the offense:
    another *unbounded* Read of the same file inside the window. Scoring this
    as adoption would report a flat 0% for a notice that is in fact obeyed.
    """
    target = firing.targets[0] if firing.targets else ""
    firing.acted = True
    firing.evidence = "respected"
    if not target:
        return firing
    for distance, (name, raw) in enumerate(following):
        if name != "Read" or target not in _normalize(raw):
            continue
        if '"offset"' in raw or '"limit"' in raw:
            firing.evidence = "ranged_read"
            continue
        firing.acted = False
        firing.evidence = "reread_again"
        firing.distance = distance
        return firing
    return firing


def _acted_target(firing: Firing, name: str, norm: str, raw: str) -> str:
    """Any named file showing up in a later tool call counts."""
    for rank, target in enumerate(firing.targets):
        if target and target in norm:
            return f"touched_rank{rank}"
    return ""


def _acted_wrong_path(firing: Firing, name: str, norm: str, raw: str) -> str:
    """Went where it pointed — and retrying the failed path is not that.

    ``_acted_target`` is an unanchored substring test, so it cannot be used
    here alone: whenever the attempted path *contains* the resolved one (the
    agent guessed an extra directory in front of a real file, which is half of
    this surface's corpus), a verbatim retry of the failed path matches the
    target and scores as compliance. Disqualifying the attempted path first is
    what keeps the rate about the rescue rather than about the mistake.
    """
    m = _WRONG_PATH_ATTEMPTED.match(firing.text)
    attempted = _normalize(m.group("attempted")) if m else ""
    if attempted and attempted in norm:
        return ""
    return _acted_target(firing, name, norm, raw)


def _acted_rescue(firing: Firing, name: str, norm: str, raw: str) -> str:
    """The rescued file, or the symbol name it offered."""
    hit = _acted_target(firing, name, norm, raw)
    if hit:
        return hit
    symbol = firing.targets[1] if len(firing.targets) > 1 else ""
    return "symbol_used" if symbol and symbol in raw else ""


def _acted_fix_history(firing: Firing, name: str, norm: str, raw: str) -> str:
    """Ran a test, or asked history about the file."""
    if name in ("Bash", "PowerShell"):
        if _TEST_CMD.search(raw):
            return "ran_test"
        if _HISTORY_CMD.search(raw):
            return "read_history"
        return ""
    target = firing.targets[0] if firing.targets else ""
    if any(t in name for t in _HISTORY_TOOLS) and target and target in norm:
        return "history_tool"
    if name == "Task" and _TEST_CMD.search(raw):
        return "ran_test"
    return ""


# ---------------------------------------------------------------------------
# Transcript replay
# ---------------------------------------------------------------------------


def iter_transcript_firings(path: Path, *, window: int = ACTION_WINDOW) -> Iterator[Firing]:
    """Every classified repowise hook firing in one Claude Code transcript.

    Reads the file once into two indexes — hook emissions and tool calls, both
    by line number — then scores each emission against the calls that follow
    it. A transcript that cannot be read at all yields nothing; individual
    unparseable lines are skipped, never guessed at.
    """
    emissions: list[tuple[int, str, float | None, int, str]] = []
    tool_uses: list[tuple[int, str, str]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                if '"attachment"' in line and "[repowise]" in line:
                    parsed = _hook_emission(line)
                    if parsed is not None:
                        emissions.append((index, *parsed))
                elif '"tool_use"' in line:
                    tool_uses.extend(_tool_uses(line, index))
    except OSError:
        return

    for index, text, ts, duration_ms, session_id in emissions:
        following = [
            (name, raw) for (j, name, raw) in tool_uses if index < j <= index + window
        ][:window]
        for firing in parse_emission(text):
            firing.ts = ts
            firing.duration_ms = duration_ms
            firing.session_id = session_id
            yield classify(firing, following)


def _hook_emission(line: str) -> tuple[str, float | None, int, str] | None:
    """``(text, ts, duration_ms, session_id)`` from a hook_success attachment.

    The paired ``hook_additional_context`` line carries the same text and is
    skipped here — counting both is what makes every firing look like two.
    """
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "attachment":
        return None
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "hook_success":
        return None
    stdout = attachment.get("stdout")
    if not isinstance(stdout, str) or "[repowise]" not in stdout:
        return None
    try:
        payload = json.loads(stdout)
        text = payload["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(text, str):
        return None
    duration = attachment.get("durationMs")
    session_id = entry.get("sessionId") or entry.get("session_id") or ""
    return (
        text,
        _parse_ts(entry.get("timestamp")),
        int(duration) if isinstance(duration, int | float) else 0,
        session_id if isinstance(session_id, str) else "",
    )


def _tool_uses(line: str, index: int) -> list[tuple[int, str, str]]:
    """``(line_index, tool_name, serialized_input)`` for one assistant line."""
    try:
        entry = json.loads(line)
    except ValueError:
        return []
    content = ((entry or {}).get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str):
                out.append(
                    (index, name, json.dumps(block.get("input") or {}, ensure_ascii=False))
                )
    return out


def _parse_ts(value: Any) -> float | None:
    from repowise.core.sessions.events import parse_timestamp

    return parse_timestamp(value)


# ---------------------------------------------------------------------------
# Ledger ingest
# ---------------------------------------------------------------------------


def discover_transcripts(
    repo_path: Path, *, projects_root: Path | None = None, all_projects: bool = False
) -> list[Path]:
    """Claude Code transcripts to replay for *repo_path*.

    ``all_projects`` widens the sweep from this checkout's own transcript
    directory to every sibling directory whose munged name shares the repo's
    final path segment — the worktrees. A hook fired in a worktree is the same
    hook on the same index, and leaving them out undercounts by roughly the
    number of parallel workstreams.
    """
    from repowise.core.sessions.adapters.claude_code import transcript_dir_for

    own = transcript_dir_for(Path(repo_path).resolve(), projects_root)
    if not all_projects:
        return sorted(own.glob("*.jsonl")) if own.is_dir() else []
    root = own.parent
    if not root.is_dir():
        return []
    needle = Path(repo_path).resolve().name.lower()
    return sorted(
        p for d in root.iterdir() if d.is_dir() and needle in d.name.lower()
        for p in d.glob("*.jsonl")
    )


def ingest_transcript_efficacy(
    repo_path: Path,
    *,
    projects_root: Path | None = None,
    all_projects: bool = False,
    since: float | None = None,
    reset: bool = False,
) -> dict[str, int]:
    """Replay transcripts into the efficacy ledger. Returns per-surface counts.

    Idempotent: a firing's ledger id is the hash of its own text, so replaying
    the same transcript settles the same rows again rather than duplicating
    them. Rows the live hook already wrote keep their first-hand ``shown_at``
    and ``chars`` and gain the two columns only a replay can fill —
    ``acted`` and the harness-measured ``duration_ms``.

    *since* skips transcripts untouched since that epoch second, which is what
    keeps the update-time pass off the whole 1.3 GB of history.

    *reset* clears the classified surfaces before replaying. Needed once, to
    retire rows written under the pre-text-hash ledger keys: those ids cannot
    be recomputed from a transcript, so they would sit beside the replayed row
    for the same firing and double the count. It never touches decision rows.
    """
    from repowise.core.sessions.staging import SessionStagingStore

    counts: dict[str, int] = {}
    store = SessionStagingStore.open_default(Path(repo_path).resolve())
    try:
        if reset:
            store.clear_surfaces(CLASSIFIED_SURFACES)
        for transcript in discover_transcripts(
            repo_path, projects_root=projects_root, all_projects=all_projects
        ):
            try:
                if since is not None and transcript.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            session_id = transcript.stem
            for firing in iter_transcript_firings(transcript):
                store.record_firing(
                    session_id=firing.session_id or session_id,
                    key=firing.key,
                    surface=firing.surface,
                    category=firing.category,
                    node_id=firing.targets[0] if firing.targets else "",
                    chars=len(firing.text),
                    shown_at=firing.ts or 0.0,
                    duration_ms=firing.duration_ms,
                    acted=firing.acted,
                )
                counts[f"{firing.surface}/{firing.category}"] = (
                    counts.get(f"{firing.surface}/{firing.category}", 0) + 1
                )
        store.commit()
    finally:
        store.close()
    return counts
