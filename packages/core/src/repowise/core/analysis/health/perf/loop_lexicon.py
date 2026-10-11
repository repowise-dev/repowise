"""Names and query text that say what a loop walks, shared by every dialect.

A dialect finds the iterable; these answer whether its name, ORM model or SQL
text names a registry or configuration table (what is walked, not how many:
such tables grow with use), the rows of one primary key, a filter that keeps
only a rare state of a growing table, a read capped in its SQL, or a
repository root a listing walks. Each answers ``False`` when the text does
not settle it.
"""

from __future__ import annotations

import re
from itertools import pairwise

# A collection a user registers or configures, as the last word of its name.
_CONFIG_NOUNS = frozenset(
    {
        "repos",
        "repositories",
        "workspaces",
        "providers",
        "plugins",
        "settings",
        "registry",
        "registries",
    }
)
# A function that returns repos or workspaces may be scanning a disk for them
# (``discover_repos(root)``); only these nouns name configuration as a callee.
_CALLEE_NOUNS = _CONFIG_NOUNS - {"repos", "repositories", "workspaces"}
# Containers whose members are configuration wherever they sit in a path
# (``ws_config.repos``, ``settings.get("exclude")``), unless what is listed is
# files. A bare ``cfg`` is as often a control-flow graph, so only ``x_cfg`` counts.
_CONFIG_CONTAINERS = frozenset({"config", "settings", "registry"})
_CFG_SUFFIX = re.compile(r"[a-z0-9](?:_cfg|Cfg)\b")
# A scan of text yields matches or lines of it, whatever the receiver is named.
_TEXT_SCANS = frozenset({"finditer", "findall", "split", "rsplit", "splitlines"})
# ``provider_dirs`` / ``repo_paths``: one config noun, then what is listed of it.
_CONFIG_SINGULARS = frozenset({"repo", "repository", "workspace", "provider", "plugin"})
_CALLEE_SINGULARS = _CONFIG_SINGULARS - {"repo", "repository", "workspace"}
_CONFIG_LISTED = frozenset({"ids", "names", "paths", "dirs", "roots", "keys", "entries"})
# ``page_types`` spans two words.
_CONFIG_PAIRS = frozenset({("page", "types")})

_CONFIG_MODELS = frozenset(
    {
        "Repository",
        "Repo",
        "Workspace",
        "WorkspaceRepo",
        "WorkspaceRepository",
        "Provider",
        "Plugin",
        "Setting",
        "Settings",
        "Config",
        "Configuration",
        "PageType",
        "Registry",
    }
)
_CONFIG_TABLES = frozenset(
    {
        "repositories",
        "repos",
        "workspaces",
        "workspace_repos",
        "providers",
        "plugins",
        "settings",
        "config",
        "configs",
        "page_types",
    }
)

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_SQL_FROM = re.compile(rb"\b(?:FROM|JOIN)\s+[\"`\[]?([A-Za-z_][\w.]*)", re.IGNORECASE)
# Several tables or a recursive walk: no longer the rows of one key.
_SQL_WIDENS = re.compile(rb"\b(?:JOIN|UNION|RECURSIVE|IN)\b|\bin_\(|\.join\(", re.IGNORECASE)
# ``WHERE id = ?`` / ``t.id = :id`` / ``Model.id == key``; ``parent_id = ?`` is not.
_KEY_EQUALITY = re.compile(
    rb"(?<![\w.])(?:\w+\.)?id\s*(?:=\s*(?:\?|:\w+|%s|%\(\w+\)s|\$\d+)|==\s*(?!=)\w)"
)
# A time threshold on an expiry-like column, or a rare-state literal.
_RARE_FILTER = re.compile(
    rb"\w*(?:expire|expiry|deadline|heartbeat|lease|ttl|timeout)\w*\s*<"
    rb"|[\"'](?:stale|expired|failed|crashed|orphan|orphaned|dead|zombie|timed_out"
    rb"|abandoned|stuck)[\"']",
    re.IGNORECASE,
)
_ROOT_OWNERS = frozenset({"repo", "repository", "project", "workspace"})
_ROOT_WORDS = frozenset({"root", "path", "dir"})


