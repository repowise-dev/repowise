"""Shared `_meta` envelope helpers for MCP tool responses.

Every tool can attach a small `_meta` dict to its response with timing and
optional hint text. The hint is the killer feature: a short, conservative
nudge toward the cheaper next-tool when one obviously applies. Hints are
intentionally narrow — pushing every agent toward `get_symbol` regardless of
question shape would replicate the over-trust failure mode that drove
jcodemunch's accuracy regression on alive-with-dead-exports tasks.

Rules of thumb baked into the hint generators:
  * NEVER suggest a more compact tool when the original question contains
    explanation words ("explain", "why", "how does", "what is the relationship",
    "describe").
  * Only suggest get_symbol when the agent has already pinpointed a single
    symbol or single file — never as a starting move.
  * Hints are advisory; the harness/agent is free to ignore them.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.git_head import read_head_commit, same_path
from repowise.core.index_scope import (
    CANONICAL_INDEX_SCOPE_PROJECTION,
    INDEX_SCOPE_ENV,
    answers_upgrade_line,
    compact_index_scope,
    index_scope_fingerprint,
    load_index_scope,
)
from repowise.server.mcp_server import _checkout, _code_drift
from repowise.server.mcp_server._rounding import round_float

# 2: index_scope carries the compact projection on routine responses. The key
# and its version field are unchanged, so a consumer reading the old shape has
# no way to notice from index_scope itself — the envelope version is where a
# wire-shape change is announced. REPOWISE_MCP_INDEX_SCOPE=full restores it.
# 3: the lean envelope. Diagnostics (this version, timing, budget accounting,
# an uncapped completeness block, a complete index_scope digest, savings) leave
# routine responses; get_overview and REPOWISE_MCP_DEBUG_META=1 keep them.
# 4: one wire vocabulary (see lean_response): no empty or false-status fields,
# `_meta.omitted` plus `<x>_total` on cut lists for truncation, `stale_warning`
# alone for freshness, one `_meta.degraded` list.
MCP_CONTRACT_VERSION = 4

#: Restores the diagnostic ``_meta`` fields on every response, for diagnosis.
DEBUG_META_ENV = "REPOWISE_MCP_DEBUG_META"

#: Called once per session, so it is where the whole envelope is worth its bytes.
_FULL_META_TOOLS = frozenset({"get_overview"})


def full_meta(tool: str | None = None) -> bool:
    """Whether *tool*'s response keeps the diagnostic ``_meta`` fields.

    Read per call, like the scope switch, so a client-spawned server picks a
    change up without a restart.
    """
    if tool in _FULL_META_TOOLS:
        return True
    return os.environ.get(DEBUG_META_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

# Only warn about age when we have no other signal AND the index is genuinely
# old. A short threshold here would nag on every call and train the agent to
# ignore the field — defeating the point. 90 days is a deliberate floor: by
# then even quiet repos have likely drifted enough that a re-index matters.
# The preferred path is the git-HEAD comparison below, which fires only on a
# real mismatch and gives a single, calibrated stale signal.
_STALE_AGE_FLOOR_DAYS = 90


def served_root(repository: Any, ctx_path: str | Path | None = None) -> str | None:
    """The checkout to read live HEAD, state.json and files from for *repository*.

    The stored ``local_path`` is where the repo was indexed; a moved or copied
    repo keeps the old value, so reads through it see another tree. A
    single-repo server's own path wins for the row its own store identifies
    (``_state._served_repo_id``) when that path holds ``.repowise/`` and
    ``.git`` (a file in a worktree). Any other row, such as another repo in a
    shared store, keeps its stored path; *ctx_path* stands in only when the
    row has none. The row is never rewritten.
    """
    from repowise.server.mcp_server import _state

    caller = _checkout.current()
    local = getattr(repository, "local_path", None)
    if caller is not None and caller.own_index and local and same_path(local, caller.served):
        # A worktree index seeded from this one keeps its repository id.
        return str(caller.served)
    served = _state._repo_path
    if (
        served
        and _state._registry is None
        and _state._served_repo_id is not None
        and getattr(repository, "id", None) == _state._served_repo_id
    ):
        root = Path(served)
        if (root / ".repowise").is_dir() and (root / ".git").exists():
            return str(root)
    # Ceiling: a workspace server keeps each repo's stored path; giving it
    # the same override needs the registry to record each store's one row.
    return getattr(repository, "local_path", None) or (str(ctx_path) if ctx_path else None)


def read_live_head(local_path: str | None) -> str | None:
    """Read the repo's current git HEAD SHA via plain file I/O.

    Returns a full SHA, or ``None`` when the repo isn't a git checkout on
    disk (hosted indexes, ephemeral clones). Avoids spawning ``git``: the
    shared reader parses ``HEAD`` and follows at most one ref — fast enough
    to call on every MCP tool response without caching, and never blocks the
    event loop. A linked worktree or submodule, whose ``.git`` is a
    ``gitdir:`` file, resolves the same way.

    Detached HEADs are handled (the HEAD file contains the SHA directly).
    Unknown ref formats just return ``None`` rather than guessing — staleness
    semantics should fail closed (no warning) rather than open (false alarms).

    ``repositories.head_commit`` is written by the same helper, so the
    freshness comparison stays symmetric with what ``update`` stored.
    """
    return read_head_commit(local_path)


def read_index_scope(local_path: str | None) -> dict[str, Any] | None:
    """Read the canonical persisted scope, or a conservative legacy projection."""
    if not local_path:
        return None
    return load_index_scope(local_path)


def _canonical_scope_requested() -> bool:
    """Whether the environment asks for the whole scope on every response.

    The compatibility window, for a reader that parses the full object and has
    no way yet to ask for it by name. Read per call, so turning it on does not
    need a restart of a server a client spawned.
    """
    return (
        os.environ.get(INDEX_SCOPE_ENV, "").strip().lower()
        == CANONICAL_INDEX_SCOPE_PROJECTION
    )


def index_scope_for_response(local_path: str | None) -> dict[str, Any] | None:
    """The scope an ordinary response carries: the digest, unless asked."""
    scope = read_index_scope(local_path)
    if scope is None or _canonical_scope_requested():
        return scope
    return compact_index_scope(scope)


def resolve_indexed_commit(head_commit: str | None, local_path: str | None) -> str | None:
    """The commit the index reflects, from ``state.json`` and the DB ``head_commit``.

    One resolver with impacted-test selection (see
    :func:`repowise.core.workspace.update.resolve_indexed_commit`), so a
    response never names an indexed commit in ``_meta`` while its selection
    calls the same index unknown. ``None`` when the two stamps diverge. Hosted
    indexes have no state file and keep the DB value.
    """
    from repowise.core.workspace.update import resolve_indexed_commit as _resolve

    return _resolve(Path(local_path) if local_path else None, head_commit).sha


# (local_path, indexed_sha, live_sha) -> files changed between the two commits,
# or None when git couldn't answer (unknown SHA after a rebase, no git, timeout).
# One git spawn per SHA pair per process; every later response reuses the set.
_changed_files_cache: dict[tuple[str, str, str], frozenset[str] | None] = {}
_CHANGED_FILES_CACHE_MAX = 32


def _changed_files_between(
    local_path: str, indexed_full: str, live_full: str
) -> frozenset[str] | None:
    """Files that differ between the indexed commit and live HEAD, cached.

    Returns ``None`` (not an empty set) whenever git can't answer, so callers
    fall back to the repo-level warning instead of falsely reporting the
    served targets as unaffected.
    """
    key = (local_path, indexed_full, live_full)
    if key in _changed_files_cache:
        return _changed_files_cache[key]
    changed: frozenset[str] | None = None
    try:
        import subprocess

        # stdin=DEVNULL + captured stdout/stderr: on stdio transport a child
        # that inherits the JSON-RPC pipe handles can wedge the session (same
        # failure mode as the get_answer git-grep hang).
        res = subprocess.run(
            [
                "git",
                "-C",
                local_path,
                "--no-pager",
                "diff",
                "--name-only",
                indexed_full,
                live_full,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            stdin=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            changed = frozenset(
                line.strip().replace("\\", "/") for line in res.stdout.splitlines() if line.strip()
            )
    except Exception:
        changed = None
    if len(_changed_files_cache) >= _CHANGED_FILES_CACHE_MAX:
        _changed_files_cache.clear()
    _changed_files_cache[key] = changed
    return changed


def _normalize_target_path(target: str) -> str:
    """Reduce a tool target to a repo-relative slash path for intersection."""
    path = target.replace("\\", "/").strip()
    # "path.py::Symbol" and "path.py:140-180" both reduce to the file path.
    if "::" in path:
        path = path.split("::", 1)[0]
    elif ":" in path and path.rsplit(":", 1)[-1].replace("-", "").isdigit():
        path = path.rsplit(":", 1)[0]
    return path.removeprefix("./").rstrip("/")


def targets_hit_by_changes(targets: list[str], changed: frozenset[str]) -> bool:
    """True when any served target (file, symbol, or module dir) changed."""
    for raw in targets:
        path = _normalize_target_path(raw)
        if not path:
            continue
        if path in changed:
            return True
        prefix = path + "/"
        if any(f.startswith(prefix) for f in changed):
            return True
    return False


# local_path -> (monotonic read time, dirty paths or None). Short-lived so a
# burst of tool calls shares one ``git status`` while a fresh edit still shows;
# a failed read is kept longer so a slow repo is not re-asked every call.
_dirty_paths_cache: dict[str, tuple[float, frozenset[str] | None]] = {}
_DIRTY_PATHS_TTL_S = 3.0
_DIRTY_PATHS_FAILED_TTL_S = 60.0
_DIRTY_PATHS_CACHE_MAX = 32
# Runs inline on the event loop from build_meta, so the bound is tight; a slow
# repo times out once and reads as "not evaluated" for the failed TTL.
_DIRTY_PATHS_TIMEOUT_S = 0.5
_UNTRACKED_DIR_FILE_CAP = 200


def _working_tree_dirty_paths(local_path: str) -> frozenset[str] | None:
    """Paths with staged, unstaged or untracked changes; ``None`` when unknown.

    An untracked directory is reported once, with a trailing ``/``. Paths are
    git-root relative; the ``.git`` gate below makes that root ``local_path``.
    """
    now = time.monotonic()
    hit = _dirty_paths_cache.get(local_path)
    if hit is not None:
        ttl = _DIRTY_PATHS_TTL_S if hit[1] is not None else _DIRTY_PATHS_FAILED_TTL_S
        if now - hit[0] < ttl:
            return hit[1]
    dirty: frozenset[str] | None = None
    # Only at a repository root: ``git -C`` on a plain directory would answer
    # for whatever repository happens to enclose it.
    if (Path(local_path) / ".git").exists():
        try:
            import subprocess

            res = subprocess.run(
                [
                    "git",
                    "-C",
                    local_path,
                    "--no-pager",
                    "status",
                    "--porcelain",
                    "-z",
                    "--untracked-files=normal",
                ],
                capture_output=True,
                timeout=_DIRTY_PATHS_TIMEOUT_S,
                stdin=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                entries = res.stdout.decode("utf-8", errors="replace").split("\0")
                paths: set[str] = set()
                i = 0
                while i < len(entries):
                    entry = entries[i]
                    i += 1
                    if len(entry) < 4:
                        continue
                    paths.add(entry[3:])
                    if entry[0] in "RC" and i < len(entries):
                        # The rename or copy source follows; it is gone or
                        # changed in the working tree too.
                        paths.add(entries[i])
                        i += 1
                dirty = frozenset(paths)
        except Exception:
            dirty = None
    if len(_dirty_paths_cache) >= _DIRTY_PATHS_CACHE_MAX:
        _dirty_paths_cache.clear()
    _dirty_paths_cache[local_path] = (now, dirty)
    return dirty


def _working_tree_record(local_path: str) -> tuple[frozenset[str], float] | None:
    """Paths the last ``update --working-tree`` indexed, and when state.json was written."""
    state_path = Path(local_path) / ".repowise" / "state.json"
    try:
        paths = json.loads(state_path.read_text(encoding="utf-8")).get("working_tree_paths")
        written = state_path.stat().st_mtime
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(paths, list):
        return None
    return frozenset(_fold(p) for p in paths if isinstance(p, str)), written


def _fold(path: str) -> str:
    # Case-insensitive filesystems: a target and git's spelling may differ in case.
    return path.casefold() if sys.platform in ("win32", "darwin") else path


def _indexed_from_working_tree(
    local_path: str, path: str, record: tuple[frozenset[str], float] | None
) -> bool:
    # Only paths are recorded, so the state.json write time stands in for the
    # run's. Ceiling: a later commit-anchored update rewrites state.json and
    # would cover a re-edit made in between; recording per-path stamps at
    # update time lifts it.
    if record is None:
        return False
    target = Path(local_path) / path
    if path.endswith("/"):
        # An untracked directory: its own mtime misses edits to files inside,
        # so it is covered only when every file under it is.
        from itertools import islice

        from repowise.core.fs_walk import iter_glob

        try:
            files = [
                f
                for f in islice(iter_glob(target, "*"), _UNTRACKED_DIR_FILE_CAP + 1)
                if f.is_file()
            ]
        except OSError:
            return False
        if len(files) > _UNTRACKED_DIR_FILE_CAP:
            # Too large to check inline; stays marked rather than guessed covered.
            return False
        return bool(files) and all(
            _indexed_from_working_tree(local_path, f.relative_to(local_path).as_posix(), record)
            for f in files
        )
    if _fold(path) not in record[0]:
        return False
    # A deleted file has no mtime; its directory's changes when it goes.
    probe = target if target.exists() else target.parent
    try:
        return probe.stat().st_mtime <= record[1]
    except OSError:
        return False


def uncommitted_targets(local_path: str | None, targets: list[str] | None) -> list[str]:
    """Served targets whose uncommitted edits the index has not seen.

    Empty when git cannot answer: that is "not evaluated", never an error.
    """
    if not local_path or not targets:
        return []
    dirty = _working_tree_dirty_paths(local_path)
    if not dirty:
        return []
    record = _working_tree_record(local_path)
    folded = [(d, _fold(d)) for d in dirty]
    out: list[str] = []
    for raw in targets:
        path = _normalize_target_path(raw)
        if not path or path in out:
            continue
        key = _fold(path)
        if any(
            (fd == key or fd.startswith(key + "/") or (fd.endswith("/") and key.startswith(fd)))
            and not _indexed_from_working_tree(local_path, d, record)
            for d, fd in folded
        ):
            out.append(path)
    return out


def reverted_targets(local_path: str | None, targets: list[str] | None) -> list[str]:
    """Served targets indexed from working-tree edits that are no longer there.

    A reverted edit leaves git status clean while the index still holds it.
    Only meaningful while HEAD equals the indexed commit; the caller checks.
    """
    if not local_path or not targets:
        return []
    record = _working_tree_record(local_path)
    if not record or not record[0]:
        return []
    dirty = _working_tree_dirty_paths(local_path)
    if dirty is None:
        return []
    dirty_keys = {_fold(d) for d in dirty}

    def _still_dirty(rec: str) -> bool:
        return rec in dirty_keys or any(d.endswith("/") and rec.startswith(d) for d in dirty_keys)

    out: list[str] = []
    for raw in targets:
        path = _normalize_target_path(raw)
        if not path or path in out:
            continue
        key = _fold(path)
        if any(
            (rec == key or rec.startswith(key + "/")) and not _still_dirty(rec)
            for rec in record[0]
        ):
            out.append(path)
    return out


def freshness_from_repo(
    repository: Any | None, targets: list[str] | None = None, root: str | None = None
) -> dict[str, Any]:
    """Return a minimal freshness dict for the given Repository row.

    Calibrated to fire ``stale_warning`` rarely so the agent keeps trusting
    the tools. Two signals, in order of preference:

      1. **Git HEAD comparison** (preferred). If the repo is a checkout on
         disk, compare ``repository.head_commit`` to the live HEAD. They
         match → silence. They differ → ``stale_warning`` plus the live SHA
         so the agent can decide whether to call ``repowise update``.
      2. **Age fallback** (only when git is unreachable). Warn only past 90
         days; under that, just emit ``index_age_days`` for the agent to
         consult on its own terms.

    Pass ``targets`` (the file/symbol/module paths this response actually
    serves) to scope the mismatch signal: when HEAD has moved but none of the
    served targets changed between the indexed commit and live HEAD, the
    response carries ``index_behind: true`` on its own, with no ``stale_warning``.
    "Silence means current" only trains trust if the warning fires only when
    the served content is actually affected — a repo-wide warning after every
    unrelated commit teaches the agent to ignore the field. ``targets=None``
    (repo-level tools like get_overview) keeps the repo-level warning;
    ``targets=[]`` means "no file content served" and never warns.

    Always-emitted fields:
      * ``index_age_days``  — informational, never a directive
      * ``indexed_commit``  — short SHA the index was built against

    Conditionally emitted:
      * ``live_head``       — current checkout commit whenever readable
      * ``stale_warning``   — only on a real signal (a served target changed,
        HEAD mismatch with real file changes on a repo-level response, OR very
        old with no git)
      * ``index_behind``.   The live-vs-indexed comparison ran and reached a
        definitive answer: ``true`` whenever HEAD has moved (with or without an
        accompanying ``stale_warning``, which is the sharper, content-scoped
        signal), ``false`` when the two commits match. Omitted only when the
        comparison could not run at all (no git, no local path, or no indexed
        commit), so absence means "not evaluated", never "false". Emitting the
        false case matters downstream: a field that is only ever present as
        ``true`` makes every consumer-side rate read 100%.
      * ``working_tree_dirty``: count of served targets with uncommitted edits
        no ``repowise update --working-tree`` has indexed; those also set
        ``stale_warning`` when nothing else has. A served file indexed from
        uncommitted edits that were since reverted sets ``stale_warning`` alone.

    Defensive throughout: any missing piece is dropped rather than raised so
    an upstream change to the Repository model can never poison a tool result.
    """
    if repository is None:
        return {}
    out: dict[str, Any] = {"contract_version": MCP_CONTRACT_VERSION} if full_meta() else {}

    # Deferred: this module's top-level imports stay free of the ORM.
    from repowise.core.persistence.crud import index_synced_at

    synced_at = index_synced_at(repository)
    age_days: int | None = None
    if synced_at is not None:
        age_days = max(0, (datetime.now(UTC) - synced_at).days)
        out["index_age_days"] = age_days

    # ``root``: the served checkout (see served_root), resolved here when omitted.
    local_path = root or served_root(repository)
    # state.json and the DB row reconciled by the shared resolver.
    indexed_full = resolve_indexed_commit(
        getattr(repository, "head_commit", None) or None, local_path
    )
    if indexed_full:
        out["indexed_commit"] = indexed_full[:12] if isinstance(indexed_full, str) else indexed_full

    borrowed = _checkout.borrowed_checkout(local_path)
    if borrowed is not None:
        out.update(_borrowed_freshness(borrowed, indexed_full, targets, local_path))
        return out

    live_full = read_live_head(local_path)
    if live_full:
        out["live_head"] = live_full[:12]

    if live_full and indexed_full:
        if live_full != indexed_full:
            # HEAD moved, so the index *is* behind, true regardless of which
            # sub-branch below fires. Emitted unconditionally here (rather than
            # only on the quiet branches) so consumers can compute a rate:
            # a key that only ever appears as True makes every aggregate 100%.
            out["index_behind"] = True
            changed = (
                _changed_files_between(local_path, indexed_full, live_full) if local_path else None
            )
            if changed is not None and not changed:
                # HEAD moved but the two trees are identical (an empty commit, a
                # merge that changed nothing, a tag-only move). Nothing this or
                # any other response serves can be stale, so the repo-level
                # warning was crying wolf on a repo that was completely current —
                # and a warning that fires when nothing changed trains an agent
                # to stop reading the field. Checked before the targets branch
                # because it holds for repo-level responses too, which are
                # exactly the ones that previously always warned.
                pass
            elif targets is not None and changed is not None:
                if targets_hit_by_changes(targets, changed):
                    out["stale_warning"] = (
                        "A file this response serves changed after indexing — "
                        "verify against source or run `repowise update`."
                    )
            else:
                # The two SHAs are already in ``indexed_commit`` / ``live_head`` —
                # don't repeat them in prose. Just the directive.
                out["stale_warning"] = "Index is behind live HEAD — run `repowise update`."
        else:
            # Match. No prose, since silence is the signal, but the comparison
            # did run and its answer is "not behind", so say so explicitly.
            out["index_behind"] = False
    elif live_full is None and age_days is not None and age_days > _STALE_AGE_FLOOR_DAYS:
        # No git signal available and the index is genuinely old.
        out["stale_warning"] = (
            f"Index is {age_days} days old and live HEAD is unreachable — "
            "results may be stale. Run `repowise update`."
        )

    # Freshness above is commit-anchored, so an uncommitted edit to a served
    # file would otherwise read as current. Served targets only: a dirty tree
    # elsewhere is no reason to warn.
    if targets:
        modified = uncommitted_targets(local_path, targets)
        if modified:
            out["working_tree_dirty"] = len(modified)
            out.setdefault(
                "stale_warning",
                "A file this response serves has uncommitted edits: source reads are live, "
                "but graph and index facts for it predate the edit. "
                "Run `repowise update --working-tree`.",
            )
        elif live_full and live_full == indexed_full and reverted_targets(local_path, targets):
            out["stale_warning"] = (
                "A file this response serves was indexed from uncommitted edits that are "
                "no longer in the working tree. Run `repowise update --working-tree`."
            )

    return out


def _borrowed_hit(
    local_path: str | None, indexed_full: str, head: str, targets: list[str] | None
) -> str | None:
    """``"file"`` when a served file differs between the two commits, ``"repo"``
    for a repo-level response or an unknown diff, else ``None``."""
    if head == indexed_full:
        return None
    changed = _changed_files_between(local_path, indexed_full, head) if local_path else None
    if changed is None or (changed and targets is None):
        return "repo"
    if changed and targets and targets_hit_by_changes(targets, changed):
        return "file"
    return None


def _borrowed_freshness(
    caller: Any, indexed_full: str | None, targets: list[str] | None, local_path: str | None
) -> dict[str, Any]:
    """Freshness of this index for another checkout of the repo, as a fact.

    The caller's HEAD stands in for live HEAD, and its working tree for the
    uncommitted edits. The warning names both checkouts and commits instead
    of an update command: updating this index does not describe the caller's.
    """
    out: dict[str, Any] = {}
    head = read_live_head(str(caller.caller))
    if head:
        out["live_head"] = head[:12]
    dirty = uncommitted_targets(str(caller.caller), targets)
    if dirty:
        out["working_tree_dirty"] = len(dirty)
    hit = None
    if head and indexed_full:
        out["index_behind"] = head != indexed_full
        hit = _borrowed_hit(local_path, indexed_full, head, targets)
    if hit or dirty:
        out["stale_warning"] = _checkout.checkout_fact(
            caller, indexed_full, head, served_file=bool(dirty) or hit == "file"
        )
    return out


def build_meta(
    *,
    timing_ms: float | None = None,
    hint: str | None = None,
    cached: bool = False,
    repository: Any | None = None,
    targets: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a `_meta` envelope. All fields optional, omitted if falsy.

    Pass ``repository`` to auto-inject freshness fields (``index_age_days``,
    ``indexed_commit``, optional ``stale_warning``). Every MCP response should
    carry these so an agent can detect drift between the index and live HEAD
    without an extra round-trip. Pass ``targets`` (the paths this response
    serves) to scope ``stale_warning`` to actually-affected content — see
    :func:`freshness_from_repo`.

    ``index_scope`` carries the compact projection (the run mode, the
    provenance, the git tier, one word for whether the index is whole, and a
    fingerprint identifying the canonical object), and only when that word is
    not ``complete``. See :func:`build_meta_with_full_scope` for the calls that are
    worth the whole thing.

    Stable shape:
      {
        "timing_ms":       float,  # tool wall-time (REPOWISE_MCP_DEBUG_META only)
        "hint":            str,    # short follow-up suggestion (omitted if None)
        "cached":          bool,   # only included when True
        "index_age_days":  int,    # days since last `repowise update`
        "indexed_commit":  str,    # short SHA the index was built against
        "stale_warning":   str,    # only when age > threshold
        ...extras
      }
    """
    full = full_meta()
    out: dict[str, Any] = {"contract_version": MCP_CONTRACT_VERSION} if full else {}
    if timing_ms is not None and full:
        # Through the shared quantizer, not ``round(..., 2)``. A wall-clock
        # duration is a float like any other on this wire, and two decimal
        # places is not the same rule the rest of the payload follows: a
        # 1077.85 ms measurement survives ``round`` but the significant-digit
        # rule wants 1078.0, so the field tripped the no-raw-doubles guard
        # whenever the run happened to land off an integer. ``round_float``
        # returns None for a non-finite measurement, which is not valid JSON,
        # so the key is omitted rather than emitted as null.
        rounded_ms = round_float(float(timing_ms))
        if rounded_ms is not None:
            out["timing_ms"] = rounded_ms
    if hint:
        out["hint"] = hint
    if cached:
        out["cached"] = True
    if repository is not None:
        root = served_root(repository)
        out.update(freshness_from_repo(repository, targets=targets, root=root))
        scope = index_scope_for_response(root)
        # A complete digest restates the default; only a gap is news.
        if scope is not None and (
            full or _canonical_scope_requested() or scope.get("status") != "complete"
        ):
            out["index_scope"] = scope
    out.update(_embedder_meta())
    out.update(_release_meta())
    if extra:
        out.update(extra)
    return out


