"""Mermaid diagram safety pass.

LLM-generated mermaid frequently breaks the renderer in two ways:

* **Illegal node IDs** — the model uses a file path (``pkg/foo.py``) or a
  dotted name (``app.main``) directly as a node ID. Mermaid only accepts
  ``[A-Za-z0-9_]`` in bare IDs, so a single path ID fails the *entire*
  diagram.
* **Unquoted labels** — a shape label containing parentheses, slashes,
  quotes or angle brackets (``A[run() -> None]``) is a parse error unless
  the label is wrapped in quotes.

This module rewrites only the ``mermaid`` fenced blocks of generated
markdown to fix both. It is deliberately conservative: every block is
processed under ``try/except`` and the original text is kept on any doubt,
so the pass can never make a diagram that already rendered worse.

Pure string work, no LLM call — cheap enough to run on every page.
"""

from __future__ import annotations

import hashlib
import re

import structlog

log = structlog.get_logger(__name__)

# Capture a fenced mermaid block: opening fence (with optional info string),
# body, closing fence. Non-greedy body so adjacent blocks don't merge.
_MERMAID_FENCE_RE = re.compile(
    r"(?P<open>```+[ \t]*mermaid[^\n]*\n)(?P<body>.*?)(?P<close>\n[ \t]*```+)",
    re.DOTALL | re.IGNORECASE,
)

# Diagram kinds that use ``id[label]`` node syntax we know how to repair.
_GRAPH_DIRECTIVE_RE = re.compile(r"^\s*(graph|flowchart)\b", re.IGNORECASE)

# Shape bracket pairs, longest opener first so ``([`` wins over ``(``.
_SHAPE_PAIRS: tuple[tuple[str, str], ...] = (
    ("([", "])"),
    ("[[", "]]"),
    ("[(", ")]"),
    ("((", "))"),
    ("{{", "}}"),
    ("[/", "/]"),
    ("[\\", "\\]"),
    ("[", "]"),
    ("(", ")"),
    ("{", "}"),
)

# A node ID directly preceding a shape bracket. Permits path/dotted chars so
# we can detect (and then slug) the illegal ones.
_RAW_ID_CHARS = r"A-Za-z0-9_./\\-"

# A path/dotted token anywhere (used to also catch bare edge endpoints that
# never got a shape definition, e.g. ``A --> pkg/foo.py``). Requires at least
# one ``.``/``/``/``\`` so plain identifiers and ``-`` IDs are left alone.
_PATHY_TOKEN_RE = re.compile(
    r"(?<![\w./\\])([A-Za-z0-9_][\w]*(?:[./\\][\w]+)+)"
)

# Label characters that force quoting.
_LABEL_NEEDS_QUOTE_RE = re.compile(r"[()\[\]{}\"<>|/]")


def _slug(raw: str) -> str:
    """Turn an illegal node ID into a clean ``[A-Za-z0-9_]`` slug."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "n"
    if not s[0].isalpha() and s[0] != "_":
        s = "n_" + s
    return s


def _split_on_quotes(text: str) -> list[tuple[str, bool]]:
    """Split *text* into ``(segment, is_quoted)`` runs on double quotes.

    The scanner's "string mode": once a label has been quoted, its contents
    are prose, and a path inside it must not be treated as a node ID. An
    unterminated quote makes the rest of the line a string, which is what the
    mermaid parser does too.
    """
    segments: list[tuple[str, bool]] = []
    buffer: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            buffer.append(char)
            if quoted:
                segments.append(("".join(buffer), True))
                buffer = []
                quoted = False
            else:
                # The quote opens a string: flush what came before it, minus
                # the quote itself, then start the quoted run with it.
                opening = "".join(buffer[:-1])
                if opening:
                    segments.append((opening, False))
                buffer = [char]
                quoted = True
            continue
        buffer.append(char)
    if buffer:
        segments.append(("".join(buffer), quoted))
    return segments


def _disambiguator(raw: str) -> str:
    """A short suffix derived from the id, for slugs that collide.

    Deliberately not a counter: numbering by order of appearance means adding
    one unrelated node renames every colliding node after it, so a
    regenerated diagram churns for no reason.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]


def _build_id_map(body: str) -> dict[str, str]:
    """Map every illegal (path/dotted) node ID in *body* to a unique slug.

    Only unquoted text is considered — a path inside a label is prose, and
    rewriting it there is what used to turn a readable ``A["src/main.py"]``
    into ``A["src_main_py"]``.
    """
    raw_ids: list[str] = []
    seen: set[str] = set()
    for line in body.split("\n"):
        for segment, is_quoted in _split_on_quotes(line):
            if is_quoted:
                continue
            for match in _PATHY_TOKEN_RE.finditer(segment):
                raw = match.group(1)
                if raw not in seen:
                    seen.add(raw)
                    raw_ids.append(raw)

    by_slug: dict[str, list[str]] = {}
    for raw in raw_ids:
        by_slug.setdefault(_slug(raw), []).append(raw)

    id_map: dict[str, str] = {}
    for slug, owners in by_slug.items():
        if len(owners) == 1:
            id_map[owners[0]] = slug
            continue
        # Every owner is suffixed, not just the ones after the first: which id
        # keeps the bare slug would otherwise depend on document order.
        for raw in owners:
            id_map[raw] = f"{slug}_{_disambiguator(raw)}"
    return id_map


