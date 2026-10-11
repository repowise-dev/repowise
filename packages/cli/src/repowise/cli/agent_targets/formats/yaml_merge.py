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


def _key_pattern(key: str, indent: str) -> re.Pattern[str]:
    """Match *key* at *indent*, bare or in either quote.

    Quoting a mapping key is ordinary YAML. Teaching only one of the two
    searches about it is worse than teaching neither: the child search learned
    the quoted spellings first, and the parent search left behind then sent a
    ``"mcp_servers":`` config down the append path, where the second block it
    added was a duplicate key and the write refused for good.
    """
    name = re.escape(key)
    return re.compile(
        rf'^{re.escape(indent)}(?:{name}|"{name}"|\'{name}\'):(?=\s|$)'
    )


def _find_top_level(lines: list[str], key: str) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of top-level *key*, or ``None`` when absent.

    ``start`` is the ``key:`` line itself. Anchored at column zero, so a key of
    the same name nested inside another mapping, or sitting inside a commented
    example block, is not mistaken for this one.
    """
    pattern = _key_pattern(key, "")
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
    """Line range ``[start, end)`` of *child* inside its parent's block.

    All three spellings of the key are matched, bare and either quote. Quoting a
    mapping key is ordinary YAML and it is what a user pasting an entry by hand
    tends to produce, and matching only the bare form was worse than it looks in
    both directions: the write appended a *second* entry beside the quoted one,
    leaving a duplicate key that the parser silently resolves to the last, and
    the removal then found neither and declined for good.

    The replacement is written bare, which changes the quoting of the one key
    repowise owns and nothing else.
    """
    pattern = _key_pattern(child, " " * indent)
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

    **The parser decides where the value ends, not a scanner here.** Three
    hand-written rules were tried and each was wrong in its own direction: one
    keyed on quoting copied a fragment of an inline value out into a comment,
    one searching back from the last bracket deleted any comment that mentioned
    a brace, and one tracking quotes and bracket depth left to right desynced on
    an apostrophe in a plain scalar (``note: don't``) and did both. Knowing
    where a YAML value stops means implementing YAML, so this asks the library
    that already has: the value node's end mark is the answer, and anything
    after it on the line is the comment.

    *text* may span several lines, which is what the inline path needs; the
    value ends on the last of them. Anything that will not compose as a single
    ``key: value`` pair has no comment this can safely identify, so it reports
    none.
    """
    import yaml

    try:
        node = yaml.compose(line)
    except yaml.YAMLError:
        return ""
    if not isinstance(node, yaml.MappingNode) or len(node.value) != 1:
        return ""
    tail = line[node.value[0][1].end_mark.index :]
    marker = tail.find("#")
    return tail[marker:].rstrip() if marker != -1 else ""


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
    # A comment counts only when it is written at the children's own indent or
    # further left. One indented deeper belongs to the child above it -- a note
    # under ``github``'s last key, say -- and stepping over that would move it
    # inside our block, where it reads as documenting our entry instead. The
    # same rule keeps a ``#`` line that is *content* rather than a comment, a
    # literal line inside a ``key: |`` block scalar, from being stepped over at
    # all, which would otherwise truncate the scalar.
    insert_at = end
    while insert_at > start + 1:
        candidate = lines[insert_at - 1]
        if not candidate.strip():
            insert_at -= 1
            continue
        stripped = candidate.lstrip()
        if stripped.startswith("#") and len(candidate) - len(stripped) <= indent:
            insert_at -= 1
            continue
        break
    return _join(lines[:insert_at] + rendered + lines[insert_at:])


