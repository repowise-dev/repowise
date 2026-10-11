"""PostToolUse / PostToolUseFailure Bash: re-ingest the coverage a test run just wrote.

An agent that runs the tests and then asks ``get_change_risk`` whether its
change is covered should get an answer about the run it just did, not the one
before. So after a command that runs a whole test suite, when an aggregate
coverage report on disk is newer than the index's last coverage ingest, the
hook starts ``repowise coverage add`` on every watched report in the
background and says so in one line. In Claude Code it fires from repo-local
entries :func:`sync_repo_hook` writes.

It writes the store without being asked, so it is opt-in and conservative:
only with ``hooks.coverage_reingest: true`` (or
``REPOWISE_HOOK_COVERAGE_REINGEST=1`` for one session); only in a repo that has
ingested coverage before; never for a run that targets some tests (a partial report
would replace full-suite coverage); never when discovery is customised in a
way this hook cannot follow; and never raising.

Cost is a few stats and one indexed SQLite read, only after a test command:
every other Bash call returns before touching the disk.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import time
from pathlib import Path

from ._shared import _find_repo_root, hook_flag_enabled
from .fast_lookup import _DB_ENV_VARS

#: Report locations checked when ``coverage.paths`` is not set: the literal
#: (glob-free) entries of ``discovery.DEFAULT_DISCOVERY_GLOBS``. Spelled out
#: rather than imported because the discovery package costs ~1s to import;
#: kept in step by ``test_default_reports_match_discovery``. Ceiling: a report
#: only a ``**`` glob finds is not watched (a tree walk per test run); name it
#: in ``coverage.paths`` without ``**``.
DEFAULT_REPORTS = (
    "coverage/lcov.info",
    "lcov.info",
    "coverage.lcov",
    "coverage/cobertura.xml",
    "coverage/cobertura-coverage.xml",
    "coverage.xml",
    "coverage/coverage.xml",
    "coverage/clover.xml",
    "coverage.out",
    "cover.out",
    "target/site/jacoco/jacoco.xml",
)

QUEUED_FILENAME = ".coverage.queued"
LOG_FILENAME = ".coverage.log"

#: A queued marker older than this no longer blocks a spawn: the ingest it
#: stood for crashed or finished long ago.
QUEUED_STALE_AFTER_SECONDS = 10 * 60

#: Test runners the rewrite hook's ``test_output`` family does not name.
#: ``coverage run`` is a wrapper, peeled off before matching.
_EXTRA_TEST_RE = re.compile(
    r"^(?:cargo llvm-cov\b|mvn test\b|gradlew? test\b|bun test\b|npm run test[\w:-]*)"
)
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;\n]")
_PREFIX_RE = re.compile(r"^(?:time|timeout\s+\S+|coverage run(?:\s+-m)?)\s+")
_DISTILL_RE = re.compile(r"^repowise distill(?:\s+--source\s+\S+)?\s+(?P<rest>.+)$", re.DOTALL)

#: Arguments that pick some tests out of a suite (lower-cased).
_TARGETING = (
    "-k", "-run", "-t", "--testnamepattern", "--filter", "--tests", "-dtest",
    "--package", "--workspace", "-pl", "--projects",
)
#: Positional arguments that still mean the whole suite.
_WHOLE_SUITE = frozenset({".", "./...", "...", "run", "--"})
#: Per runner, the single-dash options whose next argument is their value, not
#: a test. Case-sensitive. Any other short flag's argument reads as a test, so
#: ``cargo test -p x``, ``npm test -w x`` and ``go test -C sub`` stay partial.
_PYTEST_VALUE_FLAGS = frozenset({"-p", "-n", "-c", "-o", "-W"})
_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "pytest": _PYTEST_VALUE_FLAGS,
    "py.test": _PYTEST_VALUE_FLAGS,
    "go": frozenset({
        "-count", "-timeout", "-parallel", "-cpu", "-tags", "-p",
        "-covermode", "-coverprofile", "-coverpkg",
    }),
}


def is_test_command(command: object) -> bool:
    """Whether *command* runs a test suite, in any segment."""
    return any(_segment_kind(s) is not None for s in _segments(command))


def is_full_test_run(command: object) -> bool:
    """Whether *command* runs a whole suite: a test command no segment narrows.

    Conservative on purpose: an argument that might name a test (a path, a
    ``::`` node id, ``-k``, ``-run``, ``-t``, ``--testNamePattern``,
    ``--filter``, ``--package``, ``--workspace``) marks the run partial. The
    value after one of the runner's :data:`_VALUE_FLAGS` is not a test.
    """
    kinds = [_segment_kind(s) for s in _segments(command)]
    return any(k is not None for k in kinds) and "partial" not in kinds


def _segments(command: object) -> list[str]:
    """The command's simple commands, the distill wrapper and prefixes peeled off."""
    if isinstance(command, list):  # Codex sends argv
        command = " ".join(str(c) for c in command)
    if not isinstance(command, str):
        return []
    if unwrapped := _undistill(command.strip()):
        # Before splitting: the rewritten form quotes a whole chain as one token.
        return _segments(unwrapped)
    out = []
    for raw in _SEGMENT_SPLIT.split(command):
        segment = raw.strip()
        if unwrapped := _undistill(segment):
            out += _segments(unwrapped)
        elif segment:
            out.append(segment)
    return out


