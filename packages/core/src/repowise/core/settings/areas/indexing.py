"""What gets indexed and how: files, git history, parsing, workers."""

from __future__ import annotations

import shlex
from typing import Any

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import FALSY, Proposal, Setting

AREA = "indexing"
_s = factory(AREA)
_e = factory(AREA, scope="env")

#: A deeper window is suggested once at least this many code files, and this
#: share of them, have no commit in it. 100 is the git indexer's own bar for a
#: second history walk being worth it (``_DEEP_WALK_MIN_FALLBACK``).
MIN_FILES_OUTSIDE_WINDOW = 100
MIN_SHARE_OUTSIDE_WINDOW = 0.25
#: The deepest window suggested: the full-history row of the large-monorepo
#: guide's measurement (git/git, 61,453 commits, a few seconds and ~100 MB).
#: Ceiling: past it the cost is unmeasured, so a deeper history gets this much.
MAX_SUGGESTED_COMMIT_LIMIT = 100_000
_COMMIT_LIMIT_STEP = 5_000
#: Excluding is suggested once wholly copied directories hold this share of
#: the analysed files.
MIN_COPIED_SHARE = 0.25
_PATTERN_CHARS = frozenset(r"*?[]!\,")


def _full_window(facts: Any) -> int | None:
    """The commit limit, when the stored window was read with it, is full and history goes on."""
    limit = facts.values.get("commit_limit")
    window = facts.git_history
    # Another stored window means the next update already moves it.
    if not isinstance(limit, int) or window.get("per_file_limit") != limit:
        return None
    if (window.get("global_commits") or 0) < limit or facts.total_commits <= limit:
        return None
    return limit


