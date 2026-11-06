"""Pi lifecycle hooks: SessionStart context + post-tool enrichment.

Pi's extension (``plugins/pi/repowise.ts``) hands ``repowise-augment`` a
Pi-shaped payload — lowercase tool names (``bash``, ``read``, ``edit``,
``write``, ``grep``, ``find``), Pi-shaped inputs (``path`` instead of
``file_path``) and Pi ``tool_result`` content blocks — and this module
translates it into the shapes the handlers below already speak. The
translation is deliberately narrow:

* tool *names* stay Pi's. The Pi adapter's tool sets carry them, the
  dispatcher routes on them, and the ledger records them, so a Pi read is
  never counted as a Claude Code one. Only the two wrong-path sets that
  gate on tool names needed Pi's spellings added (see
  :mod:`repowise.cli.commands.augment_cmd.wrong_path`).
* tool *inputs* gain the ``file_path`` key the read/edit state machine
  reads (``path`` is kept alongside it — a handler that reads ``path``
  directly still finds it).
* tool *responses* are rebuilt into the envelopes the handlers trust:
  a Read becomes the ``{"file": {"filePath", "content"}}`` shape the
  skeleton/collapse surfaces verify against, a Grep becomes a
  ``{"mode": "content", "content"}`` dict whose line count the digest
  surface can trust, a Glob (Pi ``find``) becomes the ``filenames``
  shape, and a Bash becomes ``{"stdout", "exit_code"}`` so the commit
  surfaces read an exit code instead of scanning output for the words
  "error"/"fatal".

Nothing downstream branches on the host: after this module, a Pi payload
is indistinguishable from one the Claude Code hooks sent, except for the
tool-name spelling the ledger keeps. That is the per-host augment
precedent :mod:`.codex` set — one module per host, no branches elsewhere.
"""

from __future__ import annotations

#: Pi tool names that reach the augment pipeline, in the extension's
#: spelling. ``ls`` is deliberately absent: a directory listing carries no
#: pattern for the search surface and no file for the read surface, so
#: calling the hook for it would buy a subprocess that can only decline.
#: MCP tools (``mcp__repowise__*``) pass through unchanged — the dispatcher
#: already matches them by prefix.
_PI_TOOL_NAMES: frozenset[str] = frozenset(
    {"bash", "powershell", "read", "edit", "write", "grep", "find"}
)


def _text_blocks(content: object) -> str:
    """The text of Pi ``tool_result`` content blocks, joined.

    Pi results arrive as ``[{"type": "text", "text": "..."}, ...]``, wrapped
    in the ``{"content": [...]}`` envelope the extension sends. A non-text
    block contributes nothing; a result with no text at all is the empty
    string, which every handler below reads as "nothing to count" rather
    than as a result.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _text_blocks(content.get("content"))
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts)


def _tool_response(tool_name: str, tool_input: dict, content: object, *, is_error: bool) -> object:
    """The ``tool_response`` envelope the handlers trust, for *tool_name*.

    Built from the Pi result text rather than passed through, because the
    handlers verify shapes (a Read needs its ``file`` block, a Grep its
    ``mode``/``content`` pair) and a shape they do not recognise is a
    surface silently skipped — the same failure mode as a hook matcher
    that stops matching.
    """
    text = _text_blocks(content)
    if tool_name == "read":
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        return {"file": {"filePath": path, "content": text}}
    if tool_name in ("bash", "powershell"):
        return {"stdout": text, "exit_code": 1 if is_error else 0}
    if tool_name == "grep":
        # No ``numLines`` on purpose: the digest surface falls back to
        # counting the text, which is the path that honours the no-match
        # sentinels. A stated count would let a "No matches found" body
        # read as a hit.
        return {"mode": "content", "content": text}
    if tool_name == "find":
        filenames = [line for line in text.splitlines() if line.strip()]
        return {"filenames": filenames, "numFiles": len(filenames)}
    return text


def normalize_pi_payload(payload: dict) -> dict:
    """A Pi hook payload, translated for the shared dispatch in :mod:`.command`.

    Returns a new dict; the caller's payload is untouched. Unknown tool
    names and malformed inputs pass through unchanged — the dispatcher
    declines them with an empty result, which is the same answer a
    passthrough gives, and never an error in the agent transcript.
    """
    normalized = dict(payload)
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or tool_name not in _PI_TOOL_NAMES:
        return normalized
    tool_input = payload.get("tool_input")
    tool_input = {} if not isinstance(tool_input, dict) else dict(tool_input)
    # ``path`` is Pi's spelling; the read/edit state machine reads
    # ``file_path``. Both keys are kept so neither reader goes hungry.
    path = tool_input.get("path")
    if isinstance(path, str) and path.strip() and "file_path" not in tool_input:
        tool_input["file_path"] = path
    normalized["tool_input"] = tool_input

    content = payload.get("tool_response", payload.get("tool_output"))
    is_error = payload.get("is_error") is True or payload.get("isError") is True
    normalized["tool_response"] = _tool_response(tool_name, tool_input, content, is_error=is_error)
    return normalized


def is_pi_tool(tool_name: object) -> bool:
    """Whether *tool_name* is one the Pi extension routes to the hook."""
    return isinstance(tool_name, str) and tool_name in _PI_TOOL_NAMES


__all__ = [
    "is_pi_tool",
    "normalize_pi_payload",
]
