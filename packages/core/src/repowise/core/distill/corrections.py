"""Correction mining — fail→fixed command pairs from agent transcripts.

Scans the same Claude Code transcript directory as the missed-savings scan
(:mod:`repowise.core.distill.missed`) for shell commands that failed and were
then re-run successfully in a corrected form — the agent fumbling a flag, a
path, or the tool itself (the recurring bare ``python`` vs
``.venv\\Scripts\\python.exe`` case). The aggregated rules feed a report and,
strictly opt-in, a short managed block in CLAUDE.md/AGENTS.md so the next
session doesn't repeat the fumble.

Failure detection is grounded in real transcript payloads, not guesswork:
a failed shell call's ``toolUseResult`` is a **string** starting with
``Error: Exit code N`` (and the tool_result block carries ``is_error``);
a successful call's is a dict with ``stdout``/``stderr``. Harness-level
errors — cancelled parallel calls, user rejections, permission denials —
are strings too but are *not* command failures and are skipped entirely.

Read-only and best-effort by the same contract as the missed scan: malformed
lines, unreadable files, or an absent transcript directory produce an empty
report, never an error. Everything stays local.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from repowise.core.sessions import ClaudeCodeAdapter, Event, transcript_dir_for

#: Default scan window, in days. Corrections are rarer than missed savings,
#: so the default window is wider.
DEFAULT_WINDOW_DAYS = 30.0

#: How many subsequent shell commands to search for the corrected re-run.
_LOOKAHEAD = 6

#: Minimum occurrences for a rule to make the managed --write block.
WRITE_MIN_COUNT = 2

#: Managed-block budget: most-frequent rules first, hard-capped.
WRITE_MAX_RULES = 10

_SHELL_TOOLS = ("Bash", "PowerShell")

_ADAPTER = ClaudeCodeAdapter()

#: The one true command-failure shape in real transcripts. Everything else
#: stringly (cancellations, rejections, permission denials, harness errors)
#: is not a command failure.
_EXIT_CODE_RE = re.compile(r"^Error: Exit code (\d+)")

#: Leading segments that position the shell rather than run the command:
#: directory changes, env assignments, console setup, the PS call operator.
_PREAMBLE_RES = (
    re.compile(r"^\s*(?:Set-Location|cd|pushd)\s+(?:\"[^\"]*\"|'[^']*'|[^;&|]+?)\s*(?:;|&&)\s*"),
    re.compile(r"^\s*\$env:\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^;]+?)\s*;\s*"),
    re.compile(r"^\s*\$\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^;]+?)\s*;\s*"),
    re.compile(r"^\s*\[[^\]]+\]::[^;]+;\s*"),
    re.compile(r"^\s*\w+=\S+\s+"),
    re.compile(r"^\s*&\s+"),
)

_FLAG_ERROR_RE = re.compile(
    r"unrecognized (?:option|argument)|unknown (?:option|flag)|no such option"
    r"|unexpected argument|invalid option|is not a valid option",
    re.IGNORECASE,
)
_PATH_ERROR_RE = re.compile(
    r"no such file or directory|cannot find (?:path|the path)|does not exist"
    r"|couldn't find|path not found|(?:file|directory) not found|not found:",
    re.IGNORECASE,
)

#: Chaining/grouping syntax after which a token-level diff stops meaning
#: anything; classification only ever looks at the first command segment.
_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||[|;])\s*")


def strip_preamble(command: str) -> str:
    """Drop leading cd/env/console-setup segments so the real command leads."""
    cmd = command.strip()
    for _ in range(8):
        previous = cmd
        for pattern in _PREAMBLE_RES:
            cmd = pattern.sub("", cmd, count=1)
        if cmd == previous:
            break
    return cmd


def _basename(token: str) -> str:
    """Lowercased executable identity of a token: path and .exe stripped."""
    token = token.strip("\"'")
    token = token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if token.lower().endswith(".exe"):
        token = token[:-4]
    return token.lower()


def command_anchor(command: str) -> str:
    """The stable identity token two variants of "the same command" share.

    Path- and extension-insensitive, so bare ``python`` and
    ``.venv\\Scripts\\python.exe`` anchor identically; ``python -m <mod>``
    anchors on the module so a runner-form change still pairs.
    """
    tokens = strip_preamble(command).split()
    if not tokens:
        return ""
    tool = _basename(tokens[0])
    if tool in ("python", "python3", "py") and len(tokens) >= 3 and tokens[1] == "-m":
        return _basename(tokens[2])
    return tool


def empty_report(days: float = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    return {"rules": [], "pairs": 0, "window_days": days}


def scan_corrections(
    repo_root: Path,
    *,
    days: float = DEFAULT_WINDOW_DAYS,
    now: float | None = None,
    projects_root: Path | None = None,
) -> dict[str, Any]:
    """Mine fail→fixed pairs from recent transcripts into aggregated rules.

    Returns ``{"rules": [...], "pairs": N, "window_days": D}`` where each
    rule has ``kind``/``wrong``/``fixed``/``count``/``example`` (and
    optionally ``hint``), sorted most-frequent first. Any failure degrades
    to an empty report.
    """
    try:
        return _scan(Path(repo_root).resolve(), days, now, projects_root)
    except Exception:
        return empty_report(days)


def _scan(
    repo_root: Path, days: float, now: float | None, projects_root: Path | None
) -> dict[str, Any]:
    transcripts = transcript_dir_for(repo_root, projects_root)
    report = empty_report(days)
    if not transcripts.is_dir():
        return report

    cutoff = (now if now is not None else time.time()) - days * 86400.0
    repo_prefix = str(repo_root).lower().rstrip("\\/")

    rules: dict[tuple[str, str, str], dict[str, Any]] = {}
    pairs = 0
    for path in sorted(transcripts.glob("*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff:
                continue
            events = _session_events(path, cutoff, repo_prefix)
        except OSError:
            continue
        pairs += _pair_events(events, rules)

    ordered = sorted(rules.values(), key=lambda r: (-r["count"], r["kind"]))
    _attach_index_hints(repo_root, ordered)
    report["rules"] = ordered
    report["pairs"] = pairs
    return report


# ---------------------------------------------------------------------------
# Transcript walking — one session file to an ordered list of shell events
# ---------------------------------------------------------------------------


def _prefilter(raw: str) -> bool:
    """Only shell tool_use lines and result lines are worth parsing."""
    return ('"tool_use"' in raw and '"command"' in raw) or '"toolUseResult"' in raw


def _session_events(path: Path, cutoff: float, repo_prefix: str) -> list[dict[str, Any]]:
    """Ordered shell events: ``{"command", "failed", "error"}`` per call."""
    events: list[dict[str, Any]] = []
    pending: dict[str, str] = {}
    for event in _ADAPTER.iter_events(path, prefilter=_prefilter):
        if event.kind == "assistant" and event.tool_uses:
            _collect_tool_use(event, cutoff, repo_prefix, pending)
        elif pending and event.tool_results:
            _collect_result(event, pending, events)
    return events


def _collect_tool_use(
    event: Event, cutoff: float, repo_prefix: str, pending: dict[str, str]
) -> None:
    if event.ts is not None and event.ts < cutoff:
        return
    cwd = (event.cwd or "").lower().rstrip("\\/")
    if not cwd.startswith(repo_prefix):
        return
    for use in event.tool_uses:
        if use.name not in _SHELL_TOOLS:
            continue
        command = str(use.input.get("command") or "")
        if not command:
            continue
        pending[use.id] = command


def _collect_result(event: Event, pending: dict[str, str], events: list[dict[str, Any]]) -> None:
    command = pending.pop(event.tool_results[0].tool_use_id, None)
    if command is None:
        return

    result = event.tool_results[0].payload
    if isinstance(result, dict):
        # Real success shape: stdout/stderr/interrupted dict.
        events.append({"command": command, "failed": False, "error": ""})
    elif isinstance(result, str) and _EXIT_CODE_RE.match(result):
        # Only the exit-code string shape is a command failure; cancellations,
        # rejections, and harness errors are neither failure nor success.
        events.append({"command": command, "failed": True, "error": result})


# ---------------------------------------------------------------------------
# Pairing + classification
# ---------------------------------------------------------------------------


def _pair_events(events: list[dict[str, Any]], rules: dict[tuple, dict[str, Any]]) -> int:
    """Match each failure to the next same-anchor success; aggregate rules."""
    paired = 0
    for i, event in enumerate(events):
        if not event["failed"]:
            continue
        anchor = command_anchor(event["command"])
        if not anchor or anchor in _IGNORED_ANCHORS:
            continue
        for later in events[i + 1 : i + 1 + _LOOKAHEAD]:
            if later["failed"] or command_anchor(later["command"]) != anchor:
                continue
            classified = _classify(event["command"], later["command"], event["error"])
            if classified is None:
                break  # same command succeeded on retry → flaky, not a fumble
            kind, wrong, fixed = classified
            rule = rules.setdefault(
                (kind, wrong.lower(), fixed.lower()),
                {
                    "kind": kind,
                    "anchor": anchor,
                    "wrong": wrong,
                    "fixed": fixed,
                    "count": 0,
                    "example": {"failed": "", "fixed": ""},
                },
            )
            rule["count"] += 1
            rule["example"] = {
                "failed": strip_preamble(event["command"]),
                "fixed": strip_preamble(later["command"]),
            }
            paired += 1
            break
    return paired


def _first_segment(command: str) -> str:
    """The first line's first pipeline/chain segment, ``2>&1`` dropped."""
    segment = _SEGMENT_SPLIT_RE.split(command.split("\n", 1)[0], maxsplit=1)[0]
    return re.sub(r"\s*2>&1\s*$", "", segment).strip()