def build_meta_with_full_scope(**kwargs: Any) -> dict[str, Any]:
    """:func:`build_meta` for an orientation call: the whole ``index_scope``.

    A separate function rather than a parameter on ``build_meta``, which has
    68 call sites that all want the digest and one that wants this. A knob
    every caller must read past to learn it does not apply to them belongs
    beside the one caller it does.
    """
    meta = {"contract_version": MCP_CONTRACT_VERSION, **build_meta(**kwargs)}
    repository = kwargs.get("repository")
    if repository is not None:
        scope = read_index_scope(served_root(repository))
        if scope is not None:
            # The fingerprint is what makes a held copy checkable against a
            # later digest, so the copy being held has to carry it too.
            meta["index_scope"] = {
                **scope,
                "fingerprint": index_scope_fingerprint(scope),
            }
    return meta


def persisted_analysis_meta(
    timestamp: str | None,
    commits: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Trust metadata for a persisted analysis artifact, omitting unknowns."""
    out: dict[str, Any] = {}
    if timestamp:
        out["analysis_timestamp"] = timestamp
    if commits:
        out["analysis_commits"] = commits
    return out


def finalize_trust_envelope(result: Any, *, evidence_kind: str | None = None) -> Any:
    """Apply the transport-level trust contract to a tool result."""
    if not isinstance(result, dict):
        return result
    raw_meta = result.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    result["_meta"] = meta
    if full_meta():
        meta.setdefault("contract_version", MCP_CONTRACT_VERSION)
    if evidence_kind:
        meta.setdefault("evidence_kind", evidence_kind)
    if evidence_kind == "structural":
        meta.setdefault("runtime_breakage_proven", False)
    elif evidence_kind == "generated":
        meta.setdefault("existing_verified_code", False)
    # Here, not in build_meta, so every tool and every shielded error carries it.
    meta.update(_checkout.checkout_meta())
    server_stale = _code_drift.stale_line()
    if server_stale:
        meta["server_stale"] = server_stale

    state: dict[str, Any] = {}
    combined = {**result, **meta}
    # The bool is deliberately coarse. Carry the values behind it, not just the
    # key names: the producers already hold the answer -- a synthesis reason
    # string, the list of retrieval legs that broke -- and naming only the key
    # would discard it.
    degraded_by = {
        key: value
        for key, value in sorted(combined.items())
        if value and (key == "degraded" or key.endswith("_degraded"))
    }
    if degraded_by:
        state["degraded"] = True
        state["degraded_reasons"] = degraded_by
    if any(
        value and (key == "partial" or key.endswith("_partial")) for key, value in combined.items()
    ):
        state["partial"] = True
    if any(
        value and (key == "truncated" or "truncated" in key or key == "omitted")
        for key, value in combined.items()
    ):
        state["truncated"] = True
    if state:
        existing = meta.get("state")
        if isinstance(existing, dict):
            state = {**existing, **state}
        meta["state"] = state
    return result


#: Freshness detail behind ``stale_warning``; the hashes and age are debug data.
_DEBUG_FRESHNESS_KEYS = (
    "index_age_days",
    "indexed_commit",
    "live_head",
    "index_behind",
    "working_tree_dirty",
)
#: Accounting a reader gets from ``_meta.omitted`` and ``<x>_total`` instead.
_DEBUG_ACCOUNTING_KEYS = ("completeness", "response_budget", "floor", "reductions", "state")
#: Stamped by middleware layers for debug output; ``timed`` and the savings
#: layer drop them on a lean call too, and listing them here keeps the lean
#: shape the one place that says what a lean reply leaves out.
_DEBUG_LAYER_KEYS = ("timing_ms", "replaced_tokens", "tokens_saved")
#: A ``false`` under these names says nothing happened, so it is not sent.
_STATUS_FLAGS = ("degraded", "truncated", "capped", "partial")
#: Siblings of a capped ``<x>`` that ``<x>_total`` already says.
_CAP_COUNTERS = ("_emitted", "_reduced_reason", "_omitted", "_truncated")


def lean_response(result: Any) -> Any:
    """The delivered shape of a tool result: only fields an agent can act on.

    The one owner of the wire vocabulary, run outermost on MCP responses;
    ``get_overview`` and ``REPOWISE_MCP_DEBUG_META`` skip it.

    * No ``None`` or empty string; no empty container below the top level
      unless a ``<key>_basis`` sibling explains it; no ``false`` status flag.
    * Truncation is ``_meta.omitted`` plus ``<x>_total`` on a list actually cut.
    * Freshness is ``stale_warning`` alone.
    * Degradation is one ``_meta.degraded`` list of reasons.
    """
    if not isinstance(result, dict):
        return result
    meta = result.get("_meta")
    if isinstance(meta, dict):
        if result.get("degraded") and meta.get("degraded") == result["degraded"]:
            del meta["degraded"]  # Already said at the top level.
        _lean_meta(meta)
    collapse_omission(result)
    _prune(result, top=True)
    return result


def collapse_omission(result: Any) -> Any:
    """One omission entry per response: ``_meta.omitted``, never a marker beside it.

    The response budget runs it on every MCP response, including the tools that
    keep their full ``_meta``. The marker restates the refs ``_meta.omitted``
    lists and names only the CLI route.
    """
    if isinstance(result, dict) and (result.get("_meta") or {}).get("omitted"):
        result.pop("omission_marker", None)
    return result


def drop_false_status_flags(result: Any) -> Any:
    """Remove ``false`` status flags from *result*'s ``_meta``, keeping the rest.

    For a full-meta tool: its diagnostics stay, but ``embedder_degraded: false``
    says nothing happened, as on every lean response. Telemetry reads the flag
    before this runs, so it still sees both values.
    """
    meta = result.get("_meta") if isinstance(result, dict) else None
    if isinstance(meta, dict):
        for key in [k for k, v in meta.items() if v is False and _is_status_flag(k)]:
            del meta[key]
    return result


def _lean_meta(meta: dict[str, Any]) -> None:
    for key in (*_DEBUG_FRESHNESS_KEYS, *_DEBUG_ACCOUNTING_KEYS, *_DEBUG_LAYER_KEYS, "embedder"):
        meta.pop(key, None)
    existing = meta.pop("degraded", None)
    reasons = [existing] if isinstance(existing, str) else list(existing or [])
    for key in sorted(k for k in meta if k.endswith("_degraded")):
        reasons.extend(_degraded_reasons(key.removesuffix("_degraded"), meta.pop(key)))
    if reasons:
        meta["degraded"] = reasons


def _degraded_reasons(stem: str, value: Any) -> list[str]:
    if isinstance(value, (list, dict)):
        return [f"{stem}:{leg}" for leg in value]
    if isinstance(value, str) and value:
        return [f"{stem}:{value}"]
    return [stem] if value else []


def _is_status_flag(key: str) -> bool:
    return any(key == flag or key.endswith(f"_{flag}") for flag in _STATUS_FLAGS)


def _prune(node: Any, *, top: bool = False) -> None:
    if isinstance(node, list):
        for item in node:
            _prune(item)
        return
    if not isinstance(node, dict):
        return
    _drop_cap_counters(node)
    for key in list(node):
        value = node[key]
        _prune(value)
        if _is_noise(node, key, value, top):
            del node[key]


def _is_noise(node: dict[str, Any], key: str, value: Any, top: bool) -> bool:
    if value is None or value == "" or (value is False and _is_status_flag(key)):
        return True
    # An empty top-level list is the tool's answer; a ``_basis`` explains one.
    return value in ([], {}) and not top and f"{key}_basis" not in node


def _drop_cap_counters(node: dict[str, Any]) -> None:
    """Keep ``<x>_total`` only where ``<x>`` was cut; drop the other counters."""
    counted = {k.removesuffix("_emitted") for k in node if k.endswith("_emitted")}
    counted.update(k.removesuffix("_total") for k in node if k.endswith("_total"))
    for key in [k for k in node if k.endswith("_total") and _uncut_total(node, k)]:
        del node[key]
    for key in [k for k in node if _counter_stem(node, k) in counted]:
        del node[key]


def _counter_stem(node: dict[str, Any], key: str) -> str | None:
    """The ``<x>`` a cap counter key describes; ``None`` for any other key."""
    suffix = next((s for s in _CAP_COUNTERS if key.endswith(s)), None)
    if suffix is None or (suffix == "_truncated" and not isinstance(node[key], bool)):
        return None
    return key.removesuffix(suffix)


def _uncut_total(node: dict[str, Any], key: str) -> bool:
    """A ``<x>_total`` that says nothing: ``<x>`` arrived whole, or not at all."""
    stem = key.removesuffix("_total")
    total, kept = node[key], node.get(stem)
    if not isinstance(total, int) or isinstance(total, bool):
        return False
    if isinstance(kept, (list, dict)):
        return len(kept) >= total
    # A list projected away whole: its counts describe nothing served.
    return f"{stem}_emitted" in node


def semantic_search_state() -> bool | None:
    """Whether retrieval has a real vector leg. ``None`` when never evaluated.

    Three-valued on purpose, and the third value is the point: a signal that only
    ever reports ``False`` cannot be told apart, by anything aggregating it, from
    one this version does not report at all. That is exactly the trap
    ``embedder_degraded`` was written to avoid, and it bites harder here, because
    the population this measures - keyless installs, where nothing is broken and
    retrieval is simply full-text-only - is the larger one.

    Kept beside :func:`_embedder_meta` and read by it, so the response and the
    telemetry can never disagree about the same install.
    """
    from repowise.server.mcp_server import _state

    status = getattr(_state, "_embedder_status", None)
    if not status:
        return None
    if status.get("degraded") or getattr(_state, "_vector_store_errors", None):
        return False
    return status.get("active") != "mock"


def _embedder_meta() -> dict[str, Any]:
    """Surface embedder degradation (issue #306) in the `_meta` envelope.

    When the configured embedder failed to initialise and the server silently
    fell back to mock vectors, every tool response carries ``embedder: "mock"``,
    ``embedder_degraded: True``, and a human-readable ``embedder_warning`` so an
    agent can detect — programmatically — that semantic search is broken instead
    of trusting empty/garbage retrieval. A healthy embedder still carries
    ``embedder_degraded: False`` and nothing else, so responses stay quiet while
    the field remains a two-valued signal: telemetry that only ever sees the
    ``true`` case cannot tell a 100% degradation rate from a write-only key.
    Absent entirely means the embedder was never initialised: not evaluated,
    not healthy.

    A *keyless* index is a different state and gets a different field. Nothing
    is broken and nothing was misconfigured, so it is not flagged as degraded;
    but retrieval really is full-text-only, and a caller that assumes semantic
    matching is running will misread a lexical miss as "not in the codebase".
    ``semantic_search: false`` says so once per response without crying wolf. The
    healthy case stays silent on the wire, because ``embedder_degraded: False``
    with no ``embedder`` key already says it and a second key on every response of
    every tool would be paid for in the caller's token budget to tell it something
    it can already see. Telemetry needs the third state named rather than
    inferred, so it reads :func:`semantic_search_state` directly instead.
    """
    # Lazy import: `_state` is a sibling module; importing it at call-time keeps
    # `_meta` free of any package import-ordering coupling.
    from repowise.server.mcp_server import _state

    status = getattr(_state, "_embedder_status", None)
    if not status:
        # Embedder never initialised, so there is nothing to report either way.
        # Absence means "not evaluated", distinct from an explicit ``false``.
        return {}
    store_errors = getattr(_state, "_vector_store_errors", None) or {}
    if not status.get("degraded") and not store_errors:
        if status.get("active") == "mock":
            return {"embedder": "mock", "embedder_degraded": False, "semantic_search": False}
        return {"embedder_degraded": False}
    out: dict[str, Any] = {
        "embedder": status.get("active", "mock"),
        "embedder_degraded": True,
        "semantic_search": False,
    }
    reason = status.get("reason") or " ".join(store_errors.values())
    if reason:
        out["embedder_warning"] = reason
    return out


def _release_meta() -> dict[str, Any]:
    """Name a newer repowise release once per process, in the first response
    after the lifespan's poller sees it.

    The stdio server never re-checks its own currency otherwise, and a client
    that spawned it weeks ago keeps talking to that version until something
    says so. One key, one time: repeating it on every call would charge the
    caller's token budget for a fact it already has. A newer version seen
    later in the same process is announced again, once.
    """
    from repowise.server.mcp_server import _state

    check = getattr(_state, "_release_check", None)
    if check is None or not check.update_available or not check.latest_version:
        return {}
    if check.latest_version == _state._release_announced:
        return {}
    _state._release_announced = check.latest_version
    return {
        "newer_release": (
            f"repowise {check.latest_version} is available, this server runs "
            f"{check.current_version}; upgrade and restart the MCP server"
        )
    }


#: For a keyless server over an index whose prose a model already wrote.
_KEY_ONLY_NOTE = (
    "no model is configured, so answers are not synthesized; set a provider "
    "key (or a provider in .repowise/config.yaml) and restart the MCP server"
)


def prose_upgrade_meta(local_path: str | None) -> dict[str, Any]:
    """``keyless_note`` naming the upgrade command, on an index no model wrote prose for."""
    line = answers_upgrade_line(read_index_scope(local_path))
    return {"keyless_note": line} if line else {}


def keyless_note_meta(local_path: str | None) -> dict[str, Any]:
    """What a key would add, on every ``get_answer`` that degrades with
    ``no-llm-provider`` (#3105).

    Not once per process: an agent that starts a conversation on a server
    another conversation already used would never learn how to upgrade.
    """
    return prose_upgrade_meta(local_path) or {"keyless_note": _KEY_ONLY_NOTE}


def symbol_hint(symbol_id: str, end_line: int, start_line: int) -> str | None:
    """Hint for source retrieval (kept for backward compat with tool_symbol.py)."""
    return None


# One wording for each of the two ways a response can be a dead end, so a
# spelling cannot drift in on its own.
#
# The rule they encode: a dead end redirects into another call of OUR surface,
# naming the tool and the argument to use. An external tool is named only for a
# job we do not do, which is EXHAUSTIVE_SWEEP_HINT and nothing else.
NO_HITS_RECOVERY_HINT = (
    'Retry search_codebase with mode="symbol" for an identifier or mode="path" '
    "for a file name; if the question names a file, call get_context on it "
    "directly."
)

EXHAUSTIVE_SWEEP_HINT = (
    "For an exhaustive sweep of every literal usage — before a rename, say — "
    "Grep the name; that is the one job this surface does not do."
)

# Appended to a get_answer hint when the answer graded low and the index is
# behind live HEAD; the one place holding both signals says what to do.
INDEX_BEHIND_LOW_CONFIDENCE_HINT = (
    "The index is behind HEAD, so run `repowise update` and ask again before "
    "trusting a low-confidence answer."
)


def completeness_line(*, bodies: int = 0, files: int = 0) -> str | None:
    """One sentence naming the whole units this response already served.

    Only ever counts complete units. A sliced body or a partial range is not a
    unit, so the callers filter before they count and this returns ``None`` when
    nothing whole was served.
    """
    bodies = max(0, int(bodies))
    files = max(0, int(files))
    if not bodies and not files:
        return None
    parts: list[str] = []
    if bodies:
        noun = "symbol body" if bodies == 1 else "symbol bodies"
        parts.append(f"{bodies} {noun} served whole from live source")
    if files:
        noun = "file" if files == 1 else "files"
        parts.append(f"{files} {noun} served whole")
    closing = "do not re-open it." if bodies + files == 1 else "do not re-open them."
    return f"Complete: {' and '.join(parts)}; {closing}"


def answer_hint(
    confidence: str,
    *,
    degraded: str | None = None,
    retrieval_quality: str | None = None,
    has_bodies: bool = False,
) -> str | None:
    """Hint for `get_answer` callers.

    Encourages verification when confidence is low; never tells the agent to
    "trust the answer" — that's the over-trust failure mode.

    Deliberately takes no retrieval count. An empty ``retrieval`` block is a
    confidence-conditional VIEW, not a measure of what retrieval found — a
    high-confidence answer sets it to ``[]`` on purpose — so keying "no hits"
    off its length would tell the most trustworthy answers on the surface that
    there were none. The one site that genuinely knows retrieval came back empty
    writes its own note there.

    A degraded payload is keyed separately, because "low" means something
    different there. Everywhere else it rates an answer that exists and might be
    wrong, so "go verify" is the right push. On a degraded payload there is no
    synthesised text at all: what is missing is the prose, not the evidence, and
    the evidence beside it can be excellent. Telling an install with no LLM to
    doubt its files on every question is what made a keyless agent call
    search_codebase after every get_answer: one question, two calls, measured on
    11 of 26 questions whose rank-1 file was right. So say which half is missing
    and let `confidence` rate the other half.
    """
    if degraded:
        if retrieval_quality == "weak":
            return (
                "No synthesis, and retrieval was weak. Refine the query with "
                "search_codebase rather than reading these files in order."
            )
        # Name symbol_bodies only when there is one. A hint that points at a key
        # the payload does not carry is the same misdirection this branch exists
        # to remove, and strong retrieval without an anchored body is an ordinary
        # outcome here (a prose question naming no identifier).
        if has_bodies:
            return (
                "Synthesis is what is missing here, not retrieval. Answer from "
                "symbol_bodies."
            )
        return (
            "Synthesis is what is missing here, not retrieval. Start from the "
            "first ranked hit."
        )
    if confidence == "low":
        return (
            "Low confidence. Read the top evidence row or candidate_files to "
            "verify before answering."
        )
    return None
