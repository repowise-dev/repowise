"""Short, correctly cased labels for the wiki's navigation.

Page titles are written in several places — the concept namer, the
deterministic fallback, the chapter rule — and each humanises directory names
on its own, so a title could read "Cli Overview" in the sidebar while the page
it opens says "CLI Overview". This module is the one casing table every title
passes through last, and the one rule that turns a stored title into the label
a navigation row shows.

The label rule runs at read time as well as at generation time, so a wiki
written before it shipped reads "CLI" rather than "Cli Overview" without a
single page being regenerated.
"""

from __future__ import annotations

import json
import re

# Lower-cased word -> how it is spelled. Words whose standard spelling is not
# Title Case: acronyms, and names with an internal capital. One table for every
# title the wiki writes; extend it here rather than spelling a word elsewhere.
ACRONYMS: dict[str, str] = {
    "ai": "AI",
    "api": "API",
    "apis": "APIs",
    "ast": "AST",
    "aws": "AWS",
    "c4": "C4",
    "cd": "CD",
    "ci": "CI",
    "cli": "CLI",
    "cpu": "CPU",
    "css": "CSS",
    "csv": "CSV",
    "db": "DB",
    "devcontainer": "Dev Container",
    "dns": "DNS",
    "fts": "FTS",
    "gcp": "GCP",
    "github": "GitHub",
    "gitlab": "GitLab",
    "gpu": "GPU",
    "graphql": "GraphQL",
    "grpc": "gRPC",
    "html": "HTML",
    "http": "HTTP",
    "https": "HTTPS",
    "id": "ID",
    "ids": "IDs",
    "io": "IO",
    "ip": "IP",
    "js": "JS",
    "json": "JSON",
    "jsx": "JSX",
    "jwt": "JWT",
    "k8s": "K8s",
    "llm": "LLM",
    "llms": "LLMs",
    "lsp": "LSP",
    "mcp": "MCP",
    "npm": "npm",
    "oauth": "OAuth",
    "openapi": "OpenAPI",
    "orm": "ORM",
    "pr": "PR",
    "prs": "PRs",
    "pypi": "PyPI",
    "rpc": "RPC",
    "scc": "SCC",
    "scss": "SCSS",
    "sdk": "SDK",
    "sql": "SQL",
    "sqlite": "SQLite",
    "ssh": "SSH",
    "ssr": "SSR",
    "svg": "SVG",
    "tls": "TLS",
    "toml": "TOML",
    "ts": "TS",
    "tsx": "TSX",
    "ui": "UI",
    "uri": "URI",
    "url": "URL",
    "urls": "URLs",
    "ux": "UX",
    "vscode": "VS Code",
    "wasm": "WASM",
    "xml": "XML",
    "yaml": "YAML",
    "yml": "YAML",
}

# A word on its own: not part of a path, a dotted name or a snake_case
# identifier, which are code and keep the spelling the code gives them.
_WORD = re.compile(r"(?<![\w/.\-`])([A-Za-z][A-Za-z0-9]*)(?![\w/.\-`])")


# Entries that are also ordinary English words. Written in lower case they are
# the word ("the rest of", "cd into"), so only a capitalised one is respelled.
_ALSO_WORDS = frozenset({"ai", "cd", "ci", "id", "ids", "io", "ip", "js", "pr", "ts"})


def _respell(match: re.Match[str]) -> str:
    word = match.group(1)
    key = word.lower()
    if key in _ALSO_WORDS and word.islower():
        return word
    return ACRONYMS.get(key, word)


def recase(text: str) -> str:
    """*text* with every word in :data:`ACRONYMS` spelled the standard way."""
    return _WORD.sub(_respell, text)


# Words that name the page's kind rather than its subject. Every chapter used
# to end in "Overview", so fifteen sibling rows repeated one word that carried
# nothing; "Components" padded a one-word fallback name to two words.
_KIND_SUFFIXES = (" Overview", " Components")
# Words that name no subject when they open a two-word label: the default
# layer name ("Application Flask") and a generic tier ("Service Alembic") that
# older wikis prefixed to a one-word directory name. "Service Worker" is a
# subject, so a longer or model-written label keeps them only by being one.
_EMPTY_PREFIXES = ("Application ", "Service ")
# A disambiguator written as a path, "UI Health (packages/ui/src/health)".
# Second words that make "Service X" / "Application X" a real subject.
_KEPT_AFTER_PREFIX = frozenset({"Worker", "Workers", "Mesh", "Layer", "Layers", "State", "Shell"})
_PATH_PARENS = re.compile(r"\s*\([^()]*/[^()]*\)\s*$")
# What the repository overview's title opens with, ahead of the repo's name.
_OVERVIEW_TITLE = re.compile(r"^(Repository|Project) Overview\b.*$")


def nav_label(title: str, page_type: str = "module_page") -> str:
    """The label a navigation row shows for a page stored as *title*.

    Rule based and free: casing from :data:`ACRONYMS`, no trailing kind word,
    no path in parentheses, no prefix that names no subject. A label the rule
    would empty keeps the cased title, so no row ever reads blank.
    """
    cased = recase(title.strip())
    if page_type == "repo_overview" and _OVERVIEW_TITLE.match(cased):
        return "Overview"
    if page_type != "module_page":
        return cased
    if cased == "Repository Overview":
        # The chapter heading a repository's root files.
        return "Repository Root"
    return _strip_kind_words(_PATH_PARENS.sub("", cased)).strip() or cased


def _strip_kind_words(label: str) -> str:
    """*label* without a trailing kind word or an opening word with no subject."""
    for suffix in _KIND_SUFFIXES:
        if label.endswith(suffix) and len(label) > len(suffix):
            label = label[: -len(suffix)]
    for prefix in _EMPTY_PREFIXES:
        rest = label[len(prefix) :]
        if label.startswith(prefix) and len(rest.split()) == 1 and rest not in _KEPT_AFTER_PREFIX:
            label = rest
    return label


