"""D5: the note advertised a ``symbol_id`` nobody had checked resolves.

``answer.py`` took ``symbol_bodies[].withheld_symbols[].symbol_id`` straight from
the scanner and interpolated it into both ``note`` and ``next_action_hint``. The
scanner is a regex over source lines, so it can name something that is not a
symbol -- and the id does not end up in a list of eight, it becomes the next
action the payload tells the agent to take.

The subtlety this file exists to pin down: ``get_symbol`` has THREE outcomes and
only ONE is a failure.

  indexed row   -- resolves
  live_grep     -- no index row, but the name is in the live file, so it answers
                   with ``resolution: "live_grep"`` and ``fallback_lines``
  nothing       -- the only real failure

Counting the middle case as unresolvable is a mistake already made once while
measuring this: it read mui as "90% unresolvable" when the truth was "90% no
index row, all still usable". A guard that suppressed those would delete a
useful pointer on nine ids out of ten on that tree.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _patch_provider(monkeypatch, answer_mod, content: str):
    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


def _build_tree(tmp_path, monkeypatch, mcp_mod, answer_mod, *, answer_text):
    """A truncated ``big_handler`` whose withheld range holds ``ghost_helper``."""
    src = ["import os", ""]
    src.append("def big_handler(request):")
    src.append('    """Handle it."""')
    for i in range(300):
        src.append(f"    step_{i} = {i}")
    src.append("    return step_0")
    src.append("")
    src.append("def ghost_helper(value):")
    src.append("    return value")
    (tmp_path / "handler.py").write_text("\n".join(src) + "\n", encoding="utf-8")
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    symbol = {
        "name": "big_handler",
        "kind": "function",
        "signature": "def big_handler(request)",
        "docstring": "Handle it.",
        "start_line": 3,
        "end_line": len(src),
        "_matched": True,
        "source_excerpt": "def big_handler(request):\n    step_0 = 0",
    }

    async def _fake_retrieve(question, ctx):
        return [{"page_id": "file_page:handler.py", "score": 5.0}]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = "handler.py"
            h["title"] = "handler.py"
            h["summary"] = "Handler module."
            h["snippet"] = ""
            h["page_type"] = "file_page"
            h["symbols"] = [dict(symbol)]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    _patch_provider(monkeypatch, answer_mod, answer_text)


@pytest.mark.asyncio
async def test_an_id_that_only_live_greps_is_still_advertised(
    setup_mcp, monkeypatch, tmp_path
):
    """The middle outcome. Passes at the parent, and MUST keep passing.

    ``ghost_helper`` is a real name on a real line with no index row behind it
    in this fixture, which is exactly the population that measured 91.5% indexed
    / 8.5% live-grep / 0% dead across 130 corpus ids. A guard that rejected it
    would be a regression dressed as a fix, so this is the control that stops
    the guard from over-reaching.
    """
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    # The withheld name is implicated by the QUESTION, not by the answer text:
    # naming it in the answer instead trips the earlier claim-support gate (the
    # term is absent from every retrieved excerpt, because it was withheld) and
    # the note never reaches the branch under test.
    _build_tree(
        tmp_path,
        monkeypatch,
        mcp_mod,
        answer_mod,
        answer_text="big_handler runs each step in order and returns the first.",
    )

    result = await get_answer("how does big_handler use ghost_helper on each step")

    note = result.get("note") or ""
    hint = result.get("next_action_hint") or ""
    assert "ghost_helper" in note, note
    assert "handler.py::ghost_helper" in hint, hint


@pytest.mark.asyncio
async def test_an_id_that_resolves_to_nothing_is_not_advertised(
    setup_mcp, monkeypatch, tmp_path
):
    """FAILS at the parent: a name absent from the file is still promoted.

    The scanner is monkeypatched to emit a name that is nowhere in the live
    source. That is the shape D2 and D3 produced naturally -- GraphQL field
    names out of Go raw strings, and the Go keyword ``func`` -- and with both
    now fixed the honest way to exercise the consumer's guard is to inject one
    directly rather than to wait for the next regex defect to supply it.

    The name still appears in the note (the answer does depend on something that
    was not served); only the dead pointer is withheld.
    """
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _build_tree(
        tmp_path,
        monkeypatch,
        mcp_mod,
        answer_mod,
        answer_text="big_handler runs each step in order and returns the first.",
    )

    real = answer_mod.withheld_definitions

    def _fabricating(repo_root, continuation):
        out = list(real(repo_root, continuation))
        out.append(
            {
                "name": "phantomThing",
                "kind": "member",
                "line": 40,
                "symbol_id": "handler.py::phantomThing",
                "signature": "member phantomThing",
            }
        )
        return out

    monkeypatch.setattr(answer_mod, "withheld_definitions", _fabricating)

    result = await get_answer("how does big_handler use phantomThing on each step")

    note = result.get("note") or ""
    hint = result.get("next_action_hint") or ""
    assert "phantomThing" not in (tmp_path / "handler.py").read_text(encoding="utf-8")
    assert "phantomThing" in note, "the unserved name should still be reported"
    assert "handler.py::phantomThing" not in note, note
    assert "handler.py::phantomThing" not in hint, hint


@pytest.mark.asyncio
async def test_an_unreadable_file_does_not_disqualify_its_id(
    setup_mcp, monkeypatch, tmp_path
):
    """Absence of evidence is not evidence of fabrication.

    If the file behind an id cannot be read, the guard has learned nothing and
    must keep the id. Suppressing on an unreadable file would silently drop a
    valid pointer on every repo whose sources moved since indexing.
    """
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _build_tree(
        tmp_path,
        monkeypatch,
        mcp_mod,
        answer_mod,
        answer_text="big_handler runs each step in order and returns the first.",
    )

    real = answer_mod.withheld_definitions

    def _from_a_vanished_file(repo_root, continuation):
        out = list(real(repo_root, continuation))
        out.append(
            {
                "name": "movedAway",
                "kind": "function",
                "line": 7,
                "symbol_id": "gone/vanished.py::movedAway",
                "signature": "def movedAway()",
            }
        )
        return out

    monkeypatch.setattr(answer_mod, "withheld_definitions", _from_a_vanished_file)

    result = await get_answer("how does big_handler use movedAway on each step")

    hint = result.get("next_action_hint") or ""
    note = result.get("note") or ""
    assert not (tmp_path / "gone" / "vanished.py").exists()
    assert "gone/vanished.py::movedAway" in (note + hint), (note, hint)


def test_answer_schema_version_was_bumped_for_this_change() -> None:
    """Without the bump a cached row serves the pre-fix payload for 14 days.

    Both changes in this pair alter payload CONTENT -- which names
    ``withheld_symbols`` reports, and which id ``next_action_hint`` names -- so
    the staleness test (``cached < current``) has to see a new number.
    """
    from repowise.server.mcp_server.tool_answer.config import _ANSWER_SCHEMA_VERSION

    assert _ANSWER_SCHEMA_VERSION >= 15