def _set_child_in_flow_parent(
    lines: list[str], start: int, end: int, parent: str, child: str, value: Any
) -> str:
    """Add a child to a parent whose value is written inline, keeping it inline.

    ``mcp_servers: {}`` is an ordinary way to write a section that exists and
    holds nothing yet, and ``mcp_servers: {github: {...}}`` is legal too. Neither
    can be spliced into as a block: the child would be indented under a value
    that is already closed, producing text that does not parse. Both used to
    reach :func:`verify`, fail, and surface ``not valid YAML`` about a file that
    is perfectly valid, with no way forward but hand-editing.

    **Exactly one line is replaced**, not the parent's whole range. The range
    runs to the next top-level key, so it carries every blank line and comment
    between the inline value and the next section, and replacing all of it
    deleted them. That is the same mistake :func:`set_child`'s walk-back exists
    to avoid, met again on a path that did not inherit the rule, and the parse
    cannot see it because the document is identical either way.

    The value is re-rendered **inline**, so a parent written as one line stays
    one line. What that costs is stated in :func:`_flow_span`: the value's own
    formatting is normalised by the dumper, and a value carrying an anchor is
    refused rather than re-rendered.
    """
    span = _flow_span(lines, start, end, parent)
    if span is None:
        return _join(lines)
    stop, current = span
    if current is not None and not isinstance(current, dict):
        # A flow *sequence* is not a mapping we can add a child to.
        return _join(lines)

    merged = {**(current or {}), child: value}
    rendered = render_child(parent, merged, 0, flow=True)
    comment = _trailing_comment("\n".join(lines[start:stop]))
    if comment:
        rendered = [f"{rendered[0]}  {comment}", *rendered[1:]]
    return _join(lines[:start] + rendered + lines[stop:])


def _flow_span(
    lines: list[str], start: int, end: int, parent: str
) -> tuple[int, object] | None:
    """Where the inline value ends, and what it holds. ``None`` to leave it alone.

    An inline value may wrap across lines, so the shortest slice from *start*
    that parses is the value: an unbalanced flow collection does not parse, so
    the first slice that does is the whole of it. Reading only the first line
    looks equivalent and turned a wrapped value from a working install into a
    permanent refusal.

    **A value carrying an anchor or an alias is refused.** Adding a child means
    re-rendering the whole inline value, and the dumper writes anchors back out
    under generated names, so ``{a: &base {...}, b: *base}`` returns with the
    name replaced. Every other thing re-rendering normalises here is cosmetic
    (quote style, the spaces inside the braces) and this one is not: the module
    header calls losing a factoring the one damage that cannot be undone, so
    the same rule that keeps this module off a whole-file round trip keeps it
    off these values.
    """
    import yaml

    for stop in range(start + 1, end + 1):
        text = "\n".join(lines[start:stop])
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict) or parent not in parsed:
            return None
        if _has_anchors(text):
            return None
        if stop > start + 1 and "#" in text:
            # A wrapped value is re-rendered onto one line, so a comment living
            # *inside* it has nowhere to go. Only the one following the value is
            # recoverable. Rather than eat the rest, refuse the value: a hash
            # anywhere in a wrapped inline collection is either a comment worth
            # keeping or data that says the shape is fussier than this module
            # should be editing. The single-line case is unaffected, and it is
            # the common one.
            return None
        return stop, parsed.get(parent)
    return None


def _has_anchors(text: str) -> bool:
    """Whether *text* declares an anchor or uses an alias.

    Asked of the parser rather than by looking for ``&`` and ``*`` in the text,
    which appear freely inside ordinary scalars: a shell argument like
    ``sh -c 'ls *'`` would otherwise read as an alias and refuse a config that
    is fine.
    """
    import yaml

    try:
        for event in yaml.parse(text):
            if isinstance(event, yaml.events.AliasEvent):
                return True
            if getattr(event, "anchor", None):
                return True
    except yaml.YAMLError:
        return True
    return False


#: Returned by :func:`remove_child` when the parent key is gone from the file.
#: A sentinel rather than ``None``, because ``None`` is a value the key can
#: legitimately hold: a bare ``parent:`` parses to it. Conflating the two makes
#: the caller state the wrong document and its own :func:`verify` reject a
#: removal that was correct.
ABSENT = object()


