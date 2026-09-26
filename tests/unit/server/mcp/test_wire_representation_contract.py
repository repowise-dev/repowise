"""What each tool actually puts on the wire, pinned at the serialization seam.

Every other budget and projection suite in this directory asserts on the
Python dict a tool returns. Nothing asserted on what the MCP SDK then makes of
that dict, so the step a client actually reads had no test at all.

It is worth pinning because the wire does not match what the tool definitions
suggest. ``func_metadata`` builds a tool's output schema from the return
annotation of the callable it is handed, resolving it with
``inspect.signature(func, eval_str=True)``. It never gets to resolve anything
here. Three middleware layers preserve the tool's signature by snapshotting
``__signature__`` — ``_failure_shield.py`` innermost, then ``_rounding.py``
and ``_savings/wrapper.py`` — and each takes that snapshot with ``eval_str``
left at its default, so the annotations in it stay unevaluated strings.
``inspect.signature`` returns a pre-built ``__signature__`` verbatim and
ignores ``eval_str``, and ``functools.wraps`` copies the attribute outward, so
every layer carries it. The SDK therefore sees the *string* ``"dict"``, which
matches none of its cases and falls through to the catch-all that wraps the
payload.

Because all three snapshot the same way, removing any one of them changes
nothing; the behaviour only moves if none of them is left.

So every tool serves ``structuredContent`` as ``{"result": <payload>}``, and
two things follow that are not legible from the tool definitions:

* every tool serves both representations, so every payload crosses the wire
  twice;
* the tools that declare ``-> dict[str, Any]`` in order to serve a bare
  structured payload do not get one, and are served exactly like the ones
  declaring ``-> dict``.

The second point is what makes these tests worth having. Let the annotations
resolve and the bare-``dict`` tools serve **no** ``structuredContent`` at all
while the parameterized ones serve an unwrapped payload. That is a silent
change to every client, reachable from an edit that looks like typing or
signature hygiene, in modules none of these tools mention.

The seam under test is ``FuncMetadata.convert_result``, which is what
``FastMCP.call_tool`` uses to build the ``CallToolResult``. It reads only the
callable's signature, so these tests need no index, no fixture and no I/O.
"""

from __future__ import annotations

import inspect
import json

from mcp.server.fastmcp.utilities.func_metadata import func_metadata

from repowise.core.registry import mcp_tool_registry

# The registered surface. Pinned as a count because `ensure_full_surface`
# swallows a per-module ImportError and only logs it: a tool module that stops
# importing would quietly drop out of the registry, shrinking what the loops
# below cover while leaving them green.
EXPECTED_TOOL_COUNT = 18

# A payload shaped like a real tool reply: str keys, nested containers and a
# `_meta` envelope. Any mapping validates against the SDK's wrapping model, so
# this exercises serialization rather than any one tool's own schema.
SAMPLE_PAYLOAD = {
    "answer": "resolved in one call",
    "citations": ["packages/core/src/repowise/core/ingestion/parser.py"],
    "rows": [{"path": "a/b.py", "lines": [10, 20]}],
    "_meta": {"timing_ms": 1.5, "complete": "1 body served whole"},
}


def _registry_entries():
    """The bare tool functions, in name order."""
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    tools = mcp_tool_registry.tools()
    assert tools, "tool registry is empty — ensure_full_surface broken?"
    return sorted(tools, key=lambda fn: fn.__name__)


def _registered_callables():
    """What the server hands FastMCP, not the bare tool functions.

    `mcp_tool_registry.apply` registers `middleware(entry.fn)`, so building a
    schema from the bare function measures something the client never sees.
    """
    from repowise.server.mcp_server import tool_middleware

    return [tool_middleware(fn) for fn in _registry_entries()]


def _convert(fn):
    """Return ``(text_blocks, structured_or_None)`` exactly as the server would."""
    result = func_metadata(fn).convert_result(dict(SAMPLE_PAYLOAD))
    if isinstance(result, tuple):
        blocks, structured = result
        return list(blocks), structured
    return list(result), None


