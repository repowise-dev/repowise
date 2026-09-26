"""Name the definitions a truncated symbol body did not serve.

Definition-line shapes across languages, and the scan over a withheld range
that reports them while skipping lines the string mask says are not code.
"""

from __future__ import annotations

import re
from pathlib import Path

from repowise.server.mcp_server.tool_answer.source import (
    _read_repo_text,
    _read_signature_from_source,
)
from repowise.server.mcp_server.tool_answer.string_mask import (
    _has_backtick_strings,
    _string_masked_lines,
)

# Leading keywords that decorate a declaration without being one. Shared by the
# keyword and brace-method shapes below.
_DECL_MODIFIERS = (
    r"pub|public|private|protected|internal|open|final|static|abstract|override|"
    r"virtual|sealed|export|default|async|suspend|inline|readonly|declare|"
    r"unsafe|extern|partial|data|operator|const"
)

# Definition-line shapes, tried in order; each yields ``indent`` / ``kind`` /
# ``name``. A Python-only regex was the first cut and it made this whole feature
# inert on TS/Go/Java — which, at a 120-line body cap, is exactly where
# truncation bites hardest, since a TS class or a React component is the shape
# that overruns the cap in the first place.
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
    # The separator after ``function`` is REQUIRED (or a generator star). With
    # ``[ \t]*`` the keyword matched as a mere prefix of a longer identifier, so
    # ``function_name: Mapped[str] = ...`` reported a symbol called ``_name``:
    # measured at 507 fabricated entries across this repo, the single largest
    # source of ids that resolve to nothing.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:export[ \t]+)?(?:default[ \t]+)?(?:async[ \t]+)?"
        r"(?P<kind>function)(?:[ \t]*\*[ \t]*|[ \t]+)(?P<name>[A-Za-z_$][\w$]*)"
    ),
    # JS/TS arrow bindings: ``export const Panel = (props) => {``. The arrow has
    # to belong to the binding itself, with only an optional return annotation
    # between: allowing slack before it turns every local initialised from a
    # callback-taking call (``const pages = all.filter((p) => ...)``) into a
    # "definition" with a symbol_id that resolves to nothing.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:export[ \t]+)?(?P<kind>const|let|var)[ \t]+"
        r"(?P<name>[A-Za-z_$][\w$]*)[^=]*=[ \t]*(?:async[ \t]+)?"
        r"(?:\([^)]*\)(?:[ \t]*:[^=]*?)?|[A-Za-z_$][\w$]*)[ \t]*=>"
    ),
    # Brace-language members: ``  public void run(String a) {``, ``  render() {``.
    # Between the parameter list and the brace only a return type or a throws
    # clause may appear -- no parens, or an assertion call in a test file
    # (``expect(x).toMatchObject({``) reads as a method declaration. The brace
    # itself is optional so Allman style (``void Beta()`` with ``{`` on the next
    # line, the C# default) is reachable; the caller supplies the next line.
    re.compile(
        rf"^(?P<indent>[ \t]*)(?:(?:{_DECL_MODIFIERS})[ \t]+)*"
        r"(?:[A-Za-z_$][\w$<>,.\[\]]*[ \t]+)?(?P<name>[A-Za-z_$][\w$]*)[ \t]*"
        r"\([^;{]*\)(?P<tail>[^;{()]*)(?P<brace>\{)?[ \t]*$"
    ),
)
_BRACE_MEMBER = len(_WITHHELD_DEF_PATTERNS) - 1

# The brace-member shape above also matches control flow (``if (x) {``), a
# statement whose keyword the optional type group swallows (``raise
# ValueError(f"... {x}")`` reports ``ValueError``), and any call taking a
# callback (``describe("x", () => {``, ``it("x", function () {``). Both the
# matched NAME and the line's own first word are checked, because the type group
# hides the keyword from a name-only guard.
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