def _undistill(segment: str) -> str | None:
    """The command inside ``repowise distill [--source X] <cmd>``, else ``None``."""
    m = _DISTILL_RE.match(segment)
    if m is None:
        return None
    rest = m.group("rest").strip()
    with contextlib.suppress(ValueError):
        tokens = shlex.split(rest)
        if len(tokens) == 1:  # the rewritten form wraps one quoted token
            rest = tokens[0]
    return rest


def _segment_kind(segment: str) -> str | None:
    """``"full"`` or ``"partial"`` for a test command, ``None`` for anything else."""
    from repowise.cli.rewrite_hook import FAMILY_PATTERNS, _normalize

    test_re = dict(FAMILY_PATTERNS)["test_output"]
    cmd = segment
    for _ in range(4):
        previous = cmd
        cmd = _PREFIX_RE.sub("", _normalize(cmd))
        if cmd == previous:
            break
    match = test_re.match(cmd) or _EXTRA_TEST_RE.match(cmd)
    if match is None:
        return None
    # A script suffix glued to the match (``npm run test:unit``) is still the name.
    args = re.sub(r"^\S+", "", cmd[match.end():])
    try:
        tokens = _original_case(shlex.split(args), segment)
    except ValueError:
        return "partial"  # cannot tell, so do not replace full-suite coverage
    runner = match.group(0).split()[0]
    return "partial" if _targets_some(tokens, _VALUE_FLAGS.get(runner, frozenset())) else "full"


def _original_case(tokens: list[str], segment: str) -> list[str]:
    """*tokens* (lower-cased by normalization) as the segment spelled them, when they align."""
    with contextlib.suppress(ValueError):
        tail = shlex.split(segment)[-len(tokens):] if tokens else []
        if [t.lower() for t in tail] == tokens:
            return tail
    return tokens


def _targets_some(tokens: list[str], value_flags: frozenset[str]) -> bool:
    previous = ""
    for token in tokens:
        if _is_targeting_flag(token) or _names_some_tests(token, previous, value_flags):
            return True
        previous = token
    return False


def _is_targeting_flag(token: str) -> bool:
    """``-k``, ``--filter=x``, ``-Dtest=Foo``, a ``::`` node id: picks tests out."""
    low = token.lower()
    return "::" in token or any(low == t or low.startswith(t + "=") for t in _TARGETING)


def _names_some_tests(token: str, previous: str, value_flags: frozenset[str]) -> bool:
    """A positional argument (not an option's value) that is not "the whole suite"."""
    if token.startswith("-") or token in _WHOLE_SUITE or previous in value_flags:
        return False
    return not (previous.startswith("--") and "=" not in previous)


def coverage_reingest_notice(tool_input: dict, cwd: str) -> str | None:
    """Spawn a background ``coverage add`` after a full test run with fresh reports.

    ``None`` (silence) in every other case, and on any error: the hook must
    never be the reason an agent's tool call reads as failed. Test runs often
    exit non-zero with a report written, so the exit code is not consulted.
    """
    try:
        return _notice(tool_input, cwd)
    except Exception:
        # The hook never raises: any failure is silence (no logger on this path).
        return None