def remove_child(text: str, parent: str, child: str) -> tuple[str, object]:
    """Remove top-level ``parent``'s ``child``.

    Returns ``(text, value)``, where *value* is what the parent key holds
    afterwards, or :data:`ABSENT` when the key is gone. The caller cannot work
    that out from the text and has to state it to describe the document it
    means to write, so it is reported rather than left to be guessed.

    **It is reported from what this function did, never by re-reading its own
    output.** Re-parsing the spliced text to answer the question looks like the
    same answer and is not: the caller feeds the value straight into the
    document it hands :func:`verify`, so both sides of that comparison would
    come from one parse of one string and the check could not fail. It stopped
    catching a splice that missed, and ``agents remove`` reported ``removed``
    over a file that still held the entry. Only the caller's own arithmetic is
    an independent statement of intent, so this reports structure and leaves
    the value to the caller wherever the caller already knows it.

    Three outcomes, and each is the only safe answer for its case:

    * The block is left with nothing at all, so the parent goes too and an
      install followed by an uninstall leaves no bare ``parent:`` behind.
    * **The block still holds the user's comments, which is not empty.**
      Deleting a parent whose only remaining lines are comments takes those
      comments with it, and the parse cannot see the difference, so
      :func:`verify` would wave it through. A user who commented out a parked
      server inside ``mcp_servers`` would lose the note explaining why. The
      comments stay, the header stays with them, and the key now holds ``None``.
    * The value was written inline, in which case the child is taken out of it
      and the line stays inline. An emptied one comes back as ``parent: {}``,
      which is exactly what it was before the install.

    A no-op returning the parent untouched when either key is absent.
    """
    lines = _split(text)
    found = _find_top_level(lines, parent)
    if found is None:
        return text, ABSENT

    start, end = found
    if _is_flow_value(lines[start]):
        return _remove_child_from_flow_parent(lines, start, end, parent, child)

    indent = _child_indent(lines, start, end)
    existing = _find_child(lines, start, end, child, indent)
    if existing is None:
        # Nothing was removed, most often because the key is written in a form
        # the line match does not recognise, a quoted ``"child":`` for one. The
        # header is still there, which is what this reports. The caller's own
        # document says the child is gone while the text still holds it, so
        # :func:`verify` refuses, which is the honest outcome.
        return text, None

    child_start, child_end = existing
    remaining = lines[:child_start] + lines[child_end:]

    # Re-locate the parent in the shortened file rather than reusing the old
    # offsets: removing the child moved every line after it.
    refound = _find_top_level(remaining, parent)
    if refound is None:
        return _join(remaining), ABSENT

    new_start, new_end = refound
    body = remaining[new_start + 1 : new_end]
    if any(line.strip() for line in body):
        return _join(remaining), None
    return _join(remaining[:new_start] + remaining[new_end:]), ABSENT


def _remove_child_from_flow_parent(
    lines: list[str], start: int, end: int, parent: str, child: str
) -> tuple[str, object]:
    """Take a child out of an inline parent, leaving it inline.

    The mirror of :func:`_set_child_in_flow_parent`, sharing :func:`_flow_span`
    with it so the two cannot disagree about which lines the value occupies or
    about which values they will touch. It has to exist for the same reason
    that one does: teaching the write path about inline parents and leaving
    this one behind meant ``_find_child`` looked for a ``child:`` line that is
    not on a line of its own, found nothing, and reported the parent unchanged.
    Uninstall then failed its own consistency check and left the entry in
    place, on exactly the configs the write path had just learned to support.

    ``None`` is reported for every path that changes nothing, which says the
    header is still there and is true. The caller's own document still says the
    child is gone, so :func:`verify` refuses rather than reporting a removal
    that did not happen.
    """
    span = _flow_span(lines, start, end, parent)
    if span is None:
        return _join(lines), None
    stop, current = span
    if not isinstance(current, dict) or child not in current:
        return _join(lines), None

    remaining = {key: item for key, item in current.items() if key != child}
    rendered = render_child(parent, remaining, 0, flow=True)
    comment = _trailing_comment("\n".join(lines[start:stop]))
    if comment:
        rendered = [f"{rendered[0]}  {comment}", *rendered[1:]]
    return _join(lines[:start] + rendered + lines[stop:]), remaining


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
