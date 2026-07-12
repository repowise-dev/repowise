"""Post-generation grounding check for onboarding pages.

Onboarding prose is grounded ONLY by prompt instruction ("do not invent
file paths or symbol names"). Nothing verified the model obeyed, so a
fabricated citation - a file the payload never mentioned, a symbol that
does not exist - reached the reader as an authoritative backticked
reference.

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

# A bare identifier, optionally dotted or ``::``-qualified (e.g. ``LanguageSpec``,
# ``get_session``, ``foo.Bar.baz``, ``path.py::Name``).
_IDENT = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)+$|^[A-Za-z_][A-Za-z0-9_]*$"
)


def _looks_like_path(token: str) -> bool:
    """True when *token* is shaped like a source file path we can verify."""
    head = token.split("::", 1)[0]
    head = head.split("#", 1)[0].strip()
    if "." not in head:
        return False
    ext = head.rsplit(".", 1)[-1].lower()
    return ext in _CODE_EXTENSIONS


def _looks_like_symbol(token: str) -> bool:
    """True when *token* is an unambiguous code identifier worth checking.

    Skips lowercase single words (``full``, ``none``) - too likely to be an
    enum value or an English word the model legitimately quoted.
    """
    if not _IDENT.match(token):
        return False
    if "." in token or "::" in token:
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
        token = s.strip()
        if not token:
            continue
        if _looks_like_path(token):
            head = token.split("::", 1)[0].split("#", 1)[0].strip()
            known_paths.add(head)
            known_paths.add(head.rsplit("/", 1)[-1])
        # A string can carry both a path and a symbol vocabulary; also mine
        # bare identifiers as known symbols.
        if _looks_like_symbol(token):
            known_symbols.add(token)
            for part in re.split(r"\.|::", token):
                if part:
                    known_symbols.add(part)
    return known_paths, known_symbols


def _path_grounded(token: str, known_paths: set[str]) -> bool:
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
    if token in known_symbols:
        return True
    # Grounded if any qualified segment is known (``Registry.get`` grounds on
    # ``Registry``; a member of a known concept is acceptable).
    parts = [p for p in re.split(r"\.|::", token) if p]
    return any(p in known_symbols for p in parts)


def check_grounding(content: str, ctx: Any) -> tuple[str, list[str]]:
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
        is_path = _looks_like_path(token)
        is_symbol = (not is_path) and _looks_like_symbol(token)
        if not is_path and not is_symbol:
            return match.group(0)
        grounded = (
            _path_grounded(token, known_paths)
            if is_path
            else _symbol_grounded(token, known_symbols)
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
