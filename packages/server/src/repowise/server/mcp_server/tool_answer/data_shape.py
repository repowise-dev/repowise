"""Ground data-shape questions ("what fields does each entry in X contain?").

A data-shape question names a data blob / row / record and asks for its field
set. Fuzzy retrieval scatters across every file that *touches* the blob and
gates low, so the tool hands back a best_guesses pointer list and the agent
drills with Read/get_symbol to find the fields itself. But the answer usually
lives verbatim in source: a documented ``{...}`` shape in a docstring near the
identifier, and/or the concrete keys consumers pull off the parsed value
(``partner.get("co_change_count")``). This module mines that field set directly
so the tool answers in one call.

Precision-first: every reported field is a quoted token lifted from source, so a
field with no source backing can never be synthesised. Two grounding sources,
precision-ordered: a documented brace shape (authoritative -> high) beats mined
key accesses (usage-inferred -> medium). Returns ``None`` (the caller falls
through to normal retrieval) unless a shape is genuinely grounded.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from repowise.core.exclusion import build_exclude_spec, is_excluded
from repowise.server.mcp_server.tool_answer.config import (
    _DATA_SHAPE_ACCESS_WINDOW,
    _DATA_SHAPE_DOC_WINDOW,
    _DATA_SHAPE_GREP_TIMEOUT_S,
    _DATA_SHAPE_MAX_FILES,
    _DATA_SHAPE_MIN_FIELDS,
    _DATA_SHAPE_MIN_IDENT_LEN,
)

# --- Question detection ---------------------------------------------------

# Nouns that, when present alongside a named identifier, mark a question as
# asking for a data shape rather than a mechanism ("how does X work").
_SHAPE_NOUNS = frozenset(
    {
        "field",
        "fields",
        "key",
        "keys",
        "column",
        "columns",
        "schema",
        "attribute",
        "attributes",
        "property",
        "properties",
        "shape",
        "structure",
    }
)
# Container nouns ("each entry in X", "what does each record hold") only count as
# a data-shape signal when paired with a containment verb, so "entry point" and
# "list items" don't over-fire.
_CONTAINER_NOUNS = frozenset(
    {"entry", "entries", "element", "elements", "record", "records", "item", "items"}
)
_CONTAINMENT_VERBS = ("contain", "consist", "comprise", "hold", "look like", "made of", "made up")

_WORD = re.compile(r"[a-z_]+")


def _is_data_shape_question(question: str, question_ids: set[str]) -> bool:
    """Whether ``question`` asks for the field set of a named data blob.

    Requires (a) a named identifier (something to ground on) and (b) a lexical
    data-shape cue: a shape noun (field/key/column/schema/...), or a container
    noun (entry/record/element/...) paired with a containment verb. Mechanism
    questions ("how does X work") carry neither and fall through. The cue is a
    cheap gate only; the miner is the real precision gate (it returns nothing
    unless the fields are grounded in source), so a false-positive cue is safe.
    """
    if not question or not question_ids:
        return False
    low = question.lower()
    words = set(_WORD.findall(low))
    if words & _SHAPE_NOUNS:
        return True
    return bool(words & _CONTAINER_NOUNS) and any(v in low for v in _CONTAINMENT_VERBS)


# --- Source mining --------------------------------------------------------

_QUOTED_FIELD = re.compile(r"""['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")
_BRACE_GROUP = re.compile(r"\{([^{}]*)\}")
_COMMENT_LEAD = ("#", "*", "//", "--")


def _neg_str(s: str) -> tuple[int, ...]:
    """Sort key that inverts string order (smaller string -> larger key).

    Lets a ``max()`` tie-break toward the alphabetically-first path.
    """
    return tuple(-ord(c) for c in s)


def _specific_identifiers(question_ids: set[str]) -> list[str]:
    """Keep only identifiers specific enough to ground a data shape on.

    A bare short/generic token ("id", "row") greps the whole tree and mines an
    incoherent field union. Require a real data-name shape: long enough, and
    either snake_case or CamelCase (a name a schema/blob actually carries).
    Longest first so the most specific blob name is tried before its prefixes.
    """
    kept = [
        q
        for q in question_ids
        if len(q) >= _DATA_SHAPE_MIN_IDENT_LEN and ("_" in q or any(c.isupper() for c in q))
    ]
    return sorted(kept, key=len, reverse=True)


# Pathspecs limiting the grep to source and excluding heavy/vendored dirs. The
# exclusions matter for the --no-index fallback (a non-git tree has no ignore
# file); they're harmless on a tracked grep (those dirs are gitignored anyway).
_GREP_PATHSPECS = (
    "*.py",
    "*.ts",
    "*.tsx",
    ":!**/node_modules/**",
    ":!**/.venv/**",
    ":!**/dist/**",
    ":!**/build/**",
)
_TEST_PATH = re.compile(r"(^|/)tests?(/|$)|(^|/)test_[^/]*$|_test\.[^/]+$|\.spec\.[^/]+$")


def _order_candidates(files: list[str]) -> list[str]:
    """Order files so the one that *documents* the shape is scanned first.

    Test/fixture files build shapes but never document them, so they sort last
    (a fixture's incidental dict literal must not be the field set we mine when
    a real doc exists). Within each group, shallower paths first, then name -
    a declared schema usually lives nearer the package root than its consumers.
    """
    return sorted(
        files,
        key=lambda p: (bool(_TEST_PATH.search(p)), p.count("/"), p),
    )


def _run_grep(
    repo_root: Path, args: list[str], identifier: str
) -> subprocess.CompletedProcess | None:
    """Run a bounded, transport-safe ``git grep`` variant; ``None`` on failure."""
    try:
        return subprocess.run(
            # --no-pager + stdin=DEVNULL are load-bearing: this can run inside a
            # stdio MCP server whose stdin IS the JSON-RPC pipe, so a pager that
            # reads stdin would deadlock the transport.
            [
                "git",
                "--no-pager",
                "grep",
                # --no-color: this output is parsed, so a user's
                # ``color.ui=always`` git config must not wrap paths in ANSI
                # escapes (which would corrupt exclusion filtering and reads).
                "--no-color",
                *args,
                "-l",
                "-I",
                "-F",
                "-w",
                "-e",
                identifier,
                "--",
                *_GREP_PATHSPECS,
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_DATA_SHAPE_GREP_TIMEOUT_S,
        )
    except Exception:
        return None


def _grep_identifier_files(repo_root: Path, identifier: str, spec: object = None) -> list[str]:
    """Source files naming ``identifier`` (whole word), repo-relative.

    Tracked ``git grep`` first (fast, skips ignored/vendored). If the tree isn't
    a git checkout (returncode 128), retry with ``--no-index`` so the tool still
    grounds its answer on a non-git tree - both are fast C greps, never the
    per-file Python read that wedges on a large tree. Returns the full match set;
    the caller orders (doc files first) then caps, so the documenting file is
    never dropped by an unlucky order.

    ``--no-index`` scans the raw filesystem and ignores ``.gitignore``, so a
    gitignored stale wheel / vendored copy can surface as a match. The compiled
    ``spec`` (gitignore + ``exclude_patterns``) filters those out authoritatively,
    matching every other MCP read path.
    """
    proc = _run_grep(repo_root, [], identifier)
    if proc is not None and proc.returncode == 128:
        # Not a git repository - grep the filesystem directly.
        proc = _run_grep(repo_root, ["--no-index"], identifier)
    if proc is None or proc.returncode not in (0, 1):
        return []
    paths = [ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines() if ln.strip()]
    if spec is not None:
        paths = [p for p in paths if not is_excluded(p, spec)]
    return paths


def _read_lines(repo_root: Path, rel_path: str) -> list[str] | None:
    """Read a repo-relative file's lines, refusing any path outside the root."""
    try:
        abs_path = (repo_root / rel_path).resolve()
        abs_path.relative_to(repo_root.resolve())
        return abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None


def _docstring_line_set(lines: list[str]) -> set[int]:
    """1-based line numbers that fall inside a triple-quoted docstring.

    Approximate (toggles on triple-quote parity, not a real parser) but enough
    to tell a documented ``{...}`` shape from an executable dict literal. A
    one-line docstring (two markers on one line) counts as inside.
    """
    in_doc = False
    doc: set[int] = set()
    for idx, line in enumerate(lines, 1):
        cnt = line.count('"""') + line.count("'''")
        if cnt % 2 == 1:
            doc.add(idx)  # the boundary line itself is part of the docstring
            in_doc = not in_doc
        elif in_doc:
            doc.add(idx)
        elif cnt >= 2:
            doc.add(idx)  # a self-contained one-line docstring
    return doc


def _fields_in_order(text: str) -> list[str]:
    """Quoted field-name tokens inside ``text``, in order, deduped, >=2 chars."""
    seen: list[str] = []
    for m in _QUOTED_FIELD.finditer(text):
        tok = m.group(1)
        if len(tok) >= 2 and tok not in seen:
            seen.append(tok)
    return seen


def _mention_lines(lines: list[str], identifier: str) -> list[int]:
    """1-based line numbers where ``identifier`` appears as a whole word."""
    pat = re.compile(r"\b" + re.escape(identifier) + r"\b")
    return [idx for idx, line in enumerate(lines, 1) if pat.search(line)]


def _doc_shape_in_file(
    lines: list[str], identifier: str, mentions: list[int]
) -> tuple[list[str], int] | None:
    """A documented ``{...}`` field shape near an identifier mention, if any.

    A brace group counts only when it is in documentation context (inside a
    docstring, on a comment line, or wrapped in RST/markdown backticks), carries
    >= _DATA_SHAPE_MIN_FIELDS quoted tokens, and sits within
    _DATA_SHAPE_DOC_WINDOW lines of a mention of the identifier. Returns
    ``(fields, line)`` for the first such brace, or ``None``.
    """
    text = "\n".join(lines)
    doc_lines = _docstring_line_set(lines)
    for m in _BRACE_GROUP.finditer(text):
        start_line = text.count("\n", 0, m.start()) + 1
        line_str = lines[start_line - 1] if start_line - 1 < len(lines) else ""
        stripped = line_str.lstrip()
        doc_ctx = start_line in doc_lines or stripped.startswith(_COMMENT_LEAD) or "`" in line_str
        if not doc_ctx:
            continue
        fields = _fields_in_order(m.group(1))
        if len(fields) < _DATA_SHAPE_MIN_FIELDS:
            continue
        # The mention must sit near the brace, on either side: the identifier is
        # named then its shape documented (``blob is a list of {...}``), or the
        # shape is commented just above the field declaration (``# {...}`` then
        # ``field: type``). Both are common documentation styles.
        if not any(abs(start_line - ml) <= _DATA_SHAPE_DOC_WINDOW for ml in mentions):
            continue
        return fields, start_line
    return None


# A ``<receiver>.get("<key>")`` access - group(1) receiver, group(2) key.
_GET_ACCESS = re.compile(r"([A-Za-z_]\w*)\s*\.\s*get\(\s*['\"]([A-Za-z_]\w*)['\"]")


def _alias_keys_on_documented_lines(
    lines: list[str], doc_fields: set[str]
) -> list[tuple[str, int]]:
    """Alias keys a documented field is read as a fallback for.

    Targets one idiom precisely: ``<recv>.get("<A>") or <recv>.get("<B>")`` - the
    same receiver reads two keys joined by ``or``, so when one is a documented
    field the other is an alias for it (``partner.get("co_change_count") or
    partner.get("count")`` -> ``count``; ``... or partner.get("path")`` ->
    ``path``). Requiring the ``or`` fallback and a shared receiver keeps this tight:
    an assignment that merely co-mentions a documented key on a different record
    (``meta["prior_defect_count"] = ...meta["file_path"]``) or a test assertion
    does not match. Returns ``(alias, line)`` for keys not in ``doc_fields``.
    """
    out: list[tuple[str, int]] = []
    for idx, line in enumerate(lines, 1):
        if " or " not in line:
            continue
        by_recv: dict[str, list[str]] = {}
        for m in _GET_ACCESS.finditer(line):
            by_recv.setdefault(m.group(1), []).append(m.group(2))
        for keys in by_recv.values():
            if len(keys) < 2 or not any(k in doc_fields for k in keys):
                continue
            for k in keys:
                if k not in doc_fields:
                    out.append((k, idx))
    return out


def _accessed_fields_in_file(
    lines: list[str], identifier: str, mentions: list[int]
) -> list[tuple[str, int]]:
    """Field keys pulled off the value bound from the identifier in this file.

    Finds a variable bound to the identifier on a mention line (``for VAR in
    ...ident...`` or ``VAR = ...ident...``), then mines ``VAR.get("f")`` /
    ``VAR["f"]`` within the next _DATA_SHAPE_ACCESS_WINDOW lines. Also mines
    direct ``ident.get("f")`` / ``ident["f"]`` accesses (when the identifier is
    itself the dict). Returns ``(field, line)`` pairs in first-seen order.
    """
    bind_re = re.compile(
        r"\bfor\s+([A-Za-z_]\w*)\s+in\b.*\b"
        + re.escape(identifier)
        + r"\b|\b([A-Za-z_]\w*)\s*=\s*.*\b"
        + re.escape(identifier)
        + r"\b"
    )
    # Variables to mine accesses on, each scoped to the line it was bound at.
    scoped: list[tuple[str, int]] = []
    mention_set = set(mentions)
    for idx, line in enumerate(lines, 1):
        if idx not in mention_set:
            continue
        m = bind_re.search(line)
        if m:
            var = m.group(1) or m.group(2)
            if var and var != identifier:
                scoped.append((var, idx))
    # The identifier itself, mined across every one of its mention lines.
    scoped.extend((identifier, ml) for ml in mentions)

    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for var, start in scoped:
        get_re = re.compile(r"\b" + re.escape(var) + r"\s*\.\s*get\(\s*['\"]([A-Za-z_]\w*)['\"]")
        sub_re = re.compile(r"\b" + re.escape(var) + r"\s*\[\s*['\"]([A-Za-z_]\w*)['\"]\s*\]")
        hi = min(start + _DATA_SHAPE_ACCESS_WINDOW, len(lines))
        for i in range(start - 1, hi):
            for rx in (get_re, sub_re):
                for m in rx.finditer(lines[i]):
                    tok = m.group(1)
                    if len(tok) >= 2 and tok not in seen:
                        seen.add(tok)
                        found.append((tok, i + 1))
    return found


def mine_data_shape(repo_root: Path | None, question_ids: set[str]) -> dict | None:
    """Ground the field set of a named data blob directly from source.

    Blocking (subprocess grep + file reads) - call via ``asyncio.to_thread``.
    Tries each specific identifier the question named; the first that grounds
    wins. Returns ``None`` when nothing is grounded so the caller falls through
    to normal retrieval.

    Return shape::

        {
          "identifier": str,
          "fields": [str, ...],           # ordered, deduped, all from source
          "grounding": "docstring" | "access",
          "confidence": "high" | "medium",
          "sources": [{"file", "line", "kind"}],   # kind in {docstring, access}
          # docstring grounding only, and only when present: keys consumers read
          # beside a documented field that the doc omits (aliases / optional keys)
          "also_accessed": [{"field", "file", "line"}],
        }
    """
    if repo_root is None:
        return None
    try:
        root = Path(str(repo_root))
    except Exception:
        return None

    # Compile the repo's exclusion rules once per query. The grep fallbacks
    # (esp. ``git grep --no-index`` on a non-git tree) don't honour .gitignore,
    # so filter their hits the same way every other MCP read path does.
    exclude_spec = build_exclude_spec(root)

    for identifier in _specific_identifiers(question_ids):
        files = _grep_identifier_files(root, identifier, exclude_spec)
        if not files:
            continue
        # Scan documenting files first, then cap: the file that documents the
        # shape need not sort first among dozens of consumers.
        files = _order_candidates(files)[:_DATA_SHAPE_MAX_FILES]

        doc_hits: list[tuple[frozenset, list[str], str, int]] = []
        access_fields: list[str] = []
        access_sources: list[dict] = []
        access_seen: set[str] = set()
        file_lines: dict[str, list[str]] = {}  # cached for the divergence pass

        for rel in files:
            lines = _read_lines(root, rel)
            if not lines:
                continue
            mentions = _mention_lines(lines, identifier)
            if not mentions:
                continue
            file_lines[rel] = lines
            doc = _doc_shape_in_file(lines, identifier, mentions)
            if doc is not None:
                fields, line = doc
                doc_hits.append((frozenset(fields), fields, rel, line))
            for field, line in _accessed_fields_in_file(lines, identifier, mentions):
                if field not in access_seen:
                    access_seen.add(field)
                    access_fields.append(field)
                    access_sources.append({"file": rel, "line": line, "kind": "access"})

        # A documented shape is authoritative. When several files document a
        # shape, take the most common set (mode), tie-broken by size then path,
        # so one file's incidental brace can't override a repeated schema doc.
        if doc_hits:
            by_set: dict[frozenset, list[tuple[list[str], str, int]]] = {}
            for fset, fields, rel, line in doc_hits:
                by_set.setdefault(fset, []).append((fields, rel, line))

            def _rank(item: tuple[frozenset, list[tuple[list[str], str, int]]]):
                fset, group = item
                # Most files agreeing wins; then more fields; then the
                # alphabetically-first documenting path (deterministic).
                first_path = min(t[1] for t in group)
                return (len(group), len(fset), _neg_str(first_path))

            best_set, _ = max(by_set.items(), key=_rank)
            group = sorted(by_set[best_set], key=lambda t: t[1])
            fields = group[0][0]
            sources = [{"file": rel, "line": line, "kind": "docstring"} for _f, rel, line in group]
            # Corroborating access sites (same-named fields) strengthen the cite
            # trail without changing the authoritative doc field set.
            for src in access_sources:
                if any(src["file"] == s["file"] for s in sources):
                    continue
                sources.append(src)
            # Divergence: keys consumers read right beside a documented field but
            # the doc never lists (a legacy alias like ``count`` for
            # ``co_change_count``, an optional key). The documented shape is
            # authoritative for what it declares, but if we said "cite it, no Read
            # needed" while hiding a key four consumers defensively handle, an
            # agent could ship a change that ignores it. Surface it instead.
            doc_field_set = set(fields)
            also_accessed: list[dict] = []
            also_seen: set[str] = set()
            for rel, lines in file_lines.items():
                for key, line in _alias_keys_on_documented_lines(lines, doc_field_set):
                    if key not in also_seen:
                        also_seen.add(key)
                        also_accessed.append({"field": key, "file": rel, "line": line})
            result: dict = {
                "identifier": identifier,
                "fields": fields,
                "grounding": "docstring",
                "confidence": "high",
                "sources": sources,
            }
            if also_accessed:
                result["also_accessed"] = also_accessed
            return result

        # No documented shape - fall back to consistent key accesses.
        if len(access_fields) >= _DATA_SHAPE_MIN_FIELDS:
            return {
                "identifier": identifier,
                "fields": access_fields,
                "grounding": "access",
                "confidence": "medium",
                "sources": access_sources,
            }

    return None