# ---------------------------------------------------------------------------
# The short name a module page's own generation writes for itself
# ---------------------------------------------------------------------------

#: Metadata key holding the name the page's own generation chose.
NAV_TITLE_KEY = "nav_title"
# Tolerates the decoration a model adds to a label: "**Nav:** X", "Nav : X".
_NAV_LINE = re.compile(r"\A\s*\**Nav\**[ \t]*:\**[ \t]*(?P<name>[^\n]*)\n+")
_MAX_NAV_WORDS = 4
_MAX_NAV_CHARS = 40
_NAV_BANNED = re.compile(r"\b(overview|module|components)\b", re.IGNORECASE)


def split_nav_title(content: str) -> tuple[str | None, str]:
    """``(name, body)``: the ``Nav:`` line a module page opens with, and the rest.

    The line is the page's own pick of a sidebar name, written in the same
    call as the page. It is checked here rather than retried: a name that
    breaks a rule is dropped and the row falls back to :func:`nav_label`.
    A reply with no such line comes back unchanged.
    """
    match = _NAV_LINE.match(content or "")
    if not match:
        return None, content
    return clean_nav_title(match.group("name")), content[match.end() :]


def clean_nav_title(raw: object) -> str | None:
    """*raw* as a sidebar name, or ``None`` when it breaks a rule.

    One to four words, no path or code, no word naming the page's kind.
    """
    if not isinstance(raw, str) or "`" in raw:
        return None
    name = raw.strip().strip("*_`\"'").strip().rstrip(".")
    words = name.split()
    if not words or len(words) > _MAX_NAV_WORDS or len(name) > _MAX_NAV_CHARS:
        return None
    if any(ch in name for ch in "/\\_()[]{}<>#|:") or _NAV_BANNED.search(name):
        return None
    return recase(name)


def page_nav_title(page_type: str, title: str, metadata: object = None) -> str:
    """What a navigation row calls a page: its own chosen name, else the rule.

    *metadata* is the page's metadata dict, or its raw JSON text (a listing
    that never decodes the blob passes the text; it is only decoded when it
    mentions the key).
    """
    chosen = None
    if isinstance(metadata, str) and NAV_TITLE_KEY in metadata:
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = None
    if isinstance(metadata, dict):
        chosen = clean_nav_title(metadata.get(NAV_TITLE_KEY))
    return chosen or nav_label(title, page_type)


# ---------------------------------------------------------------------------
# A label in its place: under its parent, in a named product
# ---------------------------------------------------------------------------

# Words that name where code sits rather than what it does. They never open a
# label and never stand alone: "Web Source" under Web is Web again.
_PATH_WORDS = frozenset(
    {"src", "source", "app", "apps", "lib", "libs", "library", "package", "packages", "pkg"}
)
# A hosting directory named after the label's subject: ".github" under "CI".
_HOST_WORDS = frozenset({"github", "gitlab"})
# What is left of a glued name ("UI and Stats") once its first half is cut.
_JOINERS = frozenset({"and", "or", "of", "for", "&"})


def _drop_lead(words: list[str], lead: list[str]) -> list[str]:
    """*words* without *lead* at their start, compared case-insensitively."""
    n = len(lead)
    if n and [w.lower() for w in words[:n]] == lead:
        return words[n:]
    return words


def contextual_label(label: str, parent_label: str = "", product: str = "") -> str:
    """*label* as a row under *parent_label* in a repository named *product*.

    A row never repeats what its parent row already says ("UI Zoom" under UI
    reads "Zoom", "API Client Types" under API Client reads "Types"), never
    opens with the product's own name ("Repowise Server" reads "Server") and
    never opens with or is only a path word ("Source", "Lib"). A label left
    with nothing of its own comes back as *parent_label*, which a tree reads
    as the parent's own page and folds into the parent's row. A top-level
    label with nothing left keeps its words.
    """
    words = label.split()
    parent = parent_label.lower().split()
    brand = product.lower().replace("-", " ").replace("_", " ").split()
    changed = True
    while changed and words:
        before = words
        words = _drop_lead(_drop_lead(words, brand), parent)
        while words and (words[0].lower() in _PATH_WORDS or words[0].lower() in _JOINERS):
            words = words[1:]
        if len(words) > 1 and words[-1].lower() in _HOST_WORDS:
            words = words[:-1]
        words = _strip_kind_words(" ".join(words)).split()
        changed = words != before
    if not words:
        return parent_label or label
    return " ".join(words)


def apply_outline_labels(rows: list, product: str = "") -> None:
    """Set each outline row's ``nav_title`` in the context of its parent row.

    *rows* carry ``id``, ``parent_page_id`` and ``nav_title`` (``None`` for a
    reference page, which is left alone). A parent absent from *rows* (a
    paginated listing) reads as no parent. Parents are labelled before their
    children, so "Repowise Core" under "Core" compares with the parent's final
    label, not its stored one.
    """
    by_id = {row.id: row for row in rows}
    done: dict[str, str] = {}

    def label(row, seen: frozenset[str]) -> str:
        if row.id in done:
            return done[row.id]
        parent = by_id.get(row.parent_page_id or "")
        parent_label = ""
        if parent is not None and parent.nav_title and parent.id not in seen:
            parent_label = label(parent, seen | {row.id})
        done[row.id] = contextual_label(row.nav_title, parent_label, product)
        return done[row.id]

    for row in rows:
        if row.nav_title:
            label(row, frozenset())
    for row in rows:
        if row.id in done:
            row.nav_title = done[row.id]
