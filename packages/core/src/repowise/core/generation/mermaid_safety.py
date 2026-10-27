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
_SEQUENCE_DIRECTIVE_RE = re.compile(r"^\s*sequenceDiagram\b")

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

# A comment or a directive. ``%%`` opens a comment for the rest of the line,
# and ``%%{...}%%`` is a configuration directive — neither is diagram syntax,
# and the ``{`` in a directive would otherwise read as a rhombus label.
_DIRECTIVE_OR_COMMENT_RE = re.compile(r"^\s*%%")

# The text between a pair of pipes on an edge (``A -->|yes/no| B``). It is a
# label like any other, but it is never bracketed, so both passes below have
# to be told to leave it alone.
_PIPE_LABEL_RE = re.compile(r"\|[^|\n]*\|")

# An identifier that mermaid already accepts as-is. Used to reserve names so a
# slug can never land on a node that is already in the diagram.
_BARE_ID_RE = re.compile(r"(?<![\w./\\])([A-Za-z_][A-Za-z0-9_]*)(?![\w./\\])")


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


def _protected_runs(line: str) -> list[tuple[str, bool]]:
    """Split *line* into ``(segment, is_protected)`` runs.

    Protected means "this is label text, not diagram syntax": a quoted string,
    or the text between a pair of pipes on an edge. Neither may be slugged —
    ``A -->|read/write| B`` is an ordinary edge label, and rewriting it to
    ``read_write`` corrupts a diagram that rendered perfectly well.
    """
    runs: list[tuple[str, bool]] = []
    for segment, is_quoted in _split_on_quotes(line):
        if is_quoted:
            runs.append((segment, True))
            continue
        pos = 0
        for match in _PIPE_LABEL_RE.finditer(segment):
            if match.start() > pos:
                runs.append((segment[pos : match.start()], False))
            runs.append((match.group(0), True))
            pos = match.end()
        if pos < len(segment):
            runs.append((segment[pos:], False))
    return runs


def _is_skippable(line: str) -> bool:
    """True for a line that carries no diagram syntax at all."""
    return bool(_DIRECTIVE_OR_COMMENT_RE.match(line))


