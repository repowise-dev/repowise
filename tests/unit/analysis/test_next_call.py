"""``render_call`` reproduces, byte for byte, the call lines the action rules
used to write by hand, so moving them to ``tool`` + ``arguments`` changed no
``mcp`` string."""

from __future__ import annotations

import pytest

from repowise.core.analysis.next_call import ActionCommand, render_call

_PATHS = ["src/a.py", "src/b.py", "src/c.py"]
_SHA = "0123456789abcdef0123456789abcdef01234567"


def _old_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


# (tool, arguments, the f-string the rule used to build)
_SITES = [
    # code.py: fresh regressions
    (
        "get_health",
        {"targets": _PATHS[:10], "include": ["biomarkers"]},
        f'get_health(targets={_old_list(_PATHS[:10])}, include=["biomarkers"])',
    ),
    ("get_change_risk", {"revspec": _SHA}, f'get_change_risk(revspec="{_SHA}")'),
    (
        "get_context",
        {"targets": ["src/a.py"], "include": ["skeleton", "callers"]},
        'get_context(targets=["src/a.py"], include=["skeleton", "callers"])',
    ),
    # code.py: fragile file
    ("get_risk", {"targets": ["src/a.py"]}, 'get_risk(targets=["src/a.py"])'),
    (
        "get_health",
        {"targets": ["src/a.py"], "include": ["biomarkers"]},
        'get_health(targets=["src/a.py"], include=["biomarkers"])',
    ),
    # code.py: fix concentration
    ("get_risk", {"targets": _PATHS[:5]}, f"get_risk(targets={_old_list(_PATHS[:5])})"),
    ("get_why", {"targets": ["src/"]}, 'get_why(targets=["src/"])'),
    # code.py: fix first
    ("get_health", {"fix_id": "fix1_abc"}, 'get_health(fix_id="fix1_abc")'),
    # hygiene.py: live secret, broken doc refs, dead code, stale decision
    (
        "get_context",
        {"targets": ["src/a.py"], "include": ["callers"]},
        'get_context(targets=["src/a.py"], include=["callers"])',
    ),
    (
        "get_context",
        {"targets": ["docs/x.md"], "include": ["doc_drift"]},
        'get_context(targets=["docs/x.md"], include=["doc_drift"])',
    ),
    ("get_dead_code", {"safe_only": True}, "get_dead_code(safe_only=True)"),
    ("get_why", {"id": "dec_1"}, 'get_why(id="dec_1")'),
]


@pytest.mark.parametrize(("tool", "arguments", "expected"), _SITES)
def test_render_call_matches_the_hand_written_line(tool, arguments, expected) -> None:
    assert render_call(tool, arguments) == expected
    command = ActionCommand.call("p", tool, arguments)
    assert command.mcp == expected
    assert command.as_dict() == {
        "purpose": "p",
        "mcp": expected,
        "cli": None,
        "tool": tool,
        "arguments": arguments,
    }


def test_render_call_literals() -> None:
    assert render_call("get_change_risk") == "get_change_risk()"
    assert (
        render_call("t", {"n": 6, "x": 1.5, "off": False, "none": None, "m": {"k": ["v"]}})
        == 't(n=6, x=1.5, off=False, none=None, m={"k": ["v"]})'
    )
    assert render_call("t", {"q": 'say "hi"'}) == 't(q="say \\"hi\\"")'


def test_a_hand_written_command_keeps_its_old_wire_shape() -> None:
    assert ActionCommand("p", mcp="m").as_dict() == {"purpose": "p", "mcp": "m", "cli": None}


def test_a_command_hashes_and_equal_commands_hash_equal() -> None:
    a = ActionCommand.call("p", "get_health", {"targets": ["a.py"], "include": ["biomarkers"]})
    b = ActionCommand.call("p", "get_health", {"targets": ["a.py"], "include": ["biomarkers"]})
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b, ActionCommand("p", mcp="m")}) == 2