#: Leads whose "corrections" are just the agent saying something different.
_IGNORED_ANCHORS = frozenset({"echo", "printf", "write-output", "write-host"})


def _path_like(token: str) -> bool:
    """Heuristic: does *token* plausibly name a file or directory?"""
    bare = token.strip("\"'")
    return "/" in bare or "\\" in bare or re.search(r"\.\w{1,4}$", bare) is not None


_MISSING_ARG_ERROR_RE = re.compile(
    r"required|missing|expected (?:an? )?(?:argument|value|path)", re.IGNORECASE
)


def _classify(failed_cmd: str, fixed_cmd: str, error: str) -> tuple[str, str, str] | None:
    """(kind, wrong, fixed) for a fail→fixed pair, or None when unclassified.

    Deliberately precision-first: a red-green dev loop re-runs the same test
    command with different selections as code changes — those exit-1 runs
    are not command fumbles. Apart from the structural wrong-tool case,
    every kind therefore requires corroborating error text.
    """
    failed_full = strip_preamble(failed_cmd)
    fixed_full = strip_preamble(fixed_cmd)
    if failed_full == fixed_full:
        return None

    # Diff only the first command segment: in `pytest bad/path | tail -5`,
    # the correction lives before the pipe; across `a && b; c` chains a
    # whole-string token diff produces garbage rules.
    failed_core = _first_segment(failed_full)
    fixed_core = _first_segment(fixed_full)

    failed_tokens = failed_core.split()
    fixed_tokens = fixed_core.split()
    if not failed_tokens or not fixed_tokens:
        return None

    # Wrong tool: same anchor (path/exe-insensitive) but a different leading
    # token — bare `python` fixed to `.venv\Scripts\python.exe` and kin.
    # Checked before the heredoc bail: the leading token is meaningful even
    # when the command body is a multi-line script.
    if failed_tokens[0].lower() != fixed_tokens[0].lower():
        return ("wrong_tool", failed_tokens[0], fixed_tokens[0])

    # Heredocs and multi-line scripts token-diff into noise — skip outright.
    if any("<<" in c or "\n" in c for c in (failed_full, fixed_full)):
        return None
    if failed_core == fixed_core:
        return None

    removed = [t for t in failed_tokens[1:] if t not in fixed_tokens]
    added = [t for t in fixed_tokens[1:] if t not in failed_tokens]
    if not removed and not added:
        return None  # token-identical modulo order/whitespace → retry

    # Unknown flag: the dropped flag itself must be named by the error —
    # otherwise a re-run with different options is just a strategy change.
    removed_flags = [t for t in removed if t.startswith("-")]
    if removed_flags and _FLAG_ERROR_RE.search(error):
        named = [f for f in removed_flags if f in error]
        if named:
            return ("unknown_flag", " ".join(named), " ".join(added) or "(removed)")

    # Wrong path: the dropped argument must look like a path AND be named
    # by the error text. Both gates matter — without them, every red-green
    # loop that re-runs with a different selection becomes a "correction".
    removed_args = [t for t in removed if not t.startswith("-")]
    added_args = [t for t in added if not t.startswith("-")]
    if removed_args and added_args and _PATH_ERROR_RE.search(error):
        named = [a for a in removed_args if _path_like(a) and a.strip("\"'") in error]
        if named and any(_path_like(a) for a in added_args):
            return ("wrong_path", " ".join(named), " ".join(added_args))

    if added and not removed and _MISSING_ARG_ERROR_RE.search(error):
        return ("missing_arg", "(missing)", " ".join(added))

    return None