def _disambiguator(raw: str) -> str:
    """A short suffix derived from the id, for slugs that collide.

    Deliberately not a counter: numbering by order of appearance means adding
    one unrelated node renames every colliding node after it, so a
    regenerated diagram churns for no reason.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]


def _build_id_map(body: str) -> dict[str, str]:
    """Map every illegal (path/dotted) node ID in *body* to a unique slug.

    Only unprotected text is considered — a path inside a label is prose, and
    rewriting it there is what used to turn a readable ``A["src/main.py"]``
    into ``A["src_main_py"]``.
    """
    raw_ids: list[str] = []
    seen: set[str] = set()
    # Identifiers that were already legal. A slug landing on one of these would
    # merge two distinct nodes into a single box, so they reserve their name
    # even though they need no rewriting themselves.
    already_legal: set[str] = set()
    for line in body.split("\n"):
        if _is_skippable(line):
            continue
        for segment, is_protected in _protected_runs(line):
            if is_protected:
                continue
            for match in _PATHY_TOKEN_RE.finditer(segment):
                raw = match.group(1)
                if raw not in seen:
                    seen.add(raw)
                    raw_ids.append(raw)
            already_legal.update(_BARE_ID_RE.findall(segment))

    by_slug: dict[str, list[str]] = {}
    for raw in raw_ids:
        by_slug.setdefault(_slug(raw), []).append(raw)

    id_map: dict[str, str] = {}
    for slug, owners in by_slug.items():
        if len(owners) == 1 and slug not in already_legal:
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

    return "\n".join(
        line if _is_skippable(line) else _process(line) for line in body.split("\n")
    )


def _rewrite_ids(body: str, id_map: dict[str, str]) -> str:
    """Replace illegal node IDs with their slugs, outside label text only."""
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
        if _is_skippable(line):
            out_lines.append(line)
            continue
        pieces: list[str] = []
        for segment, is_protected in _protected_runs(line):
            if not is_protected:
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
            elif _SEQUENCE_DIRECTIVE_RE.match(first_line):
                body = _drop_flowchart_styling(body)
            # Other diagram kinds (erDiagram, classDiagram) have their own
            # grammars; quoting labels there is unsafe, so they are left alone.
        except Exception as exc:  # never corrupt a working diagram
            log.debug("mermaid_safety.block_failed", error=str(exc))
            body = match.group("body")
        return open_fence + body + close_fence

    return _MERMAID_FENCE_RE.sub(_replace, markdown)


# ---------------------------------------------------------------------------
# Structural validation
#
# ``sanitize_mermaid`` repairs what it can recognise. What it cannot repair a
# renderer rejects, and one rejected block shows the reader an error box where
# the page promised a picture. These checks are the grammar rules a generated
# diagram breaks in practice, per diagram type. They are not a parser: a block
# they pass can still fail to render, and types they have no rules for pass on
# a known header.
# ---------------------------------------------------------------------------

_DIAGRAM_TYPES = frozenset(
    {
        "graph",
        "flowchart",
        "sequencediagram",
        "classdiagram",
        "statediagram",
        "statediagram-v2",
        "erdiagram",
        "journey",
        "gantt",
        "pie",
        "mindmap",
        "timeline",
        "gitgraph",
        "quadrantchart",
        "c4context",
        "c4container",
        "c4component",
        "c4dynamic",
        "c4deployment",
    }
)

# A flowchart node id as mermaid reads it, followed by what may come after one.
_FLOW_ID_RE = re.compile(r"^[A-Za-z0-9_.]+$")
# Keywords the flowchart grammar reads before it reads an id: a node named
# ``end`` closes a subgraph, one named ``graph`` starts a new diagram.
_FLOW_RESERVED_IDS = frozenset(
    {"end", "graph", "flowchart", "subgraph", "style", "class", "classDef", "click", "linkStyle"}
)
_FLOW_KEYWORDS = ("classDef ", "class ", "style ", "linkStyle ", "click ", "direction ")

# Every statement a sequence diagram accepts, by its leading word.
_SEQ_STATEMENTS = frozenset(
    {
        "participant",
        "actor",
        "autonumber",
        "note",
        "activate",
        "deactivate",
        "title",
        "create",
        "destroy",
        "link",
        "links",
        "box",
        "loop",
        "alt",
        "else",
        "opt",
        "par",
        "and",
        "critical",
        "option",
        "break",
        "rect",
        "end",
    }
)
_SEQ_BLOCK_OPENERS = frozenset({"loop", "alt", "opt", "par", "critical", "break", "rect", "box"})
# ``A->>B: text``, with the arrow set sequence diagrams define.
_SEQ_MESSAGE_RE = re.compile(
    r"^[^\s:<>\-+][^:<>]*?\s*(?:<<)?(?:-->>|->>|-->|->|--x|-x|--\)|-\))\s*[+-]?[^:]+?\s*:.*$"
)
_SEQ_PARTICIPANT_RE = re.compile(
    r'^(participant|actor)\s+([A-Za-z0-9_]+|"[^"]+")(\s+as\s+.+)?\s*$', re.I
)


_FLOWCHART_STYLING_RE = re.compile(r"^\s*(classDef|class|style|linkStyle)\s")


def _drop_flowchart_styling(body: str) -> str:
    """Remove flowchart styling lines a sequence diagram cannot parse.

    A model that has drawn flowcharts carries ``classDef`` and ``class`` lines
    over; they add nothing to a sequence and fail the whole block.
    """
    return "\n".join(line for line in body.split("\n") if not _FLOWCHART_STYLING_RE.match(line))


def _statements(body: str) -> list[str]:
    """The block's lines that carry syntax: no blanks, comments, directives or front matter."""
    lines = body.strip().split("\n")
    if lines and lines[0].strip() == "---":
        # A ``---``-fenced front matter block (title, config) precedes the header.
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0)
        lines = lines[closing + 1 :]
    return [line.strip() for line in lines if line.strip() and not _is_skippable(line)]


def _balanced(line: str) -> bool:
    """Whether quotes and brackets on *line* close, ignoring quoted text."""
    if line.count('"') % 2:
        return False
    unquoted = re.sub(r'"[^"]*"', "", line)
    unquoted = _PIPE_LABEL_RE.sub("", unquoted)
    pairs = {"]": "[", ")": "(", "}": "{"}
    stack: list[str] = []
    for char in unquoted:
        if char in "[({":
            stack.append(char)
        elif char in pairs and (not stack or stack.pop() != pairs[char]):
            return False
    return not stack


def _flowchart_problems(lines: list[str]) -> list[str]:
    problems: list[str] = []
    depth = 0
    for line in lines:
        word = line.split()[0]
        if word == "subgraph":
            depth += 1
            continue
        if line == "end":
            depth -= 1
            if depth < 0:
                problems.append("`end` without a matching `subgraph`")
                depth = 0
            continue
        if line.startswith(_FLOW_KEYWORDS):
            continue
        if not _balanced(line):
            problems.append(f"unbalanced quotes or brackets: {line[:60]}")
            continue
        # The first token is a node id, before any shape, class or edge.
        head = re.split(r"[\s\[\(\{>:|&-]|:::", line, maxsplit=1)[0]
        if head and (not _FLOW_ID_RE.match(head) or head in _FLOW_RESERVED_IDS):
            problems.append(f"illegal node id {head!r}")
    if depth:
        problems.append("`subgraph` without a matching `end`")
    return problems


