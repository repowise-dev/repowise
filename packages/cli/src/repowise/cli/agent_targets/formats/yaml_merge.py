"""A deliberately narrow YAML writer, scoped to one nested key at a time.

The same shape as :mod:`toml_merge` and for the same reason: the edit is
surgery on the *source text*, not a parse-and-reserialize, so the user's
comments, key order and formatting survive everywhere outside the one key we
own. Only the value being written is rendered fresh.

**Why not round-trip the whole document.** ``yaml.safe_load`` followed by
``yaml.safe_dump`` is four lines and it is wrong here in three separate ways,
the last of which is not recoverable:

* It deletes every comment in the file. These configs are commonly seeded from
  a heavily annotated example, and the annotations are most of the file.
* It reformats everything: quoting style, flow versus block, long scalars
  folded at a different column. A one-key change becomes a whole-file diff.
* **It expands anchors and merge keys.** ``safe_load`` resolves ``&defaults``
  / ``*defaults`` / ``<<:`` into the values they stand for, and ``safe_dump``
  then writes those values out in full. A config factored with anchors comes
  back structurally different and much larger, and the factoring is gone for
  good. Nothing about that failure is visible in a diff of the parsed data,
  which is what makes it worse than the other two.

**Why not decline the way the JSONC path does.** ``vscode``, ``cursor`` and
``opencode`` refuse to rewrite their config when it will not parse, because
comments make it unparseable and there is nothing to be done about that
without a comment-preserving parser. YAML has no such problem: comments are
part of the grammar, so a commented file parses cleanly and we can read it
exactly. Declining here would be refusing work we are able to do correctly.

**Why the surgery is safe despite being line-based.** It is not trusted. The
caller computes the document it *means* to end up with, from the parsed
original, and :func:`verify` re-parses the spliced text and compares. A shape
this module's line matching gets wrong -- a flow mapping, a duplicate top-level
key, an unusual indent -- lands the edit somewhere that changes the document,
and the write is refused with the file untouched. So a mis-fired splice fails
closed. That is a stronger guarantee than enumerating the shapes in advance,
because the enumeration is the part that would be wrong.

The guarantee is about *meaning*, and it stops there. Comments and blank lines
do not survive into the parse, so nothing checks them mechanically; they are
preserved because the edit is confined to one key's own lines, and the tests
that cover them assert on the text. Inputs that cannot be parsed at all --
tabs, several documents in one file -- are refused before any of this, by
:func:`load_mapping`.

Two primitives are enough for every edit a target needs: :func:`set_child` and
:func:`remove_child`. Adding an item to a nested list is ``set_child`` with the
new list, so there is no third code path for it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..types import FileAction
from .json_merge import atomic_write_text, json_deep_equal

#: Indent used for a child under a parent whose block is empty, so there is no
#: sibling to copy. Two spaces is what PyYAML's dumper emits by default, which
#: is what these hosts write their own configs with.
DEFAULT_INDENT = 2


def detect_newline(text: str) -> str:
    """The line ending *text* is written with, for writing it back the same way.

    Every function in this module works in ``\\n`` internally, so a caller
    normalises on the way in and passes this back to :func:`write_if_changed`
    on the way out. Skipping that step is a whole-file diff rather than a
    cosmetic one: the platform translation an ordinary config write uses would
    rewrite every line of a user's LF config to CRLF on Windows, on an edit
    that touched three lines. The whole point of splicing rather than
    reserializing is that untouched lines stay untouched, and the line endings
    are part of that.

    A mixed file resolves to CRLF and is normalised, which is the one case this
    does not round-trip. Picking the majority style instead would leave the
    minority lines alone and produce a file that is still mixed, which is not a
    better answer for anyone.
    """
    return "\r\n" if "\r\n" in text else "\n"


def load_mapping(text: str) -> dict:
    """Parse *text* as a YAML mapping.

    Raises ``ValueError`` for anything that is not one, so a caller can catch
    it beside the other reasons a config is unusable. ``yaml.YAMLError`` is not
    a ``ValueError``, and letting it through would escape every handler in the
    targets, which all catch ``(OSError, ValueError)``.

    An empty file is an empty mapping rather than an error: YAML parses it to
    ``None``, and a config file that exists and says nothing is a perfectly
    ordinary thing to find.
    """
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML ({exc.__class__.__name__})") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("top level must be a YAML mapping")
    return data


def render_child(key: str, value: Any, indent: int, *, flow: bool = False) -> list[str]:
    """Render ``key: value`` as block YAML, indented by *indent* spaces.

    The dumper does the serializing rather than a hand-written encoder. That is
    the one place this module differs from :mod:`toml_merge`, and deliberately:
    the narrow TOML encoder had to grow inline tables and floats the moment it
    met a real config, because rewriting a whole entry means re-rendering
    everything the user put in it. A server entry here can legally hold a
    nested ``env`` mapping, an ``args`` list, a ``tools.include`` list and
    arbitrary scalars, and the dumper already handles all of it correctly.

    ``width`` is set past any real line length on purpose. The default folds
    long scalars at around 80 columns, and it folds on spaces -- so an absolute
    repo path containing a space would be wrapped across two lines. It still
    parses back to the same string, but it is unreadable in a config file and
    it makes the rendering depend on how long the user's path happens to be.

    *flow* renders ``key: [a, b]`` instead of a block sequence. Callers pass
    what the key was already written as, so editing one item of a user's inline
    list gives back an inline list rather than silently restyling the line.
    Block is the default because it is what the dumper and these hosts emit.

    The flow branch dumps the **value** alone rather than passing
    ``default_flow_style=True`` for the whole pair. That flag applies to the
    enclosing mapping too, so the pair comes back as ``{key: [a, b]}`` -- still
    valid YAML and still the right document, which is why nothing downstream
    catches it, but the key is then inside a flow mapping where no line-based
    search will find it again. The next edit appends a second copy instead of
    replacing it.
    """
    import yaml

    pad = " " * indent
    if flow and isinstance(value, (list, dict)):
        inline = yaml.safe_dump(
            value,
            default_flow_style=True,
            sort_keys=False,
            allow_unicode=True,
            width=1_000_000,
        ).strip()
        return [f"{pad}{key}: {inline}"]

    dumped = yaml.safe_dump(
        {key: value},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=1_000_000,
    )
    return [pad + line if line else line for line in dumped.rstrip("\n").split("\n")]


def _is_top_level_key(line: str) -> bool:
    """Whether *line* starts a new top-level node, ending the block above it.

    Blank lines and comments do not, because both appear freely inside a block
    and neither closes it. Everything else at column zero does, including a
    document separator, which is exactly right: this module must never edit
    across one.
    """
    if not line.strip():
        return False
    return not line.startswith((" ", "\t", "#"))


def _find_top_level(lines: list[str], key: str) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of top-level *key*, or ``None`` when absent.

    ``start`` is the ``key:`` line itself. Anchored at column zero, so a key of
    the same name nested inside another mapping, or sitting inside a commented
    example block, is not mistaken for this one.
    """
    pattern = re.compile(r"^" + re.escape(key) + r":(?=\s|$)")
    for index, line in enumerate(lines):
        if pattern.match(line):
            end = index + 1
            while end < len(lines) and not _is_top_level_key(lines[end]):
                end += 1
            return index, end
    return None