def test_the_whole_registered_surface_is_under_test():
    """Guard the loops below against silently covering fewer tools."""
    names = [fn.__name__ for fn in _registry_entries()]
    assert len(names) == EXPECTED_TOOL_COUNT, (
        f"expected {EXPECTED_TOOL_COUNT} registered tools, found {len(names)}: "
        f"{names}. A tool module failing to import is logged and skipped, so a "
        "shrinking surface otherwise leaves this file green while testing less."
    )


def test_every_tool_serves_a_readable_text_representation():
    """No client loses access to a usable response, whatever it consumes.

    The text block is the representation every MCP client is guaranteed, so it
    has to be present, non-empty, and a faithful rendering of what the tool
    returned rather than a summary or a repr.
    """
    broken = {}
    for fn in _registered_callables():
        blocks, _structured = _convert(fn)
        if not blocks or not (blocks[0].text or "").strip():
            broken[fn.__name__] = "no usable text block"
            continue
        try:
            if json.loads(blocks[0].text) != SAMPLE_PAYLOAD:
                broken[fn.__name__] = "text block is not a faithful rendering"
        except json.JSONDecodeError:
            broken[fn.__name__] = "text block is not valid JSON"
    assert not broken, (
        f"tools whose text representation a client could not use: {broken}. "
        "Every client is guaranteed the text block and nothing else."
    )


def test_every_tool_serves_structured_content_wrapped_in_result():
    """The structured half of the envelope, and the shape clients must unwrap.

    A consumer of structured output reads ``structuredContent["result"]``, not
    ``structuredContent``. Both facts are pinned so that changing either is a
    deliberate, reviewed wire change rather than a side effect.

    A failure here is far more likely to come from the installed SDK version
    or from the ``__signature__`` snapshots described in the module docstring
    than from the named tool, so check those before the tool.
    """
    wrong = {}
    for fn in _registered_callables():
        blocks, structured = _convert(fn)
        if structured is None:
            wrong[fn.__name__] = "serves no structuredContent at all"
        elif set(structured) != {"result"}:
            wrong[fn.__name__] = f"keyed {sorted(structured)}, not ['result']"
        elif structured["result"] != json.loads(blocks[0].text):
            wrong[fn.__name__] = "structured and text representations disagree"
    assert not wrong, (
        f"structured output moved: {wrong}. Suspect, in order: the installed "
        "mcp version, the __signature__ snapshots in _failure_shield.py / "
        "_rounding.py / _savings/wrapper.py, then the tool itself."
    )


def test_declared_structured_annotations_do_not_reach_the_wire():
    """The declared return type is not what the SDK sees, and this records it.

    Some tools declare ``-> dict[str, Any]`` to serve a bare structured
    payload. The registered callable presents its annotations unevaluated, so
    the declaration never matches and their output is wrapped like everything
    else — it is inert.

    The declaring set is derived from the source rather than listed here, so
    that a tool added to or removed from it cannot make the module docstring
    quietly untrue. This exists to fail the moment the declaration starts
    mattering, not to endorse it.
    """
    declaring = sorted(
        fn.__name__
        for fn in _registry_entries()
        if "dict[" in str(fn.__annotations__.get("return", ""))
    )
    assert declaring, (
        "no tool declares a parameterized return annotation any more; the "
        "module docstring's account of an inert declaration is now stale."
    )
    registered = {fn.__name__: fn for fn in _registered_callables()}
    reaching = {}
    for name in declaring:
        annotation = inspect.signature(registered[name], eval_str=True).return_annotation
        if not isinstance(annotation, str):
            reaching[name] = repr(annotation)
    assert not reaching, (
        f"these tools now expose an evaluated return annotation: {reaching}. "
        "Their structuredContent is no longer wrapped in 'result', and every "
        "tool declaring a bare `dict` has stopped serving structuredContent "
        "entirely. That is a wire change for every client."
    )
