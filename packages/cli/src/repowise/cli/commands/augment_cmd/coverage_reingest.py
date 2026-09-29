"""PostToolUse Bash: re-ingest the coverage a test run just wrote.

An agent that runs the tests and then asks ``get_change_risk`` whether its
change is covered should get an answer about the run it just did, not the one
before. So after a command that runs a whole test suite, when an aggregate
coverage report on disk is newer than the index's last coverage ingest, the
hook starts ``repowise coverage add`` on exactly those reports in the
background and says so in one line.

It writes the store without being asked, so it is conservative: on by default
but switched off by ``hooks.coverage_reingest: false`` (or
``REPOWISE_HOOK_COVERAGE_REINGEST=0``); only in a repo that has ingested
coverage before; never for a run that targets some tests (a partial report
would replace full-suite coverage); never when discovery is customised in a
way this hook cannot follow; and never raising.

Cost is a few stats and one indexed SQLite read, only after a test command:
every other Bash call returns before touching the disk.
"""

from __future__ import annotations

import contextlib
import json
import re
import shlex
import time
from pathlib import Path

from ._shared import _find_repo_root, hook_flag_enabled

#: Report locations checked when ``coverage.paths`` is not set: the literal
#: (glob-free) entries of ``discovery.DEFAULT_DISCOVERY_GLOBS``. Spelled out
#: rather than imported because the discovery package costs ~1s to import;
#: kept in step by ``test_default_reports_match_discovery``. Ceiling: a report
#: only a ``**`` glob finds is not watched; name it in ``coverage.paths``.
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

#: Arguments that pick some tests out of a suite.
_TARGETING = ("-k", "-run", "-t", "--testnamepattern", "--filter", "--tests", "-dtest")
#: Positional arguments that still mean the whole suite.
_WHOLE_SUITE = frozenset({".", "./...", "...", "run", "--"})


def is_test_command(command: object) -> bool:
    """Whether *command* runs a test suite, in any segment."""
    return any(_segment_kind(s) is not None for s in _segments(command))


def is_full_test_run(command: object) -> bool:
    """Whether *command* runs a whole suite: a test command no segment narrows.

    Conservative on purpose: an argument that might name a test (a path, a
    ``::`` node id, ``-k``, ``-run``, ``-t``, ``--testNamePattern``,
    ``--filter``) marks the run partial. Ceiling: an option value that looks
    like a path after a short flag (``-p no:x``) also reads as partial, so such
    a run is not re-ingested; ``repowise coverage add`` by hand still is.
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
    return "partial" if _targets_some(args) else "full"


def _targets_some(args: str) -> bool:
    try:
        tokens = shlex.split(args)
    except ValueError:
        return True  # cannot tell, so do not replace full-suite coverage
    previous = ""
    for token in tokens:
        if _is_targeting_flag(token) or _names_some_tests(token, previous):
            return True
        previous = token
    return False


def _is_targeting_flag(token: str) -> bool:
    """``-k``, ``--filter=x``, ``-Dtest=Foo``, a ``::`` node id: picks tests out."""
    low = token.lower()
    return "::" in token or any(low == t or low.startswith(t + "=") for t in _TARGETING)


def _names_some_tests(token: str, previous: str) -> bool:
    """A positional argument (not a long option's value) that is not "the whole suite"."""
    if token.startswith("-") or token in _WHOLE_SUITE:
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
    fresh = _fresh_reports(repo, db)
    if not fresh:
        return None
    newest = max(mtime for _path, mtime in fresh)
    if _already_queued(repo, newest):
        return None
    paths = [path for path, _mtime in fresh]
    spawn_coverage_add(repo, paths)
    _write_queued(repo, newest)
    names = ", ".join(_rel(repo, p) for p in paths)
    return (
        f"[repowise] Re-ingesting coverage from {names} in the background "
        f"(log: .repowise/{LOG_FILENAME}); get_change_risk reads it once the ingest finishes."
    )


def _local_index(cwd: str) -> tuple[Path, Path] | None:
    """``(repo root, wiki.db)`` when the repo has a local index and the hook is on."""
    repo = _find_repo_root(Path(cwd))
    if repo is None:
        return None
    db = repo / ".repowise" / "wiki.db"
    # Ceiling: an index configured elsewhere (REPOWISE_DB_URL) is not read
    # here; `coverage add` by hand still works.
    if not db.is_file() or not hook_flag_enabled(repo, "coverage_reingest", default=True):
        return None
    return repo, db


def _fresh_reports(repo: Path, db: Path) -> list[tuple[Path, float]]:
    """Watched reports newer than the last ingest; none when the repo never ingested."""
    candidates = _candidates(repo)
    if not candidates:
        return []
    last = last_ingest_time(db)
    if not last:
        return []  # never ingested (0.0) or unreadable (None): not ours to start
    return [(path, mtime) for path, mtime in _existing(repo, candidates) if mtime > last]


def _candidates(repo: Path) -> tuple[str, ...]:
    """The report paths to watch, or ``()`` when the config says not to guess.

    ``coverage.paths`` when set. Otherwise the default locations, unless
    discovery is off or customised: ``auto_discover: false`` with no paths
    means reports are named by hand, and custom ``artifacts`` globs are ones
    this hook does not expand (ceiling: those repos re-ingest by hand).
    """
    block = _coverage_block(repo)
    if block is None:
        return ()
    paths = block.get("paths")
    if isinstance(paths, str):
        return (paths,)
    if isinstance(paths, list) and paths:
        return tuple(str(p) for p in paths if p)
    if block.get("auto_discover", True) is False or block.get("artifacts"):
        return ()
    return DEFAULT_REPORTS


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


def _existing(repo: Path, candidates: tuple[str, ...]) -> list[tuple[Path, float]]:
    """``(path, mtime)`` of the aggregate text reports among *candidates* that exist.

    A coverage.py ``.coverage`` database is skipped: it only feeds the per-test
    map, and ingesting it alone would stamp the older aggregate rows current.
    """
    found = []
    for rel in candidates:
        path = repo / rel
        if path.name.startswith(".coverage"):
            continue
        with contextlib.suppress(OSError):
            if path.is_file():
                found.append((path, path.stat().st_mtime))
    return found


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


def spawn_coverage_add(repo: Path, reports: list[Path]) -> None:
    """Start ``repowise coverage add <reports>`` detached, logging to ``.repowise/.coverage.log``.

    The log is truncated per spawn, so it holds the last ingest only.
    """
    import sys

    from repowise.cli.spawn import spawn_detached

    argv = [sys.executable, "-m", "repowise.cli.main", "coverage", "add", "--path", str(repo)]
    with open(repo / ".repowise" / LOG_FILENAME, "wb") as log:
        spawn_detached([*argv, *(str(p) for p in reports)], str(repo), log)