def _child_indent(lines: list[str], start: int, end: int) -> int:
    """Indent the children of the block at *start* are written at."""
    for index in range(start + 1, end):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        return len(line) - len(line.lstrip())
    return DEFAULT_INDENT


def _find_child(lines: list[str], start: int, end: int, child: str, indent: int) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of *child* inside its parent's block."""
    pad = " " * indent
    pattern = re.compile(r"^" + re.escape(pad + child) + r":(?=\s|$)")
    for index in range(start + 1, end):
        if pattern.match(lines[index]):
            return index, _child_end(lines, index, end, indent)
    return None


def _child_end(lines: list[str], child_start: int, end: int, indent: int) -> int:
    """Where the block belonging to the child at *child_start* stops.

    Two rules beyond "a line indented further belongs to the child":

    * **A ``- `` item at the child's own indent belongs to the child.** That is
      not an exotic hand-written style, it is what PyYAML emits for a block
      sequence with ``default_flow_style=False``, so it is the shape these
      hosts write their own lists in. Reading it as the next sibling truncates
      the block after its first line, and an edit then lands above items that
      are still there.
    * **Trailing blank and comment lines are given back.** A comment sitting
      between two keys documents the one below it, so swallowing it into the
      block above would move or delete the user's comment when that block is
      replaced.
    """
    cursor = child_start + 1
    while cursor < end:
        line = lines[cursor]
        if line.strip() and not line.lstrip().startswith("#"):
            line_indent = len(line) - len(line.lstrip())
            is_own_list_item = line_indent == indent and line.lstrip().startswith("- ")
            if line_indent <= indent and not is_own_list_item:
                break
        cursor += 1
    while cursor > child_start + 1 and (
        not lines[cursor - 1].strip() or lines[cursor - 1].lstrip().startswith("#")
    ):
        cursor -= 1
    return cursor


