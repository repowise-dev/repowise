"""Phase 2 response-shape projections: what they drop, and what they must keep.

Each projection here removes a field the reader can rebuild from the response
it ships beside. That is only true while the derivation holds, so every test
below breaks the derivation and asserts the field comes back rather than being
dropped on a coincidence.
"""

from __future__ import annotations

from repowise.server.mcp_server._budget.collector import _RESTORE_HINT
from repowise.server.mcp_server._helpers import drop_echoed_target
from repowise.server.mcp_server.tool_risk.directives import _project_recommendation


def _row(**over):
    row = {
        "test_id": "tests/test_a.py",
        "test_file": "tests/test_a.py",
        "repository_id": "repo1",
        "repository": "demo",
        "basis": "inferred",
        "bases": ["inferred"],
        "source_files": ["src/a.py"],
        "evidence": [
            {
                "basis": "inferred",
                "source_file": "src/a.py",
                "via": "call-graph",
                "source_format": None,
            }
        ],
    }
    row.update(over)
    return row


def test_a_derivable_field_is_dropped():
    out = _project_recommendation(_row())
    for gone in ("source_files", "bases", "test_file", "repository", "repository_id"):
        assert gone not in out, gone
    # Everything the dropped fields were folded from is still on the wire.
    assert out["basis"] == "inferred"
    assert [e["source_file"] for e in out["evidence"]] == ["src/a.py"]
    assert "source_format" not in out["evidence"][0]


def test_a_test_file_that_is_not_the_test_id_survives():
    """A measured row's id carries a ``::`` selector; its file does not."""
    out = _project_recommendation(
        _row(test_id="tests/test_a.py::test_one", test_file="tests/test_a.py")
    )
    assert out["test_file"] == "tests/test_a.py"


def test_a_second_basis_survives():
    """``bases`` goes only when it is exactly ``[basis]``."""
    out = _project_recommendation(
        _row(basis="measured", bases=["measured", "inferred"])
    )
    assert out["bases"] == ["measured", "inferred"]


def test_a_real_source_format_survives():
    out = _project_recommendation(
        _row(
            evidence=[
                {
                    "basis": "measured",
                    "source_file": "src/a.py",
                    "via": "coverage-map",
                    "source_format": "lcov",
                }
            ]
        )
    )
    assert out["evidence"][0]["source_format"] == "lcov"


def test_the_echoed_map_key_is_dropped():
    targets = {"src/a.py": {"target": "src/a.py", "type": "file"}}
    drop_echoed_target(targets)
    assert targets["src/a.py"] == {"type": "file"}


def test_a_card_that_disagrees_with_its_key_keeps_its_target():
    """The equality guard: a card is never silently stripped of a *different*
    value, because then the key would not carry it."""
    targets = {"alias": {"target": "src/a.py", "type": "file"}}
    drop_echoed_target(targets)
    assert targets["alias"]["target"] == "src/a.py"


def test_the_restore_hint_still_names_both_recovery_routes():
    """The CLI route is also in the omission marker; the ``get_symbol`` route
    is only here, and it is how a shell-less client recovers."""
    assert "repowise expand" in _RESTORE_HINT
    assert 'get_symbol("repowise#<ref>"' in _RESTORE_HINT