# Words no language lets you NAME a definition, so a match producing one is a
# parse accident whatever shape it came from: ``fn is not None`` reads as Rust's
# ``fn <name>`` and reported a symbol called ``is``. Kept strictly to reserved
# words -- ``match``, ``range`` and ``print`` are all real function names.
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
        if not m:
            continue
        if m.group("name").lower() in _RESERVED_NAMES:
            continue
        if i == _BRACE_MEMBER:
            head = _FIRST_WORD_RE.match(raw.strip())
            if m.group("name").lower() in _NOT_A_DEFINITION:
                continue
            if head and head.group(0).lower() in _NOT_A_DEFINITION:
                continue
            # An anonymous function passed as an argument, in either syntax.
            if "=>" in raw[: m.end()] or "function" in raw[: m.end()]:
                continue
            # Go's third spelling of the same thing: ``func(req *http.Request)
            # (*http.Response, error) {``. There is no space after ``func``, so
            # the optional return-type group matches empty and the name group
            # takes the keyword itself, yielding an unresolvable ``path::func``.
            # This cannot be handled by either general-purpose set above:
            # ``_RESERVED_NAMES`` is tested for every pattern and ``def func():``
            # is a real Python definition (41 of them in django alone), while
            # ``_NOT_A_DEFINITION`` is also tested against the line's FIRST
            # word, which is ``func`` on every named Go function too.
            #
            # Requiring ``func`` to open the line is what keeps it to the Go
            # literal: ``int func(int a) {`` is a real definition named ``func``
            # in C, C++, Java, C# and Kotlin, and a name-only test suppresses
            # all five.
            if m.group("name") == "func" and head and head.group(0) == "func":
                continue
            # Allman: the brace is on the next line. A declaration never ends in
            # a comma, but an argument on its own line inside a multi-line call
            # does -- and when the following argument is a dict literal, the
            # next line really is ``{`` (``bool(matched_nums),`` then ``{``).
            if not m.group("brace") and (
                next_raw.strip() != "{" or raw.rstrip().endswith(",")
            ):
                continue
        return m
    return None


def _indent_width(raw: str) -> int:
    return len(raw) - len(raw.lstrip())


# Stand-in indent for "the cut is inside something still open", used when every
# withheld line is a string body or a bracket tail and none carries a real one.
_UNBOUNDED_INDENT = 1 << 30


# Cap on how many withheld definitions are surfaced. This block exists to let
# the agent CONTINUE inside the tool rather than fall back to Read, so it has to
# stay small enough that it never competes with the answer for the window: names
# and signatures only, never bodies.
_WITHHELD_MAX_SYMBOLS: int = 8