def words(name: str) -> list[str]:
    """Lower-cased words of a name: ``ws_config.repos`` -> ws, config, repos."""
    return [w.lower() for part in re.split(r"[^A-Za-z0-9]+", name) for w in _WORD.findall(part)]


def is_config_name(name: str, *, callee: bool = False) -> bool:
    """A name (``ws_config.repos``, ``_iter_provider_dirs``) for a configured collection.

    *callee*: the name is a function the loop calls for its collection.
    """
    ws = words(name)
    if not ws or ws[-1] in _TEXT_SCANS:
        return False
    if ws[-1] in (_CALLEE_NOUNS if callee else _CONFIG_NOUNS):
        return True
    if ws[-1] != "files" and (_CONFIG_CONTAINERS.intersection(ws) or _CFG_SUFFIX.search(name)):
        return True
    if len(ws) >= 2 and (ws[-2], ws[-1]) in _CONFIG_PAIRS:
        return True
    singulars = _CALLEE_SINGULARS if callee else _CONFIG_SINGULARS
    return len(ws) >= 2 and ws[-1] in _CONFIG_LISTED and ws[-2] in singulars


def is_config_model(name: str) -> bool:
    """An ORM model (last dotted segment) that is a registry or configuration table."""
    return name.rsplit(".", 1)[-1] in _CONFIG_MODELS


def sql_tables(sql: bytes) -> list[str]:
    """The tables a SQL text reads, lower-cased, schema dropped."""
    return [m.group(1).decode().rsplit(".", 1)[-1].lower() for m in _SQL_FROM.finditer(sql)]


def is_config_sql(sql: bytes) -> bool:
    tables = sql_tables(sql)
    return bool(tables) and all(t in _CONFIG_TABLES for t in tables)


def is_key_lookup(text: bytes) -> bool:
    """The read filters on equality with one primary key and nothing widens it."""
    return bool(_KEY_EQUALITY.search(text)) and not _SQL_WIDENS.search(text)


def is_rare_filter(text: bytes) -> bool:
    return bool(_RARE_FILTER.search(text))


# ``authority_id = ?`` / ``where(user_id: u.id)`` / ``findAllByOwnerId(id)``: the
# rows that belong to one parent row.
_PARENT_KEY = re.compile(
    rb"\b[a-z]\w*_id\b(?:\s*=\s*(?:\?|:\w+|@\w+|\$\d+)|:\s*[^\s:])"
    rb"|\b(?:find|get|query|read|stream|count)\w*By\w+Id\s*\(",
    re.IGNORECASE,
)


_RECURSIVE = re.compile(rb"\bRECURSIVE\b", re.IGNORECASE)
_UNION = re.compile(rb"\bUNION\b", re.IGNORECASE)


def is_parent_key_filter(text: bytes) -> bool:
    """The read filters on equality with one foreign key, or walks the tree
    below one row: a ``WITH RECURSIVE`` whose seed (the part before the first
    ``UNION``) keys on one id. A key only in the recursive member says nothing
    about how many roots the seed starts from."""
    recursive = _RECURSIVE.search(text)
    if recursive is not None:
        seed = _UNION.split(text[recursive.end() :], maxsplit=1)[0]
        if _KEY_EQUALITY.search(seed):
            return True
    return bool(_PARENT_KEY.search(text))


_SQL_LIMIT = re.compile(
    rb"\bselect\b.*?(?:\blimit\s+(?:\d+\b|\?|\$\d|:\w)|\btop\s*\(?\s*\d+|\bfetch\s+first\s+\d+)",
    re.IGNORECASE | re.DOTALL,
)


def has_sql_limit(text: bytes) -> bool:
    """A SELECT capped at a row count (``LIMIT 10``, ``LIMIT ?``, ``TOP 5``)."""
    return bool(_SQL_LIMIT.search(text))


def names_repo_root(name: str) -> bool:
    """``repo_root`` / ``self._repo_path`` / ``projectDir``: the tree a repository is."""
    ws = words(name)
    return any(a in _ROOT_OWNERS and b in _ROOT_WORDS for a, b in pairwise(ws))


__all__ = [
    "has_sql_limit",
    "is_config_model",
    "is_config_name",
    "is_config_sql",
    "is_key_lookup",
    "is_parent_key_filter",
    "is_rare_filter",
    "names_repo_root",
    "sql_tables",
    "words",
]
