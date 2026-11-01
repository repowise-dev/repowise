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