# ---------------------------------------------------------------------------
# The managed "Known command corrections" block (strictly opt-in --write)
# ---------------------------------------------------------------------------

CORRECTIONS_MARKER_START = (
    "<!-- REPOWISE_CORRECTIONS:START — Do not edit below this line. Auto-generated by Repowise. -->"
)
CORRECTIONS_MARKER_END = "<!-- REPOWISE_CORRECTIONS:END -->"

_BLOCK_HEADING = "### Known command corrections"


def _clip(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def rule_line(rule: dict[str, Any]) -> str:
    """One markdown bullet for a correction rule."""
    kind = rule["kind"]
    wrong, fixed = _clip(rule["wrong"]), _clip(rule["fixed"])
    if kind == "wrong_tool":
        text = f"Use `{fixed}` instead of `{wrong}`"
    elif kind == "wrong_path":
        text = f"`{rule['anchor']}`: use `{fixed}`, not `{wrong}`"
        if rule.get("hint"):
            text += f" ({rule['hint']})"
    elif kind == "unknown_flag":
        text = f"`{rule['anchor']}` does not support `{wrong}`"
    else:  # missing_arg
        text = f"`{rule['anchor']}` needs `{fixed}`"
    return f"- {text} (corrected {rule['count']}x)"


def render_corrections_block(
    rules: list[dict[str, Any]],
    *,
    min_count: int = WRITE_MIN_COUNT,
    max_rules: int = WRITE_MAX_RULES,
) -> str | None:
    """The managed block body, or None when no rule clears the threshold."""
    qualifying = [r for r in rules if r["count"] >= min_count][:max_rules]
    if not qualifying:
        return None
    lines = [_BLOCK_HEADING, ""]
    lines.extend(rule_line(r) for r in qualifying)
    return "\n".join(lines)


def update_corrections_block(target: Path, block: str | None) -> bool:
    """Upsert (or, with ``block=None``, remove) the managed block in *target*.

    Only ever touches content between the REPOWISE_CORRECTIONS markers; a
    file without markers gets the block appended. Returns True when the file
    changed. Never creates a file just to remove a block.
    """
    exists = target.exists()
    if not exists and block is None:
        return False
    existing = target.read_text(encoding="utf-8") if exists else ""

    pattern = re.escape(CORRECTIONS_MARKER_START) + r".*?" + re.escape(CORRECTIONS_MARKER_END)
    if block is None:
        removal = r"\n*" + pattern + r"\n?"
        content = re.sub(removal, "", existing, flags=re.DOTALL)
        if content == existing:
            return False
        if content and not content.endswith("\n"):
            content += "\n"
    else:
        wrapped = f"{CORRECTIONS_MARKER_START}\n{block}\n{CORRECTIONS_MARKER_END}"
        if CORRECTIONS_MARKER_START in existing:
            # Lambda replacement: rule text carries Windows paths whose
            # backslashes re.sub would otherwise parse as escapes.
            content = re.sub(pattern, lambda _: wrapped, existing, flags=re.DOTALL)
        elif existing:
            content = existing.rstrip() + "\n\n" + wrapped + "\n"
        else:
            content = wrapped + "\n"
        if content == existing:
            return False

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return True


# ---------------------------------------------------------------------------
# Index-aware hints — where does the corrected path actually live?
# ---------------------------------------------------------------------------


def _attach_index_hints(repo_root: Path, rules: list[dict[str, Any]]) -> None:
    """Best-effort: annotate wrong_path rules with the indexed location.

    A plain sqlite3 read of the wiki sidecar (no engine, no ORM) — the hint
    is decoration, so any failure leaves the rules untouched.
    """
    targets = [r for r in rules if r["kind"] == "wrong_path"]
    db_path = repo_root / ".repowise" / "wiki.db"
    if not targets or not db_path.exists():
        return
    try:
        import sqlite3

        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1.0) as conn:
            for rule in targets:
                first_fixed = rule["fixed"].split()[0] if rule["fixed"].split() else ""
                basename = first_fixed.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                if not basename or len(basename) < 3:
                    continue
                row = conn.execute(
                    "SELECT DISTINCT file_path FROM wiki_symbols WHERE file_path LIKE ? LIMIT 2",
                    (f"%{basename}",),
                ).fetchall()
                if len(row) == 1:
                    rule["hint"] = f"indexed at {row[0][0]}"
    except Exception:
        return