def _notice(tool_input: dict, cwd: str) -> str | None:
    if not is_full_test_run(tool_input.get("command")):
        return None
    found = _local_index(cwd)
    if found is None:
        return None
    repo, db = found
    reports = _reports_to_ingest(repo, db)
    if not reports:
        return None
    newest = max(mtime for _path, mtime, _prefix in reports)
    if _already_queued(repo, newest):
        return None
    # A mapped ``coverage.paths`` entry keeps its prefix as ``PATH=PREFIX``.
    spawn_coverage_add(repo, [f"{p}={pre}" if pre else p for p, _mtime, pre in reports])
    _write_queued(repo, newest)
    names = ", ".join(_rel(repo, p) for p, _mtime, _prefix in reports)
    return (
        f"[repowise] Re-ingesting coverage from {names} in the background "
        f"(log: .repowise/{LOG_FILENAME}); get_change_risk reads it once the ingest finishes."
    )


def _local_index(cwd: str) -> tuple[Path, Path] | None:
    """``(repo root, wiki.db)`` when the repo has a local index and the hook is on.

    ``None`` under a configured database (``REPOWISE_DB_URL``): the ingest
    would write there, so the local file cannot say whether it is due.
    """
    if any(os.environ.get(name) for name in _DB_ENV_VARS):
        return None
    repo = _find_repo_root(Path(cwd))
    if repo is None:
        return None
    db = repo / ".repowise" / "wiki.db"
    if not db.is_file() or not hook_flag_enabled(repo, "coverage_reingest"):
        return None
    return repo, db


def _reports_to_ingest(repo: Path, db: Path) -> list[tuple[Path, float, str | None]]:
    """Every watched report, once any is newer than the last ingest; none otherwise.

    All of them, not just the fresh ones: the ingest replaces the stored
    coverage, so a run that rewrote one shard must not drop the others.
    """
    candidates = _candidates(repo)
    if not candidates:
        return []
    last = last_ingest_time(db)
    if not last:
        return []  # never ingested (0.0) or unreadable (None): not ours to start
    found = _existing(repo, candidates)
    return found if any(mtime > last for _path, mtime, _prefix in found) else []


def _candidates(repo: Path) -> dict[str, str | None]:
    """The report paths to watch, each with its prefix; none when the config says not to guess.

    ``coverage.paths`` when set (each entry a path, a glob or
    ``{path, path_prefix}``). Otherwise the default locations, unless
    discovery is off or customised: ``auto_discover: false`` with no paths
    means reports are named by hand, and custom ``artifacts`` globs are ones
    this hook does not expand (ceiling: those repos re-ingest by hand).
    """
    block = _coverage_block(repo)
    if block is None:
        return {}
    paths = block.get("paths")
    if isinstance(paths, str):
        return {paths: None}
    if isinstance(paths, list) and paths:
        return dict(pair for pair in map(_path_entry, paths) if pair is not None)
    if block.get("auto_discover", True) is False or block.get("artifacts"):
        return {}
    return dict.fromkeys(DEFAULT_REPORTS)


def _path_entry(entry: object) -> tuple[str, str | None] | None:
    """One ``coverage.paths`` entry as ``(pattern, prefix)``; ``None`` for an empty one."""
    if not isinstance(entry, dict):
        return (str(entry), None) if entry else None
    if not entry.get("path"):
        return None
    prefix = entry.get("path_prefix")
    return str(entry["path"]), (str(prefix) if prefix else None)


def _coverage_block(repo: Path) -> dict | None:
    """``coverage:`` from ``.repowise/config.yaml``; ``{}`` when absent, ``None`` when unreadable.

    A substring check first, like ``_shared.hook_flag_enabled``, so a config
    without the block never pays for the yaml import.
    """
    try:
        text = (repo / ".repowise" / "config.yaml").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    if "coverage" not in text:
        return {}
    try:
        import yaml

        block = (yaml.safe_load(text) or {}).get("coverage")
    except Exception:
        # The hook never raises: a config that does not parse reads as "cannot tell".
        return None
    return block if isinstance(block, dict) else {}


def _existing(
    repo: Path, candidates: dict[str, str | None]
) -> list[tuple[Path, float, str | None]]:
    """``(path, mtime, prefix)`` of the aggregate text reports among *candidates* that exist.

    A coverage.py ``.coverage`` database is skipped: it only feeds the per-test
    map, and ingesting it alone would stamp the older aggregate rows current.
    """
    found = []
    for rel, prefix in candidates.items():
        for path in _expand(repo, rel):
            if path.name.startswith(".coverage"):
                continue
            with contextlib.suppress(OSError):
                if path.is_file():
                    found.append((path, path.stat().st_mtime, prefix))
    return found


