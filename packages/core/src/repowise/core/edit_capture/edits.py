"""What an agent's tool call wrote, per agent, from the live hook or a transcript.

Two layers, and the split is what lets one parser serve both event sources:

* An **edit extractor** per agent, ``(tool_name, tool_input, tool_response)`` to
  the lines each file gained. The live hook and the transcript adapters hand it
  the same three values (a Claude Code transcript's ``tool_use.input`` and
  ``toolUseResult`` are the hook's ``tool_input`` and ``tool_response``; a Codex
  rollout's ``apply_patch`` input is the hook's ``tool_input.command``), so
  :meth:`repowise.core.sessions.adapters.base.HarnessAdapter.file_edits` calls
  the extractor named here rather than parsing edits a second time.
* A **capture adapter** per agent, a hook payload to a :class:`CapturedCall`:
  the envelope (session, model, cwd, call id) around the extractor's result.

Payload shapes, and where each was checked:

* Claude Code ``PostToolUse``: ``session_id``, ``cwd``, ``tool_name``,
  ``tool_input``, ``tool_response``, ``tool_use_id``; no model. ``Edit`` input
  ``file_path``/``old_string``/``new_string``/``replace_all``, ``Write`` input
  ``file_path``/``content``; responses carry ``structuredPatch`` hunks
  (``lines`` prefixed ``+``/``-``/space) and ``userModified``. Read from the
  installed 2.1.280 binary and local transcripts. ``MultiEdit``
  (``file_path`` plus ``edits[]`` of ``old_string``/``new_string``) is no
  longer a tool in that build; kept for older transcripts, shape as documented.
* Codex ``PostToolUse``: adds ``model`` and ``turn_id``; ``apply_patch`` carries
  the patch envelope in ``tool_input.command`` and a string ``tool_response``
  starting ``Exit code: N`` (openai/codex hook schema and hook tests). In code
  mode (an ``exec`` cell, Codex 0.144 to 0.162 rollouts on this machine) the
  envelope is a JavaScript string literal passed to ``tools.apply_patch`` and
  the response is a list of text blocks opening ``Script completed`` or
  ``Script failed``; :func:`codex_edits` reads both shapes, whichever one the
  hook or the rollout hands it.
* Cursor ``afterFileEdit``: ``conversation_id``, ``generation_id``, ``model``,
  ``workspace_roots``, ``file_path``, ``edits[]`` of
  ``old_string``/``new_string`` (cursor.com/docs/agent/hooks). ``user_email``
  is in the payload and is never read.

Stdlib only: imported on the hook path.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from .hashing import normalize_line, split_lines


class FileEdit:
    """Lines one tool call added to one file. Text lives only in memory."""

    __slots__ = ("added", "path")

    def __init__(self, path: str, added: list[str]) -> None:
        self.path = path
        self.added = added

    def __repr__(self) -> str:
        return f"FileEdit({self.path!r}, {len(self.added)} lines)"


class CapturedCall:
    """One hook payload, reduced to what an edit event records."""

    __slots__ = ("call_id", "cwd", "edits", "model", "session_id", "tool")

    def __init__(
        self,
        *,
        tool: str,
        edits: list[FileEdit],
        session_id: str = "",
        model: str | None = None,
        cwd: str = "",
        call_id: str = "",
    ) -> None:
        self.tool = tool
        self.edits = edits
        self.session_id = session_id
        self.model = model
        self.cwd = cwd
        self.call_id = call_id


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _replacement_lines(old: Any, new: Any) -> list[str]:
    """Lines of *new* that *old* did not already have (multiset difference).

    Context an agent repeats from ``old_string`` is not a line it wrote.
    """
    remaining = Counter(split_lines(_str(old)))
    added: list[str] = []
    for line in split_lines(_str(new)):
        if remaining[line] > 0:
            remaining[line] -= 1
        else:
            added.append(line)
    return added


def _plus_lines(hunk: Any) -> list[str]:
    lines = hunk.get("lines") if isinstance(hunk, dict) else None
    if not isinstance(lines, list):
        return []
    return [
        normalize_line(line[1:])
        for line in lines
        if isinstance(line, str) and line.startswith("+")
    ]


def _patch_added(structured_patch: Any) -> list[str] | None:
    """``+`` lines of a Claude Code ``structuredPatch``, or None when it has none."""
    if not isinstance(structured_patch, list) or not structured_patch:
        return None
    return [line for hunk in structured_patch for line in _plus_lines(hunk)]


def _edits_added(edits: Any) -> list[str]:
    """Replacement lines of an ``edits[]`` list of old/new string pairs."""
    added: list[str] = []
    for edit in edits if isinstance(edits, list) else []:
        if isinstance(edit, dict):
            added += _replacement_lines(edit.get("old_string"), edit.get("new_string"))
    return added


def _claude_code_added(tool_name: str, tool_input: dict, response: dict) -> list[str]:
    if tool_name == "MultiEdit":
        return _edits_added(tool_input.get("edits"))
    if tool_name not in ("Write", "Edit"):
        return []
    patched = _patch_added(response.get("structuredPatch"))
    if patched is not None:
        return patched
    if tool_name == "Edit":
        return _replacement_lines(tool_input.get("old_string"), tool_input.get("new_string"))
    # A Write without a patch: only a new file is all the agent's; an
    # overwrite (``update``, or no response) may keep lines it did not write.
    if response.get("type") == "create":
        return split_lines(_str(tool_input.get("content")))
    return []


def claude_code_edits(tool_name: str, tool_input: Any, tool_response: Any) -> list[FileEdit]:
    """Claude Code ``Edit`` / ``Write`` / ``MultiEdit``.

    The response's ``structuredPatch`` is preferred because it names exactly
    the added lines. A response saying ``userModified`` is skipped: the user
    changed the proposal before it landed, so the lines are not the agent's alone.
    """
    if not isinstance(tool_input, dict):
        return []
    path = _str(tool_input.get("file_path"))
    response = tool_response if isinstance(tool_response, dict) else {}
    if not path or response.get("userModified") is True:
        return []
    added = _claude_code_added(tool_name, tool_input, response)
    return [FileEdit(path, added)] if added else []


def _patch_header(line: str, current: FileEdit | None, edits: list[FileEdit]) -> FileEdit | None:
    """The file a ``*** `` header line opens (appended to *edits*), renames, or ends."""
    if line.startswith(("*** Add File: ", "*** Update File: ")):
        edits.append(FileEdit(line.split(": ", 1)[1].strip(), []))
        return edits[-1]
    if line.startswith("*** Move to: ") and current is not None:
        current.path = line.split(": ", 1)[1].strip()
        return current
    return None


def parse_apply_patch(patch: str) -> list[FileEdit]:
    """The files and added lines of a Codex ``apply_patch`` envelope.

    ``*** Add File:`` bodies are all added; ``*** Update File:`` hunks add their
    ``+`` lines, under the ``*** Move to:`` path when the file is renamed.
    ``*** Delete File:`` adds nothing.
    """
    edits: list[FileEdit] = []
    current: FileEdit | None = None
    for raw in patch.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("*** "):
            current = _patch_header(line, current, edits)
        elif current is not None and line.startswith("+"):
            current.added.append(normalize_line(line[1:]))
    return [edit for edit in edits if edit.added]


_JS_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_APPLY_PATCH_CALL = re.compile(r"tools\.apply_patch\s*\(\s*")
_JS_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_HEX = re.compile(r"[0-9a-fA-F]+")


def _js_escape(source: str, i: int) -> tuple[str, int] | None:
    """The escape whose letter sits at *source[i]*, decoded, and the index after it."""
    esc = source[i]
    if esc in _JS_ESCAPES:
        return _JS_ESCAPES[esc], i + 1
    if esc == "\r" and source[i + 1 : i + 2] == "\n":
        return "", i + 2  # line continuation
    if esc == "\n":
        return "", i + 1
    if esc == "x":
        digits, end, size = source[i + 1 : i + 3], i + 3, 2
    elif esc == "u" and source[i + 1 : i + 2] == "{":
        close = source.find("}", i)
        digits, end, size = (source[i + 2 : close], close + 1, 0) if close > 0 else ("", i, 0)
    elif esc == "u":
        digits, end, size = source[i + 1 : i + 5], i + 5, 4
    else:
        return esc, i + 1  # \\ \" \' \` and identity escapes
    if not _HEX.fullmatch(digits) or (size and len(digits) != size) or int(digits, 16) > 0x10FFFF:
        return None
    return chr(int(digits, 16)), end


def _js_string(source: str, start: int) -> str | None:
    """The JavaScript string literal opening at *source[start]*, decoded.

    None when it does not close, or is a template literal that interpolates.
    """
    quote, out, i = source[start], [], start + 1
    while i < len(source):
        ch = source[i]
        if ch == quote:
            try:  # a \\uD83D\\uDE00 pair becomes one character
                return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16")
            except UnicodeError:
                return None
        if ch == "\\" and i + 1 < len(source):
            decoded = _js_escape(source, i + 1)
            if decoded is None:
                return None
            out.append(decoded[0])
            i = decoded[1]
            continue
        if (ch == "\n" and quote != "`") or (quote == "`" and source.startswith("${", i)):
            return None
        out.append(ch)
        i += 1
    return None


def _apply_patch_argument(source: str, call: re.Match[str]) -> str | None:
    """The string literal a ``tools.apply_patch(`` call receives: written in
    place, or the literal last assigned to the variable it passes."""
    start = call.end()
    if source[start : start + 1] in ("\"", "'", "`"):
        return _js_string(source, start)
    name = _JS_NAME.match(source, start)
    if name is None:
        return None
    assign = rf"(?<![\w$.]){re.escape(name.group(0))}\s*=\s*(?=[\"'`])"
    found = list(re.finditer(assign, source[: call.start()]))
    return _js_string(source, found[-1].end()) if found else None


def _exec_cell_patches(source: str) -> list[str]:
    """Patches a Codex code-mode cell passes to ``tools.apply_patch``.

    The cell is JavaScript (``const patch = "*** Begin Patch\\n..."; await
    tools.apply_patch(patch)``), so the envelope sits escaped inside a literal.
    A literal the call does not receive is never read.
    """
    found = (_apply_patch_argument(source, m) for m in _APPLY_PATCH_CALL.finditer(source))
    return [p for p in found if p and p.lstrip().startswith("*** Begin Patch")]


def _response_text(tool_response: Any) -> str:
    if isinstance(tool_response, list):
        return "\n".join(
            block["text"]
            for block in tool_response
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return tool_response if isinstance(tool_response, str) else ""


def _codex_call_failed(tool_response: Any) -> bool:
    """Whether a Codex patch call reports it wrote nothing, or cannot say yet.

    ``Exit code: N`` (N not 0) for a direct call; a code-mode cell says
    ``Script failed``, or ``Script running`` while its outcome is unknown, and
    a cell that caught a failed patch still prints the verification error.
    """
    text = _response_text(tool_response)
    return bool(
        re.match(r"Exit code: (?!0\b)-?\d+", text)
        or text.startswith(("Script failed", "Script running"))
        or "apply_patch verification failed" in text
    )


def codex_edits(tool_name: str, tool_input: Any, tool_response: Any) -> list[FileEdit]:
    """Codex ``apply_patch``, called directly or from a code-mode cell.

    A call whose response reports a failure (:func:`_codex_call_failed`) wrote nothing.
    """
    if _codex_call_failed(tool_response):
        return []
    patch = ""
    if isinstance(tool_input, dict):
        patch = _str(tool_input.get("command")) or _str(tool_input.get("input"))
    elif isinstance(tool_input, str):
        patch = tool_input
    if "*** Begin Patch" not in patch:
        return []
    if patch.lstrip().startswith("*** Begin Patch"):
        return parse_apply_patch(patch)
    return [edit for body in _exec_cell_patches(patch) for edit in parse_apply_patch(body)]


def cursor_edits(tool_name: str, tool_input: Any, tool_response: Any) -> list[FileEdit]:
    """Cursor ``afterFileEdit``: ``file_path`` plus ``edits[]`` of old/new strings."""
    if not isinstance(tool_input, dict):
        return []
    path = _str(tool_input.get("file_path"))
    added = _edits_added(tool_input.get("edits")) if path else []
    return [FileEdit(path, added)] if added else []


Extractor = Callable[[str, Any, Any], list[FileEdit]]

#: Keyed by the agent's capture adapter name (``AgentIdentity.capture_adapter``),
#: which is also its transcript adapter name where it has one.
EXTRACTORS: dict[str, Extractor] = {
    "claude_code": claude_code_edits,
    "codex": codex_edits,
    "cursor": cursor_edits,
}


def _tool_call(payload: dict, extractor: Extractor) -> CapturedCall | None:
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    tool = _str(payload.get("tool_name"))
    edits = extractor(tool, payload.get("tool_input"), payload.get("tool_response"))
    if not edits:
        return None
    return CapturedCall(
        tool=tool,
        edits=edits,
        session_id=_str(payload.get("session_id")),
        model=_str(payload.get("model")) or None,
        cwd=_str(payload.get("cwd")),
        call_id=_str(payload.get("tool_use_id")),
    )


def claude_code_call(payload: dict) -> CapturedCall | None:
    return _tool_call(payload, claude_code_edits)


def codex_call(payload: dict) -> CapturedCall | None:
    return _tool_call(payload, codex_edits)


def cursor_call(payload: dict) -> CapturedCall | None:
    if payload.get("hook_event_name") != "afterFileEdit":
        return None
    edits = cursor_edits("afterFileEdit", payload, None)
    if not edits:
        return None
    roots = payload.get("workspace_roots")
    cwd = next((r for r in roots if isinstance(r, str) and r), "") if isinstance(roots, list) else ""
    generation = _str(payload.get("generation_id"))
    return CapturedCall(
        tool="afterFileEdit",
        edits=edits,
        session_id=_str(payload.get("conversation_id")),
        model=_str(payload.get("model")) or None,
        cwd=cwd,
        # One afterFileEdit per file per generation.
        call_id=f"{generation}:{edits[0].path}" if generation else "",
    )


#: Hook payload to :class:`CapturedCall`, by capture adapter name.
CAPTURE_ADAPTERS: dict[str, Callable[[dict], CapturedCall | None]] = {
    "claude_code": claude_code_call,
    "codex": codex_call,
    "cursor": cursor_call,
}