def _suggest_commit_limit(facts: Any) -> Proposal | None:
    """A window covering the history, when the full window leaves many files out of it."""
    limit = _full_window(facts)
    if limit is None:
        return None
    total = facts.total_commits
    files = facts.git_history.get("eligible_files") or 0
    outside = files - (facts.git_history.get("recent_files") or 0)
    if outside < MIN_FILES_OUTSIDE_WINDOW or outside < files * MIN_SHARE_OUTSIDE_WINDOW:
        return None
    step = _COMMIT_LIMIT_STEP
    value = min(-(-total // step) * step, MAX_SUGGESTED_COMMIT_LIMIT)
    if value <= limit:
        return None
    covers = "all" if value >= total else f"the newest {value:,} of the"
    return Proposal(
        value,
        f"Git history here is read through the newest {limit:,} of {total:,} commits, "
        f"and {outside:,} of {files:,} code files have no commit among them, so co-change "
        "and the commit list say nothing about those files.",
        f"Co-change, change history and the commit list cover {covers} {total:,} commits. "
        "The next repowise update re-reads git history; on git/git reading all 61,453 "
        "commits instead of 5,000 added 3 s and about 100 MB to the index.",
        "quality",
    )


def _suggest_exclude(facts: Any) -> Proposal | None:
    """Exclude the directories that hold only vendored or generated code, when they dominate."""
    current = list(facts.values.get("exclude_patterns") or [])
    kept = {str(p).strip("/") for p in current}
    # A name that a pattern would read as a glob, or a comma that the list
    # parser would split on, is left for the user to spell.
    dirs = [
        (d, n)
        for d, n in facts.copied_dirs
        if d not in kept and not any(c in d for c in _PATTERN_CHARS)
    ]
    copied = sum(n for _, n in dirs)
    analysed = facts.analysed_files
    if not dirs or copied < analysed * MIN_COPIED_SHARE:
        return None
    # Anchored: ``vendor/`` alone would also match a first-party ``src/vendor/``.
    patterns = [f"/{d}/" for d, _ in dirs]
    named = ", ".join(f"{d}/ ({n:,})" for d, n in dirs[:3])
    more = f" and {len(dirs) - 3} more" if len(dirs) > 3 else ""
    value = [*current, *patterns]
    return Proposal(
        value,
        f"{copied:,} of the {analysed:,} files analysed here are vendored or generated "
        f"code in {named}{more}.",
        "The next repowise update leaves them out: a smaller, faster index, and search "
        "and answers stop surfacing code nobody here edits. Imports into them read as "
        "external libraries.",
        "quality",
        command="repowise config set exclude_patterns "
        + shlex.quote(",".join(str(p) for p in value)),
    )


SETTINGS: tuple[Setting, ...] = (
    _s(
        "exclude_patterns",
        "list",
        [],
        "Extra paths to skip, as gitignore patterns.",
        writer="repowise.core.repo_config:write_file_setting",
        suggest_when=_suggest_exclude,
    ),
    _s(
        "commit_limit",
        "int",
        5000,
        "How many of the newest commits are analysed.",
        minimum=1,
        parser="repowise.core.ingestion.git_indexer._constants:parse_commit_limit",
        suggest_when=_suggest_commit_limit,
    ),
    _s("follow_renames", "bool", False, "Follow files across renames in git history."),
    _s(
        "sql_dialect",
        "str",
        None,
        "SQL dialect for parsing .sql files (a sqlglot name such as postgres).",
    ),
    _s(
        "forges.hosts",
        "mapping",
        {},
        "Which forge a self-hosted git host is: host to github, gitlab, azure, "
        "bitbucket or generic.",
    ),
    _e(
        "REPOWISE_FORGE_HOSTS",
        "str",
        "",
        "Comma-separated host=forge pairs, added to forges.hosts.",
    ),
    _e(
        "REPOWISE_PARSE_WORKERS",
        "int",
        None,
        "Parallel parse workers for init and update. Unset sizes to the machine.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _e(
        "REPOWISE_PARSE_TIMEOUT_S",
        "float",
        60.0,
        "Seconds one file may take to parse; 0 turns the limit off.",
        minimum=0,
    ),
    _e(
        "REPOWISE_PARSE_WORKER_MEMORY_MB",
        "float",
        2048.0,
        "Memory cap per parse worker; 0 turns it off.",
        minimum=0,
        internal=True,
    ),
    _e(
        "REPOWISE_GIT_WORKERS",
        "int",
        None,
        "Parallel git processes while indexing history. Unset sizes to the machine.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _e(
        "REPOWISE_GIT_INDEX_TIMEOUT_S",
        "float",
        120.0,
        "Seconds one file's git history lookup may take.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _e(
        "REPOWISE_GIT_SCOPE",
        "str",
        "module",
        "Set to repo to index the whole repository's history when indexing one module.",
    ),
    _e(
        "REPOWISE_GIT_WINDOW_ANCHOR",
        "str",
        None,
        "Set to now to measure git windows (recent churn, prior defects, blame age) "
        "from today instead of from the indexed commit's date.",
    ),
    _e(
        "REPOWISE_GRAMMAR_DIR",
        "str",
        None,
        "Folder of pre-downloaded language grammars, for offline machines.",
    ),
    _e(
        "REPOWISE_WORKTREE_FALLBACK",
        "bool",
        True,
        "Let a git worktree read its main checkout's index; 0 turns it off.",
        env_false=FALSY,
    ),
    _e(
        "REPOWISE_INCREMENTAL_GRAPH",
        "bool",
        True,
        "Patch the code graph on update instead of rebuilding it; 0 forces a rebuild.",
        env_false=frozenset({"0"}),
        internal=True,
    ),
    _e(
        "REPOWISE_INDEX_INFORMATION_FLOOR",
        "int",
        0,
        "Least characters of real prose a wiki page needs to be found by semantic "
        "search. Shorter pages stay readable, just not in that search. 0 is off.",
        minimum=0,
    ),
    _e(
        "REPOWISE_KG_CURATION",
        "bool",
        True,
        "Tidy the knowledge-graph layers; 0 shows the raw export.",
        env_false=FALSY,
        internal=True,
    ),
    _e(
        "REPOWISE_WORKSPACE_CEILING",
        "str",
        None,
        "Folder above which the workspace search stops walking.",
        internal=True,
    ),
    _e(
        "REPOWISE_CACHE_HMAC_KEY",
        "str",
        None,
        "Fixed hex key that signs the local cache, for CI.",
        internal=True,
        secret=True,
    ),
    _e(
        "REPOWISE_CACHE_KEY_PATH",
        "str",
        None,
        "Where the machine's cache signing key lives.",
        internal=True,
    ),
)