def withheld_definitions(
    repo_root: Path | None, continuation: str | None
) -> list[dict]:
    """Definitions that live in the range a truncated body did NOT serve.

    ``continuation`` is the ``path:first-last`` pointer already attached to a
    truncated ``symbol_bodies`` entry, so this reads exactly the lines the
    payload admits it withheld.

    Why this exists rather than just flagging the truncation: measured on the
    transcripts on disk, when a body is truncated the withheld range contains a
    symbol the answer goes on to talk about **78% of the time**, and the
    responses are at ``confidence: high`` in most of those. A flag alone does
    not help the consumer, and the ``get_symbol`` pointer the payload already
    carries was followed ZERO times across the runs measured. Names and
    signatures are cheap and keep the agent inside the tool.

    Returns ``[{name, kind, line, symbol_id, signature}]``, the boundary-cut
    symbol first, empty on any failure (a probe that cannot read must not
    manufacture doubt).
    """
    if not continuation:
        return []
    path, _, span = continuation.rpartition(":")
    first, _, last = span.partition("-")
    if not path or not first.isdigit() or not last.isdigit():
        return []
    lo, hi = int(first), int(last)
    text = _read_repo_text(repo_root, path)
    if text is None:
        return []
    lines = text.splitlines()
    if lo < 1 or lo > len(lines):
        return []
    mask = _string_masked_lines(tuple(lines), _has_backtick_strings(path))
    masked = mask.all

    def _entry(line_no: int, m: re.Match[str], *, cut: bool = False) -> dict:
        name = m.group("name")
        # The brace-member shape has no keyword to report, so it is named for
        # what it is rather than mislabelled as a Python `def`.
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

    out: list[dict] = []

    # The symbol whose body is CUT BY the boundary, which is the case that
    # motivated this whole helper and the one a naive implementation misses.
    # In the reference defect the served range ended at 166 and `_validate`
    # starts at 164: its `def` line was served, so it does not appear anywhere
    # in the withheld range, while the line that actually causes the bug (176)
    # sits inside it. Reporting only defs that START after the cut would say
    # nothing about the symbol the answer is about.
    #
    # Taking the nearest preceding definition unconditionally is wrong, though:
    # a symbol that ENDED before the cut was served whole, and reporting it as
    # continuing puts a fully-served name at the head of the note and into the
    # get_symbol pointer. A definition at indent I reaches line ``lo`` only if
    # every non-blank line from it up to the first non-blank withheld line is
    # indented deeper than I, so walking backwards while tracking the running
    # minimum indent decides it exactly, in one pass and with no re-scan.
    #
    # The anchor obeys the same two exclusions as the walk. Taking the first
    # non-blank withheld line flatly is what put the walk one line short of
    # reality: when the cut lands ON a multi-line signature's own ``) -> dict:``
    # (or on a flush-left docstring line), the anchor reads as column 0, the
    # walk dies at once, and the payload ships truncated with NO withheld
    # symbols -- gate 8 inert on exactly the long entry points that truncate.
    _end = min(hi, len(lines))
    _usable = [
        n
        for n in range(lo, _end + 1)
        if lines[n - 1].strip()
        and n not in masked
        and lines[n - 1].strip()[0] not in ")]}{"
    ]
    if lo in mask.strings:
        # The cut is INSIDE a multi-line string, so the expression holding that
        # string -- and everything enclosing it -- is still open at ``lo``.
        # Without this the anchor reads from the first line BELOW the string,
        # usually a top-level declaration at column 0, and the walk dies at once
        # (D9: 8 real definitions lost across cli/cli and mui). A block COMMENT
        # cannot stand in for this: one sitting between two methods would report
        # the preceding method as continuing when it has already ended.
        anchor = _UNBOUNDED_INDENT
    elif _usable:
        anchor = _indent_width(lines[_usable[0] - 1])
    elif any(lines[n - 1].strip() for n in range(lo, _end + 1)):
        # Every withheld line is a string body or a bracket tail, so whatever
        # encloses the cut is certainly still open: let any preceding
        # definition qualify.
        anchor = _UNBOUNDED_INDENT
    else:
        anchor = None
    if anchor is not None:
        min_indent = anchor
        for back in range(lo - 1, 0, -1):
            if min_indent <= 0:
                break  # nothing can be shallower, so nothing can still be open
            raw = lines[back - 1]
            stripped = raw.strip()
            if not stripped:
                continue
            # A line opening with a closing bracket is the tail of a multi-line
            # construct, not a statement at its own indent. Folding it is what
            # made this miss the live reference case: ``get_answer``'s signature
            # spans lines and ends ``) -> dict:`` at column 0, so the running
            # minimum hit zero on the signature's own closing paren and the walk
            # gave up two lines short of the ``async def`` it was looking for.
            # An Allman brace (``{`` alone) is likewise part of the declaration
            # above it, not a statement.
            if stripped[0] in ")]}{":
                continue
            ind = _indent_width(raw)
            nxt = lines[back] if back < len(lines) else ""
            m = None if back in masked else _match_definition(raw, nxt)
            if m is not None and ind < min_indent:
                out.append(_entry(back, m, cut=True))
                break
            min_indent = min(min_indent, ind)

    seen = {d["name"] for d in out}
    for offset, raw in enumerate(lines[lo - 1 : _end]):
        line_no = lo + offset
        if line_no in masked:
            continue
        nxt = lines[line_no] if line_no < len(lines) else ""
        m = _match_definition(raw, nxt)
        if m is None or m.group("name") in seen:
            continue
        seen.add(m.group("name"))
        out.append(_entry(line_no, m))
        if len(out) >= _WITHHELD_MAX_SYMBOLS:
            break
    return out