def _expand(repo: Path, rel: str) -> list[Path]:
    """The files one watched entry names: a literal path as is, a glob expanded."""
    if "**" in rel:
        return []  # ceiling: see DEFAULT_REPORTS
    if not any(c in rel for c in "*?["):
        return [repo / rel]
    # Only a glob pays for the discovery import (absolute roots, pruned dirs).
    from repowise.core.analysis.health.coverage.discovery import expand_report_patterns

    return expand_report_patterns([rel], repo)


def _rel(repo: Path, path: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def last_ingest_time(db: Path) -> float | None:
    """Epoch seconds of the newest coverage ingest; ``0.0`` when none; ``None`` when unreadable."""
    import sqlite3

    try:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=0.1)
    except sqlite3.Error:
        return None
    try:
        raw = _newest_ingested_at(con)
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if raw is _NO_TABLE:
        return None
    return 0.0 if raw is None else _epoch(raw)


#: :func:`_newest_ingested_at` found neither table: an index too old to say.
_NO_TABLE = object()


def _newest_ingested_at(con) -> object:
    """``MAX(ingested_at)`` from the ingest history, else the coverage rows."""
    import sqlite3

    found: object = _NO_TABLE
    for table in ("coverage_ingests", "coverage_files"):
        try:
            found = con.execute(f"SELECT MAX(ingested_at) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            continue  # an index older than the table
        if found is not None:
            return found
        # Empty: an index upgraded from before the ingest history keeps its
        # ingests only on the coverage rows, so read those next.
    return found


def _epoch(raw: object) -> float | None:
    """A stored ``ingested_at`` as epoch seconds; ``None`` when it does not parse."""
    from datetime import UTC, datetime

    try:
        at = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    # Stored in UTC; SQLite keeps no zone.
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp()


def _already_queued(repo: Path, newest: float) -> bool:
    """A recent spawn already covers reports this new."""
    try:
        marker = json.loads((repo / ".repowise" / QUEUED_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    queued_at, report_mtime = marker.get("queued_at"), marker.get("report_mtime")
    if not isinstance(queued_at, (int, float)) or not isinstance(report_mtime, (int, float)):
        return False
    return time.time() - queued_at <= QUEUED_STALE_AFTER_SECONDS and report_mtime >= newest


def _write_queued(repo: Path, newest: float) -> None:
    with contextlib.suppress(OSError):
        (repo / ".repowise" / QUEUED_FILENAME).write_text(
            json.dumps({"queued_at": time.time(), "report_mtime": newest}), encoding="utf-8"
        )


def spawn_coverage_add(repo: Path, reports: list[Path | str]) -> None:
    """Start ``repowise coverage add <reports>`` detached, logging to ``.repowise/.coverage.log``.

    The log is truncated per spawn, so it holds the last ingest only.
    """
    import sys

    from repowise.cli.spawn import spawn_detached

    argv = [sys.executable, "-m", "repowise.cli.main", "coverage", "add", "--path", str(repo)]
    with open(repo / ".repowise" / LOG_FILENAME, "wb") as log:
        spawn_detached([*argv, *(str(p) for p in reports)], str(repo), log)


def sync_repo_hook(repo_path: Path, console) -> None:
    """Keep this repo's Claude Code coverage re-ingest entries in step with its policy.

    Called wherever coverage gets stored (``coverage add``, ``init``,
    ``update``). Adds the entries when ``hooks.coverage_reingest`` is true, the
    repo has a stored coverage ingest and Claude Code has the repowise augment
    hooks; removes them whenever it is not true. Prints one line when the file
    changed. Never raises: a hook entry is not worth failing an ingest over.
    """
    try:
        from repowise.cli.editor_integrations.claude_config import (
            claude_code_augment_installed,
            set_repo_coverage_hook,
        )

        if not hook_flag_enabled(repo_path, "coverage_reingest"):
            removed = set_repo_coverage_hook(repo_path, False)
            if removed is not None:
                console.print(
                    f"[dim]Removed the coverage re-ingest hook from {removed} "
                    "(hooks.coverage_reingest is not true).[/dim]"
                )
            return
        found = _local_index(str(repo_path))
        if found is None or not last_ingest_time(found[1]) or not claude_code_augment_installed():
            return
        added = set_repo_coverage_hook(repo_path, True)
        if added is not None:
            console.print(
                f"[dim]Added the coverage re-ingest hook to {added} (this repository only).[/dim]"
            )
    except OSError:
        # A settings file we cannot write is not worth failing an ingest over;
        # an unreadable one already reads as "no change" in the writer.
        return