def _sequence_problems(lines: list[str]) -> list[str]:
    problems: list[str] = []
    depth = 0
    for line in lines:
        word = line.split()[0].lower().rstrip(":")
        if word in ("classdef", "class", "style", "linkstyle", "click", "subgraph"):
            problems.append(f"`{word}` is not sequence diagram syntax")
            continue
        if word in _SEQ_BLOCK_OPENERS:
            depth += 1
        elif word == "end":
            depth -= 1
            if depth < 0:
                problems.append("`end` without an opening block")
                depth = 0
        elif word in ("participant", "actor"):
            if not _SEQ_PARTICIPANT_RE.match(line):
                problems.append(f"participant id must be a plain word: {line[:60]}")
        elif word not in _SEQ_STATEMENTS and not _SEQ_MESSAGE_RE.match(line):
            problems.append(f"not a sequence diagram statement: {line[:60]}")
    if depth:
        problems.append("a block (`loop`, `alt`, `opt`, ...) without its `end`")
    return problems


def mermaid_problems(body: str) -> list[str]:
    """What would stop the renderer drawing this mermaid block, if anything.

    Empty means no known rule is broken. The checks follow the diagram type the
    first statement names; types with no checks here pass on a known header.
    """
    lines = _statements(body)
    if not lines:
        return ["empty diagram"]
    header = lines[0].split()[0].lower()
    if header not in _DIAGRAM_TYPES and not header.endswith("-beta"):
        return [f"unknown diagram type {lines[0].split()[0]!r}"]
    if header in ("graph", "flowchart"):
        return _flowchart_problems(lines[1:])
    if header == "sequencediagram":
        return _sequence_problems(lines[1:])
    return []


def invalid_mermaid_blocks(markdown: str) -> list[str]:
    """One problem line per mermaid block in *markdown* that fails validation."""
    found: list[str] = []
    for match in _MERMAID_FENCE_RE.finditer(markdown or ""):
        problems = mermaid_problems(match.group("body"))
        if problems:
            found.append(problems[0])
    return found


def renderable_mermaid(markdown: str) -> str:
    """*markdown* with its mermaid repaired, and any block still failing removed."""
    return strip_invalid_mermaid(sanitize_mermaid(markdown))


def strip_invalid_mermaid(markdown: str) -> str:
    """*markdown* without the mermaid blocks that fail validation."""

    def _keep(match: re.Match[str]) -> str:
        return "" if mermaid_problems(match.group("body")) else match.group(0)

    stripped = _MERMAID_FENCE_RE.sub(_keep, markdown)
    if stripped == markdown:
        # Nothing removed: leave the page byte-identical, or a template page
        # with spare blank lines reads as changed and is stored twice.
        return markdown
    return re.sub(r"\n{3,}", "\n\n", stripped)


_HEADING_LINE_RE = re.compile(r"(?m)^#{1,6}\s")


def strip_leading_preamble(markdown: str) -> str:
    """Drop agent narration before the first markdown heading.

    Pages always start with ``# ...``. Text before that is tool-use chatter.
    If there is no heading, or stripping would empty the page, return input
    unchanged so clean output stays byte-identical.
    """
    if not markdown:
        return markdown
    match = _HEADING_LINE_RE.search(markdown)
    if match is None or match.start() == 0:
        return markdown
    trimmed = markdown[match.start() :]
    if not trimmed.strip():
        return markdown
    return trimmed


def sanitize_pages(pages: list) -> int:
    """Make every page of a run renderable before it is stored.

    The one pass every page goes through, whatever wrote it: a fresh model
    response, one reused from a prior run, a diagram embedded after
    generation (the overview's map) or a template. Repairs what
    :func:`sanitize_mermaid` can, and removes any block that still fails
    :func:`mermaid_problems`. Mutates ``page.content`` in place. Returns the
    number of pages whose content changed (for logging).
    """
    changed = 0
    for page in pages:
        content = getattr(page, "content", None)
        if not content:
            continue
        fixed = renderable_mermaid(strip_leading_preamble(content))
        if invalid_mermaid_blocks(sanitize_mermaid(content)):
            log.warning("mermaid_safety.block_removed", page_id=getattr(page, "page_id", ""))
        if fixed != content:
            page.content = fixed
            changed += 1
    return changed


__all__ = [
    "invalid_mermaid_blocks",
    "mermaid_problems",
    "renderable_mermaid",
    "sanitize_mermaid",
    "sanitize_pages",
    "strip_invalid_mermaid",
    "strip_leading_preamble",
]