def _find_closer(text: str, start: int, opener: str, closer: str) -> int:
    """Index of the closer matching the opener at *start*, or -1.

    Counts nesting rather than taking the first closer, so a label that
    contains the same bracket — ``A[run(x[0])]`` — ends where it really ends
    instead of in the middle.
    """
    depth = 1
    i = start + len(opener)
    n = len(text)
    while i < n:
        if text.startswith(closer, i):
            depth -= 1
            if depth == 0:
                return i
            i += len(closer)
            continue
        if text.startswith(opener, i):
            depth += 1
            i += len(opener)
            continue
        i += 1
    return -1


def _quote_labels(body: str) -> str:
    """Wrap shape labels containing special characters in double quotes."""

    def _process(text: str) -> str:
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            matched = False
            for opener, closer in _SHAPE_PAIRS:
                if text.startswith(opener, i):
                    end = _find_closer(text, i, opener, closer)
                    if end == -1:
                        continue
                    label = text[i + len(opener) : end]
                    stripped = label.strip()
                    already_quoted = (
                        len(stripped) >= 2
                        and stripped[0] == '"'
                        and stripped[-1] == '"'
                    )
                    if (
                        stripped
                        and not already_quoted
                        and _LABEL_NEEDS_QUOTE_RE.search(label)
                    ):
                        safe = label.replace('"', "&quot;")
                        out.append(opener + '"' + safe + '"' + closer)
                    else:
                        out.append(opener + label + closer)
                    i = end + len(closer)
                    matched = True
                    break
            if not matched:
                out.append(text[i])
                i += 1
        return "".join(out)

    return "\n".join(_process(line) for line in body.split("\n"))


def _rewrite_ids(body: str, id_map: dict[str, str]) -> str:
    """Replace illegal node IDs with their slugs, outside quoted labels only."""
    if not id_map:
        return body
    patterns = [
        (
            re.compile(
                r"(?<![" + _RAW_ID_CHARS + r"])"
                + re.escape(raw)
                + r"(?![" + _RAW_ID_CHARS + r"])"
            ),
            slug,
        )
        for raw, slug in id_map.items()
    ]

    out_lines: list[str] = []
    for line in body.split("\n"):
        pieces: list[str] = []
        for segment, is_quoted in _split_on_quotes(line):
            if not is_quoted:
                for pattern, slug in patterns:
                    segment = pattern.sub(slug, segment)
            pieces.append(segment)
        out_lines.append("".join(pieces))
    return "\n".join(out_lines)


def _rewrite_graph_block(body: str) -> str:
    """Quote risky labels, then slug illegal node IDs outside those labels.

    Order matters. Rewriting IDs first meant a path inside a label was still
    bare text, so it was slugged too and ``A[src/main.py]`` came out as
    ``A[src_main_py]`` — a legal diagram with an unreadable label. Quoting
    first turns the label into a string the ID pass then skips.
    """
    quoted = _quote_labels(body)
    return _rewrite_ids(quoted, _build_id_map(quoted))


def sanitize_mermaid(markdown: str) -> str:
    """Return *markdown* with every mermaid block made render-safe.

    No-op for content with no mermaid blocks. Each block is repaired
    independently and falls back to its original text on any error.
    """
    if not markdown or "mermaid" not in markdown.lower():
        return markdown

    def _replace(match: re.Match[str]) -> str:
        open_fence = match.group("open")
        body = match.group("body")
        close_fence = match.group("close")
        try:
            first_line = body.lstrip().split("\n", 1)[0]
            if _GRAPH_DIRECTIVE_RE.match(first_line):
                body = _rewrite_graph_block(body)
            else:
                # Other diagram kinds (sequenceDiagram, erDiagram, classDiagram)
                # have different grammars — only quote obviously risky labels is
                # unsafe there, so leave them untouched.
                pass
        except Exception as exc:  # never corrupt a working diagram
            log.debug("mermaid_safety.block_failed", error=str(exc))
            body = match.group("body")
        return open_fence + body + close_fence

    return _MERMAID_FENCE_RE.sub(_replace, markdown)


def sanitize_pages(pages: list) -> int:
    """Run :func:`sanitize_mermaid` over a list of ``GeneratedPage``.

    Mutates ``page.content`` in place. Returns the number of pages whose
    content actually changed (for logging).
    """
    changed = 0
    for page in pages:
        content = getattr(page, "content", None)
        if not content:
            continue
        fixed = sanitize_mermaid(content)
        if fixed != content:
            page.content = fixed
            changed += 1
    return changed


__all__ = ["sanitize_mermaid", "sanitize_pages"]
