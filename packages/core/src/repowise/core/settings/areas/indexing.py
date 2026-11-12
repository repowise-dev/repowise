"""What gets indexed and how: files, git history, parsing, workers."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import FALSY, Setting

AREA = "indexing"
_s = factory(AREA)
_e = factory(AREA, scope="env")

SETTINGS: tuple[Setting, ...] = (
    _s(
        "exclude_patterns",
        "list",
        [],
        "Extra paths to skip, as gitignore patterns.",
        writer="repowise.core.repo_config:write_file_setting",
    ),
    _s(
        "commit_limit",
        "int",
        5000,
        "How many of the newest commits are analysed.",
        minimum=1,
        parser="repowise.core.ingestion.git_indexer._constants:parse_commit_limit",
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
