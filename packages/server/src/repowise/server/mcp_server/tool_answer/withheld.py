"""Name the definitions a truncated symbol body did not serve.

Definition-line shapes across languages, and the scan over a withheld range
that reports them while skipping lines the string mask says are not code.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from repowise.server.mcp_server.tool_answer.source import (
    _read_repo_text,
    _read_signature_from_source,
)
from repowise.server.mcp_server.tool_answer.string_mask import (
    _has_backtick_strings,
    _Masked,
    _string_masked_lines,
)

# Leading keywords that decorate a declaration without being one. Shared by the
# keyword and brace-method shapes below.
_DECL_MODIFIERS = (
    r"pub|public|private|protected|internal|open|final|static|abstract|override|"
    r"virtual|sealed|export|default|async|suspend|inline|readonly|declare|"
    r"unsafe|extern|partial|data|operator|const"
)

# Definition-line shapes across languages, tried in order; each yields
# ``indent`` / ``kind`` / ``name``.
_WITHHELD_DEF_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Python: def / async def / class.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:async[ \t]+)?(?P<kind>def|class)[ \t]+"
        r"(?P<name>[A-Za-z_]\w*)"
    ),
    # Go methods, whose name follows the receiver: ``func (s *Store) Write(``.
    re.compile(
        r"^(?P<indent>[ \t]*)(?P<kind>func)[ \t]+\([^)]*\)[ \t]*"
        r"(?P<name>[A-Za-z_]\w*)"
    ),
    # Declaration keyword + name: Go func/type, Rust fn/struct/trait/impl/enum,
    # Java/C#/Kotlin class/interface/record, TS class/interface/type/enum.
    re.compile(
        rf"^(?P<indent>[ \t]*)(?:(?:{_DECL_MODIFIERS})[ \t]+)*"
        r"(?P<kind>func|fn|class|struct|interface|enum|trait|impl|record|type)"
        r"[ \t]+(?P<name>[A-Za-z_$][\w$]*)"
    ),
    # JS/TS function declarations, including ``export default`` and generators.
    # The separator after ``function`` is required, or ``function_name: ...``
    # would report a symbol called ``_name``.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:export[ \t]+)?(?:default[ \t]+)?(?:async[ \t]+)?"
        r"(?P<kind>function)(?:[ \t]*\*[ \t]*|[ \t]+)(?P<name>[A-Za-z_$][\w$]*)"
    ),
    # JS/TS arrow bindings: ``export const Panel = (props) => {``. The arrow must
    # belong to the binding, or ``const pages = all.filter((p) => ...)`` matches.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:export[ \t]+)?(?P<kind>const|let|var)[ \t]+"
        r"(?P<name>[A-Za-z_$][\w$]*)[^=]*=[ \t]*(?:async[ \t]+)?"
        r"(?:\([^)]*\)(?:[ \t]*:[^=]*?)?|[A-Za-z_$][\w$]*)[ \t]*=>"
    ),
    # Brace-language members: ``  public void run(String a) {``, ``  render() {``.
    # No parens after the parameter list, or ``expect(x).toMatchObject({``
    # matches. The brace is optional for Allman style; the caller supplies the
    # next line.
    re.compile(
        rf"^(?P<indent>[ \t]*)(?:(?:{_DECL_MODIFIERS})[ \t]+)*"
        r"(?:[A-Za-z_$][\w$<>,.\[\]]*[ \t]+)?(?P<name>[A-Za-z_$][\w$]*)[ \t]*"
        r"\([^;{]*\)(?P<tail>[^;{()]*)(?P<brace>\{)?[ \t]*$"
    ),
)
_BRACE_MEMBER = len(_WITHHELD_DEF_PATTERNS) - 1

# Words that make a brace-member match control flow or a statement rather than
# a definition. Checked against the matched name and the line's first word,
# since the optional type group can swallow the keyword (``raise X(...{``).
_NOT_A_DEFINITION = frozenset(
    {
        "if", "for", "while", "switch", "catch", "else", "do", "try", "return",
        "with", "using", "lock", "foreach", "case", "synchronized", "await",
        "yield", "new", "typeof", "in", "of", "when", "unless", "match",
        "raise", "throw", "assert", "del", "delete", "print", "elif", "except",
        "finally", "import", "from", "global", "nonlocal", "pass", "break",
        "continue", "go", "defer", "select", "range", "constructor",
    }
)

_FIRST_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")

# Words no language lets you name a definition (``fn is not None`` is not Rust).
# Strictly reserved words: ``match``, ``range`` and ``print`` are real names.
_RESERVED_NAMES = frozenset(
    {
        "is", "not", "and", "or", "in", "if", "else", "elif", "for", "while",
        "return", "none", "true", "false", "null", "undefined", "class", "def",
        "import", "from", "as", "with", "pass", "lambda", "del", "global",
        "raise", "try", "except", "finally", "yield", "await", "assert",
        "break", "continue", "nonlocal", "var", "let", "const", "function",
    }
)


def _match_definition(raw: str, next_raw: str = "") -> re.Match[str] | None:
    """First definition shape *raw* matches, or None.

    ``next_raw`` is the following source line, consulted only for Allman-style
    braces where the declaration and its ``{`` sit on separate lines.
    """
    for i, pattern in enumerate(_WITHHELD_DEF_PATTERNS):
        m = pattern.match(raw)
        if not m or m.group("name").lower() in _RESERVED_NAMES:
            continue
        if i == _BRACE_MEMBER and _not_a_brace_member(raw, next_raw, m):
            continue
        return m
    return None


def _not_a_brace_member(raw: str, next_raw: str, m: re.Match[str]) -> bool:
    """Whether a brace-member match is a statement or call that only looks like one."""
    head = _FIRST_WORD_RE.match(raw.strip())
    if m.group("name").lower() in _NOT_A_DEFINITION:
        return True
    if head and head.group(0).lower() in _NOT_A_DEFINITION:
        return True
    # An anonymous function passed as an argument, in either syntax.
    if "=>" in raw[: m.end()] or "function" in raw[: m.end()]:
        return True
    # A Go function literal, ``func(req *http.Request) (...) {``. Neither set
    # above fits: ``func`` is a real name elsewhere (``int func(int a) {``), so
    # only a line that opens with it is the Go literal.
    if m.group("name") == "func" and head and head.group(0) == "func":
        return True
    # Allman: the brace is on the next line. A trailing comma means a call
    # argument followed by a dict literal, not a declaration.
    return not m.group("brace") and (
        next_raw.strip() != "{" or raw.rstrip().endswith(",")
    )


def _indent_width(raw: str) -> int:
    return len(raw) - len(raw.lstrip())


# Stand-in indent for "the cut is inside something still open", used when every
# withheld line is a string body or a bracket tail and none carries a real one.
_UNBOUNDED_INDENT = 1 << 30


# Cap on withheld definitions surfaced: names and signatures only, small enough
# never to compete with the answer.
_WITHHELD_MAX_SYMBOLS: int = 8


def withheld_definitions(
    repo_root: Path | None, continuation: str | None
) -> list[dict]:
    """Definitions that live in the range a truncated body did NOT serve.

    ``continuation`` is the ``path:first-last`` pointer already attached to a
    truncated ``symbol_bodies`` entry, so this reads exactly the lines the
    payload admits it withheld.

    More than a truncation flag: the withheld range often holds the symbol the
    answer is about, and names and signatures are cheap.

    Returns ``[{name, kind, line, symbol_id, signature}]``, the boundary-cut
    symbol first, empty on any failure (a probe that cannot read must not
    manufacture doubt).
    """
    span = _parse_continuation(continuation)
    if span is None:
        return []
    path, lo, hi = span
    text = _read_repo_text(repo_root, path)
    if text is None:
        return []
    lines = text.splitlines()
    if lo < 1 or lo > len(lines):
        return []
    mask = _string_masked_lines(tuple(lines), _has_backtick_strings(path))
    end = min(hi, len(lines))

    out: list[dict] = []
    anchor = _cut_anchor_indent(lines, lo, end, mask)
    cut = _definition_cut_by_boundary(lines, lo, anchor, mask.all)
    if cut is not None:
        out.append(_withheld_entry(repo_root, path, text, *cut, cut=True))

    seen = {d["name"] for d in out}
    for line_no, m in _definitions_starting_in(lines, lo, end, mask.all):
        if m.group("name") in seen:
            continue
        seen.add(m.group("name"))
        out.append(_withheld_entry(repo_root, path, text, line_no, m))
        if len(out) >= _WITHHELD_MAX_SYMBOLS:
            break
    return out


def _parse_continuation(continuation: str | None) -> tuple[str, int, int] | None:
    """``(path, first, last)`` from a ``path:first-last`` pointer, or None."""
    if not continuation:
        return None
    path, _, span = continuation.rpartition(":")
    first, _, last = span.partition("-")
    if not path or not first.isdigit() or not last.isdigit():
        return None
    return path, int(first), int(last)


def _withheld_entry(
    repo_root: Path | None,
    path: str,
    text: str,
    line_no: int,
    m: re.Match[str],
    *,
    cut: bool = False,
) -> dict:
    name = m.group("name")
    # The brace-member shape has no keyword to report.
    kind = m.groupdict().get("kind") or "member"
    sig = _read_signature_from_source(repo_root, path, line_no, text=text)
    e = {
        "name": name,
        "kind": kind,
        "line": line_no,
        "symbol_id": f"{path}::{name}",
        "signature": (sig or f"{kind} {name}").strip(),
    }
    if cut:
        e["body_continues"] = True
    return e


def _cut_anchor_indent(lines: list[str], lo: int, end: int, mask: _Masked) -> int | None:
    """The indent a definition above ``lo`` must undercut to still be open there.

    None when every withheld line is blank, so nothing can be continuing.
    """
    # Same exclusions as the walk: a cut on a signature's ``) -> dict:`` or a
    # flush-left docstring line would otherwise read as column 0.
    usable = [
        n
        for n in range(lo, end + 1)
        if lines[n - 1].strip()
        and n not in mask.all
        and lines[n - 1].strip()[0] not in ")]}{"
    ]
    if lo in mask.strings:
        # A cut inside a multi-line string leaves everything enclosing it open.
        # Not so for a block comment, which can sit between two methods.
        return _UNBOUNDED_INDENT
    if usable:
        return _indent_width(lines[usable[0] - 1])
    if any(lines[n - 1].strip() for n in range(lo, end + 1)):
        # Only string bodies and bracket tails: whatever encloses the cut is open.
        return _UNBOUNDED_INDENT
    return None


def _definition_cut_by_boundary(
    lines: list[str], lo: int, anchor: int | None, masked: frozenset[int]
) -> tuple[int, re.Match[str]] | None:
    """The definition above ``lo`` whose body the cut splits, if any.

    Its ``def`` line was served, so it appears nowhere in the withheld range,
    yet its body continues into it. Not simply the nearest preceding definition:
    one that ended before the cut was served whole. A definition at indent I
    reaches ``lo`` only if every non-blank line after it is deeper than I, so a
    backward walk tracking the minimum indent decides it in one pass.
    """
    if anchor is None:
        return None
    min_indent = anchor
    for back in range(lo - 1, 0, -1):
        if min_indent <= 0:
            break  # nothing can be shallower, so nothing can still be open
        raw = lines[back - 1]
        stripped = raw.strip()
        if not stripped:
            continue
        # A closing-bracket line is the tail of a multi-line construct (e.g. a
        # signature's ``) -> dict:``), and an Allman ``{`` belongs to the
        # declaration above: neither is a statement at its own indent.
        if stripped[0] in ")]}{":
            continue
        ind = _indent_width(raw)
        nxt = lines[back] if back < len(lines) else ""
        m = None if back in masked else _match_definition(raw, nxt)
        if m is not None and ind < min_indent:
            return back, m
        min_indent = min(min_indent, ind)
    return None


def _definitions_starting_in(
    lines: list[str], lo: int, end: int, masked: frozenset[int]
) -> Iterator[tuple[int, re.Match[str]]]:
    """Every unmasked definition line in ``lo..end``, in order."""
    for offset, raw in enumerate(lines[lo - 1 : end]):
        line_no = lo + offset
        if line_no in masked:
            continue
        nxt = lines[line_no] if line_no < len(lines) else ""
        m = _match_definition(raw, nxt)
        if m is not None:
            yield line_no, m