def _trailing_comment(line: str) -> str:
    """A comment sitting on the key's own line, or ``""``.

    Replacing a child replaces the whole of its first line, and that line can
    carry a comment describing the value being replaced -- ``cli: [a, b]  # an
    allowlist`` is exactly the shape this module meets. Losing it would be the
    one place a module built to preserve comments silently ate one.

    Conservative on purpose: the comment is only recovered when nothing after
    the colon is quoted. A ``#`` inside a quoted scalar is not a comment, and
    telling the two apart properly means tokenising the line. Skipping the
    recovery there costs a comment in a rare shape; getting it wrong would move
    a fragment of the user's data into a comment. The wrong direction is not
    worth the coverage.
    """
    _, separator, rest = line.partition(":")
    if not separator or '"' in rest or "'" in rest:
        return ""
    marker = rest.find("#")
    return rest[marker:].rstrip() if marker != -1 else ""


def _is_flow_value(line: str) -> bool:
    """Whether the key on *line* already holds an inline ``[...]`` or ``{...}``.

    Editing one item of a user's inline list should give back an inline list.
    Re-rendering it as a block sequence is a correct document and a gratuitous
    diff, and on uninstall it would mean the value came back while the
    formatting did not.
    """
    _, separator, rest = line.partition(":")
    return bool(separator) and rest.lstrip().startswith(("[", "{"))


def _split(text: str) -> list[str]:
    return text.split("\n")


def _join(lines: list[str]) -> str:
    joined = "\n".join(lines)
    if joined and not joined.endswith("\n"):
        joined += "\n"
    return joined


def set_child(text: str, parent: str, child: str, value: Any) -> str:
    """Return *text* with top-level ``parent``'s ``child`` set to *value*.

    Creates the parent block at the end of the file when it is absent, replaces
    the child in place when it is present, and appends it to the parent's block
    otherwise. Everything outside the child's own lines is returned untouched.
    """
    lines = _split(text)
    found = _find_top_level(lines, parent)

    if found is None:
        rendered = [f"{parent}:", *render_child(child, value, DEFAULT_INDENT)]
        body = text.rstrip("\n")
        prefix = f"{body}\n\n" if body else ""
        return prefix + "\n".join(rendered) + "\n"

    start, end = found

    if _is_flow_value(lines[start]):
        return _set_child_in_flow_parent(lines, start, end, parent, child, value)

    indent = _child_indent(lines, start, end)

    existing = _find_child(lines, start, end, child, indent)
    if existing is not None:
        child_start, child_end = existing
        rendered = render_child(
            child, value, indent, flow=_is_flow_value(lines[child_start])
        )
        comment = _trailing_comment(lines[child_start])
        if comment:
            rendered = [f"{rendered[0]}  {comment}", *rendered[1:]]
        return _join(lines[:child_start] + rendered + lines[child_end:])

    rendered = render_child(child, value, indent)

    # Append inside the parent's block, above any blank or comment lines that
    # trail it, so a comment introducing the next top-level section keeps its
    # blank line and stays attached to that section rather than to ours.
    #
    # Comments count here, not only blanks. The parent's range runs to the next
    # top-level key, and a heading written above that key sits inside the range,
    # so stopping at the first blank line inserts our entry *below* the heading:
    # the user's section title ends up indented inside our block and the section
    # it introduced loses it. ``_child_end`` walks back over both for the same
    # reason. The cost when a trailing comment really did belong to the parent
    # is that our entry lands above it, which loses nothing.
    insert_at = end
    while insert_at > start + 1 and (
        not lines[insert_at - 1].strip() or lines[insert_at - 1].lstrip().startswith("#")
    ):
        insert_at -= 1
    return _join(lines[:insert_at] + rendered + lines[insert_at:])


def _set_child_in_flow_parent(
    lines: list[str], start: int, end: int, parent: str, child: str, value: Any
) -> str:
    """Rewrite a flow-mapping parent as a block mapping carrying the new child.

    ``mcp_servers: {}`` is an ordinary way to write a section that exists and
    holds nothing yet, and ``mcp_servers: {github: {...}}`` is legal too. Neither
    can be spliced into as a block: the child would be indented under a value
    that is already closed, producing text that does not parse. Before this, both
    reached :func:`verify`, failed, and surfaced ``not valid YAML`` about a file
    that is perfectly valid, with no way forward but hand-editing.

    Nothing is lost by re-rendering: a flow mapping is one line, so the only
    thing to carry across is a comment on it.

    One asymmetry worth stating, because it is the single case that does not
    round-trip byte for byte. Converting ``parent: {}`` to a block mapping and
    then removing the child again leaves the key gone rather than empty, since
    by then there is no flow syntax left to restore. Both spellings mean the
    same thing to the hosts that read them, and the alternative was declining
    the install outright and forever.
    """
    import yaml

    try:
        parsed = yaml.safe_load("\n".join(lines[start:end]))
    except yaml.YAMLError:
        parsed = None
    current = parsed.get(parent) if isinstance(parsed, dict) else None
    if current is not None and not isinstance(current, dict):
        # A flow *sequence* is not a mapping we can add a child to. Leave the
        # lines alone and let ``verify`` refuse, which is the honest outcome.
        return _join(lines)

    merged = {**(current or {}), child: value}
    rendered = render_child(parent, merged, 0)
    comment = _trailing_comment(lines[start])
    if comment:
        rendered = [f"{rendered[0]}  {comment}", *rendered[1:]]
    return _join(lines[:start] + rendered + lines[end:])


