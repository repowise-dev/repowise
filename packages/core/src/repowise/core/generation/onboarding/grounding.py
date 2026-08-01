"""Post-generation grounding check for onboarding pages.

Onboarding prose is grounded in its structured context and, when configured,
explicit repository evidence. A fabricated citation - a file neither input
mentioned, or a symbol neither input establishes - must not reach the reader
as an authoritative backticked reference.

This module closes that gap deterministically. It collects the paths and
symbols actually present in a subkind's context object, then scans the
generated markdown for backticked tokens that *look* like a file path or a
code symbol. A token that is clearly one of those shapes but is absent from
the context is "ungrounded": its backticks are stripped so it is no longer
presented as a verified reference, and it is reported for logging.

Design choices, all in the safe direction (never mangle a good page):
  - Only backticked spans are examined; prose is untouched.
  - A token is checked only when its shape is unambiguous - a path with a
    source-code extension, or an identifier that is CamelCase / snake_case /
    dotted / ``::``-qualified. Lowercase single words (enum values like
    ``full``) are left alone.
  - "Grounded" matching is generous (suffix / basename for paths, membership
    for symbols) so legitimate abbreviations survive.
  - Ungrounded tokens are demoted to plain text, not deleted, so sentences
    stay intact.

Run this on the content returned by the provider whether it was freshly
generated or reused from a prior run, so an existing user's cached page is
cleaned on their next docs update even when the prompt is unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

# Backticked span: `...` with no backtick or newline inside.
_BACKTICK = re.compile(r"`([^`\n]+)`")

# Source-code file extensions. A backticked token ending in one of these
# (optionally with a member suffix) is treated as a path citation.
_CODE_EXTENSIONS = frozenset(
    {
        "py",
        "pyi",
        "ts",
        "tsx",
        "js",
        "jsx",
        "mjs",
        "cjs",
        "go",
        "rs",
        "java",
        "kt",
        "kts",
        "scala",
        "rb",
        "php",
        "proto",
        "cs",
        "cpp",
        "cc",
        "cxx",
        "c",
        "h",
        "hpp",
        "swift",
        "dart",
        "sql",
        "sh",
        "lua",
        "ex",
        "exs",
        "clj",
        "vue",
        "svelte",
        "m",
        "mm",
    }
)
_DOCUMENT_EXTENSIONS = frozenset(
    {
        "adoc",
        "asciidoc",
        "cfg",
        "conf",
        "ini",
        "json",
        "md",
        "mdx",
        "rst",
        "toml",
        "txt",
        "yaml",
        "yml",
    }
)
_EXTENSIONLESS_PATH_NAMES = frozenset(
    {
        "containerfile",
        "copying",
        "dockerfile",
        "gemfile",
        "justfile",
        "license",
        "makefile",
        "notice",
        "procfile",
        "rakefile",
        "readme",
    }
)
_REPOSITORY_DIRECTORY_NAMES = frozenset(
    {
        ".github",
        "app",
        "apps",
        "config",
        "deploy",
        "docs",
        "examples",
        "include",
        "lib",
        "packages",
        "scripts",
        "src",
        "test",
        "tests",
        "tools",
    }
)
# A bare identifier, optionally dotted or ``::``-qualified (e.g. ``LanguageSpec``,
# ``get_session``, ``foo.Bar.baz``, ``path.py::Name``).
_QUALIFIER = r"(?:\.|::|#|/|:)"
_IDENT = re.compile(
    rf"^[A-Za-z_][A-Za-z0-9_]*(?:{_QUALIFIER}[A-Za-z_][A-Za-z0-9_]*)+$"
    r"|^[A-Za-z_][A-Za-z0-9_]*$"
)


def _looks_like_path(token: str) -> bool:
    """True when *token* is shaped like a source file path we can verify."""
    head = token.split("::", 1)[0]
    head = head.split("#", 1)[0].strip()
    name = head.rsplit("/", 1)[-1]
    if "/" in head:
        if head.startswith("/") or "://" in head or any(char.isspace() for char in head):
            return False
        parts = head.split("/")
        first = parts[0]
        if ("." in first and not first.startswith(".")) or ":" in first:
            return False
        if not all(part not in {"", ".", ".."} for part in parts):
            return False
        versioned = any(re.fullmatch(r"v\d+", part, re.IGNORECASE) for part in parts)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        route_root = first.lower()
        versioned_repository_path = route_root in _REPOSITORY_DIRECTORY_NAMES or (
            bool(re.fullmatch(r"v\d+", first, re.IGNORECASE))
            and len(parts) > 1
            and parts[1].lower() in _REPOSITORY_DIRECTORY_NAMES
        )
        if (
            route_root in {"localhost", "user", "users"}
            or (route_root in {"api", "service", "services"} and versioned)
            or (route_root == "api" and ext in _DOCUMENT_EXTENSIONS)
            or (versioned and not versioned_repository_path)
        ):
            return False
        if name.startswith(".") or name.lower() in _EXTENSIONLESS_PATH_NAMES:
            return True
        if "." in name:
            ext = name.rsplit(".", 1)[-1].lower()
            return ext in _CODE_EXTENSIONS or ext in _DOCUMENT_EXTENSIONS
        # Extensionless slash tokens are inherently ambiguous with HTTP routes.
        # Validate them only under conventional repository directories; exact
        # configured evidence paths are recognized separately.
        return first.lower() in _REPOSITORY_DIRECTORY_NAMES or (
            bool(re.fullmatch(r"v\d+", first, re.IGNORECASE))
            and len(parts) > 1
            and parts[1].lower() in _REPOSITORY_DIRECTORY_NAMES
        )
    if (
        name.startswith(".")
        or head.lower() in _EXTENSIONLESS_PATH_NAMES
        or ("#" in token and "." in head)
    ):
        return True
    if "." not in head:
        return False
    ext = head.rsplit(".", 1)[-1].lower()
    return ext in _CODE_EXTENSIONS or ext in _DOCUMENT_EXTENSIONS


def _looks_like_evidence_path(token: str, evidence: Mapping[str, str] | None) -> bool:
    """Recognize exact or sibling paths under configured evidence directories."""
    head = token.split("::", 1)[0].split("#", 1)[0].strip()
    if not evidence:
        return False
    if head in evidence:
        return True
    parent = head.rpartition("/")[0]
    return bool(parent and any(path.rpartition("/")[0] == parent for path in evidence))


def _looks_like_symbol(token: str) -> bool:
    """True when *token* is an unambiguous code identifier worth checking.

    Skips lowercase single words (``full``, ``none``) - too likely to be an
    enum value or an English word the model legitimately quoted.
    """
    if not _IDENT.match(token):
        return False
    if "/" in token:
        owner = token.split("/", 1)[0]
        if (
            owner[:1].islower()
            or owner.upper()
            in {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
            or re.fullmatch(r"v\d+", owner, re.IGNORECASE)
        ):
            return False
    if re.search(_QUALIFIER, token):
        return True
    if "_" in token:
        return True
    # CamelCase / has an internal capital: an uppercase letter after the first
    # character marks it as a type-like identifier rather than a plain word.
    return any(ch.isupper() for ch in token[1:])


def _iter_strings(obj: Any, _depth: int = 0) -> Any:
    """Yield every string reachable inside a (possibly nested) context object."""
    if _depth > 6:
        return
    if isinstance(obj, str):
        yield obj
    elif is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            yield from _iter_strings(getattr(obj, f.name), _depth + 1)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_strings(k, _depth + 1)
            yield from _iter_strings(v, _depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            yield from _iter_strings(item, _depth + 1)


def _normalize_token(token: str) -> str:
    """Remove surrounding prose punctuation without stripping a leading dot path."""
    return token.strip().strip(",;:()[]{}<>\"'").rstrip(".")


def _collect_token(token: str, known_paths: set[str], known_symbols: set[str]) -> None:
    token = _normalize_token(token)
    if not token:
        return
    if _looks_like_path(token):
        head = token.split("::", 1)[0].split("#", 1)[0].strip()
        known_paths.add(token)
        known_paths.add(head)
        known_paths.add(head.rsplit("/", 1)[-1])
    if _looks_like_symbol(token):
        known_symbols.add(token)
        for part in re.split(_QUALIFIER, token):
            if part:
                known_symbols.add(part)


def collect_known(ctx: Any) -> tuple[set[str], set[str]]:
    """Collect the known paths and symbols from a subkind context object.

    Returns ``(known_paths, known_symbols)``. ``known_paths`` includes each
    path plus its basename so a page that cites ``builder.py`` still matches a
    payload path of ``.../builder.py``. ``known_symbols`` also includes the
    last dotted / ``::`` segment of each identifier so ``Foo.bar`` grounds a
    citation of ``bar``.
    """
    known_paths: set[str] = set()
    known_symbols: set[str] = set()
    for s in _iter_strings(ctx):
        _collect_token(s, known_paths, known_symbols)
    return known_paths, known_symbols


def _evidence_grounded(
    token: str,
    *,
    is_path: bool,
    evidence: Mapping[str, str] | None,
) -> bool:
    """Require the complete evidence-derived citation to occur verbatim.

    Context matching intentionally permits abbreviations, but applying that
    policy to free-form evidence lets an unrelated qualifier borrow a shared
    basename or member. Evidence therefore has the stricter contract promised
    by the prompt: the complete normalized citation must be in the included
    excerpt (or exactly name the included file).
    """
    if not evidence:
        return False
    normalized = _normalize_token(token)
    if is_path:
        head = normalized.split("::", 1)[0].split("#", 1)[0].strip()
        if normalized == head and head in evidence:
            return True
    boundary_chars = r"A-Za-z0-9_./:#-"
    pattern = re.compile(rf"(?<![{boundary_chars}]){re.escape(normalized)}(?![{boundary_chars}])")
    return any(pattern.search(text) is not None for text in evidence.values())


def _path_grounded(token: str, known_paths: set[str]) -> bool:
    normalized = _normalize_token(token)
    if "::" in normalized or "#" in normalized:
        return normalized in known_paths
    head = token.split("::", 1)[0].split("#", 1)[0].strip()
    if head in known_paths:
        return True
    base = head.rsplit("/", 1)[-1]
    if base in known_paths:
        return True
    # Cited path is a suffix of a known path (or vice versa) - same file,
    # different depth of qualification.
    return any(kp.endswith("/" + head) or head.endswith("/" + kp) for kp in known_paths)


def _symbol_grounded(token: str, known_symbols: set[str]) -> bool:
    return token in known_symbols


def check_grounding(
    content: str,
    ctx: Any,
    evidence: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Strip ungrounded path/symbol citations from *content*.

    Returns ``(cleaned_content, ungrounded_tokens)``. Each ungrounded token
    keeps its text but loses its backticks, so it reads as prose rather than a
    verified code reference. ``ungrounded_tokens`` is deduplicated in first-seen
    order for logging.
    """
    if not content:
        return content, []
    known_paths, known_symbols = collect_known(ctx)
    ungrounded: list[str] = []
    seen: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        is_path = _looks_like_path(token) or _looks_like_evidence_path(token, evidence)
        is_symbol = (not is_path) and _looks_like_symbol(token)
        if not is_path and not is_symbol:
            return match.group(0)
        grounded_in_context = (
            _path_grounded(token, known_paths)
            if is_path
            else _symbol_grounded(token, known_symbols)
        )
        grounded = grounded_in_context or _evidence_grounded(
            token, is_path=is_path, evidence=evidence
        )
        if grounded:
            return match.group(0)
        if token not in seen:
            seen.add(token)
            ungrounded.append(token)
        # Demote to plain text (keep the token, drop the code-span backticks).
        return match.group(1)

    cleaned = _BACKTICK.sub(replace, content)
    return cleaned, ungrounded