def remove_child(text: str, parent: str, child: str) -> tuple[str, bool]:
    """Remove top-level ``parent``'s ``child``. Returns ``(text, parent_kept)``.

    The parent goes with it once the block holds nothing at all, so an install
    followed by an uninstall leaves no bare ``parent:`` behind.

    **A block still holding the user's comments is not empty**, and that is why
    this reports back rather than just returning text. Deleting a parent whose
    only remaining lines are comments takes those comments with it, and the
    parse cannot see the difference: comments do not survive into the document,
    so :func:`verify` would wave it through. A user who commented out a parked
    server inside ``mcp_servers`` would lose the note explaining why.

    So the comments stay and the bare ``parent:`` stays with them, which parses
    to ``None`` rather than to an absent key. The caller has to know which of
    the two happened to state the document it intends, which is what the second
    element is for. Both hosts this matters for read a null section as empty.

    A no-op returning ``(text, True)`` when either key is absent: nothing was
    removed, so nothing about the parent changed.
    """
    lines = _split(text)
    found = _find_top_level(lines, parent)
    if found is None:
        return text, True

    start, end = found
    indent = _child_indent(lines, start, end)
    existing = _find_child(lines, start, end, child, indent)
    if existing is None:
        return text, True

    child_start, child_end = existing
    remaining = lines[:child_start] + lines[child_end:]

    # Re-locate the parent in the shortened file rather than reusing the old
    # offsets: removing the child moved every line after it.
    refound = _find_top_level(remaining, parent)
    if refound is None:
        return _join(remaining), False

    new_start, new_end = refound
    body = remaining[new_start + 1 : new_end]
    if any(line.strip() for line in body):
        return _join(remaining), True
    return _join(remaining[:new_start] + remaining[new_end:]), False


def verify(merged_text: str, expected: dict) -> None:
    """Refuse a splice whose result is not the document the caller meant.

    This is what makes line-based surgery safe to ship. The caller builds
    *expected* from the parsed original, so any shape the matching above did
    not understand shows up here as a mismatch, and the caller declines with
    the file untouched instead of writing something it did not intend.

    **What it does not cover, stated plainly so nobody leans on it further than
    it goes.** The comparison is between *documents*, so it proves the file
    will mean what the caller intended and nothing more. A splice that dropped
    only a comment or a blank line produces the same document and passes here.
    Comment preservation is a property of the surgery being minimal, not
    something this check enforces, and the tests that cover it assert on the
    text rather than on the parse.

    Raises ``ValueError`` on a mismatch, so it lands in the same handler as an
    unreadable file.
    """
    actual = load_mapping(merged_text)
    if not json_deep_equal(actual, expected):
        raise ValueError(
            "the merged YAML did not match the intended config, so nothing was written"
        )


def write_if_changed(
    config_path: Path,
    merged_text: str,
    merged_doc: dict,
    existing_doc: dict | None,
    *,
    newline: str | None = None,
) -> FileAction:
    """Write *merged_text* only when it would change what the file means.

    Documents rather than bytes, for the reason :func:`toml_merge.write_if_changed`
    gives: a file that already says the right thing is left exactly as the user
    has it, including a hand-written flow-style entry that this module would
    otherwise reformat into block style for no gain. It is also what makes a
    re-run report :attr:`~..types.FileAction.UNCHANGED` rather than an update it
    did not make.

    *existing_doc* is ``None`` when the file is new. *newline* should be
    :func:`detect_newline` of the original text for an existing file, and
    ``None`` for a new one so it takes the platform's ending the way every
    other config writer here does.
    """
    if existing_doc is not None and json_deep_equal(merged_doc, existing_doc):
        return FileAction.UNCHANGED
    action = FileAction.UPDATED if existing_doc is not None else FileAction.CREATED
    atomic_write_text(config_path, merged_text, newline=newline)
    return action
