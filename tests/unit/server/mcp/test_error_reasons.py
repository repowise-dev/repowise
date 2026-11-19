"""Every in-band MCP error names why it failed, from a closed set of reasons.

A tool answers a failure in-band as ``{"error": ...}``, and telemetry files the
call under the reason its payload noted, or ``other`` when none was noted.
These tests hold the server source to one rule: a top-level error dict is
built inside :func:`error_reply`, whose reason is one of
:data:`ERROR_REASONS`. The few error dicts that are not a call's answer (a
card for one target among several, the failure shield's own shapes, an HTTP
body) are listed here by function, each with why.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

import repowise.server.mcp_server as mcp_server_pkg
from repowise.server.mcp_server._savings import interaction
from repowise.server.mcp_server._savings.interaction import ERROR_REASONS, error_reply

_ROOT = Path(mcp_server_pkg.__file__).parent

#: ``(module, function)`` whose ``"error"`` dicts are not a call's top-level answer.
_NOT_A_CALL_ANSWER = {
    # Shapes picked by _shape_exception, which notes the reason for all of them.
    ("_failure_shield.py", "_shape_not_indexed"),
    ("_failure_shield.py", "_shape_store_unavailable"),
    ("_failure_shield.py", "_shape_unknown_repo"),
    ("_failure_shield.py", "_shape_stale_index"),
    ("_failure_shield.py", "_shape_stale_server"),
    ("_failure_shield.py", "_shape_broken_install"),
    ("_failure_shield.py", "_shape_internal_error"),
    # An HTTP 401 body, not a tool reply.
    ("_http_auth.py", "__call__"),
    # Per-target cards inside an ok ``targets`` map (get_context, workspace fan-out).
    ("_workspace_routing.py", "card"),
    ("_workspace_routing.py", "run_grouped"),
    ("_components.py", "component_cards"),
    ("_packages.py", "package_cards"),
    ("_changes.py", "_change_miss"),
    ("tool_context/resolution.py", "_excluded_error"),
    ("tool_context/resolution.py", "_git_metadata"),
    ("tool_context/resolution.py", "_legacy_community_id"),
    ("tool_context/resolution.py", "_not_found"),
    ("tool_context/context.py", "_per_target_results"),
    ("tool_context/sections.py", "_tombstone_section"),
    ("tool_context/skeleton.py", "_resolve_skeleton"),
    # Rewrites the message of the error_reply payload built just above it.
    ("tool_why/path_mode.py", "_path_not_found"),
}

#: Functions that pass error_reply a computed reason; tested by behaviour below.
_COMPUTED_REASON = {("tool_change_risk/tool.py", "_score_guarded"), ("tool_symbol/ranges.py", "_err")}


def _modules() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(_ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(_ROOT.rglob("*.py"))
    ]


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return "<module>"


def _is_error_reply(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "id", None) == "error_reply"


def _is_error_key(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "error"


def _error_sites(tree: ast.Module) -> list[ast.AST]:
    """Dict literals with an ``"error"`` key, and ``x["error"] = ...`` writes."""
    return [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Dict) and any(map(_is_error_key, node.keys)))
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Subscript) and _is_error_key(t.slice) for t in node.targets)
        )
    ]


def test_every_error_dict_is_built_inside_error_reply() -> None:
    stray = []
    for module, tree in _modules():
        parents = _parents(tree)
        for site in _error_sites(tree):
            if _is_error_reply(parents.get(site)):
                continue
            if (module, _function(site, parents)) in _NOT_A_CALL_ANSWER:
                continue
            stray.append(f"{module}:{site.lineno} in {_function(site, parents)}")
    assert not stray, "error dicts without a reason; build them with error_reply:\n" + "\n".join(
        stray
    )


def test_the_exemption_list_names_only_functions_that_still_build_errors() -> None:
    """A stale exemption would quietly cover a new error site of the same name."""
    seen = set()
    for module, tree in _modules():
        parents = _parents(tree)
        seen |= {(module, _function(site, parents)) for site in _error_sites(tree)}
    assert seen >= _NOT_A_CALL_ANSWER, _NOT_A_CALL_ANSWER - seen


def test_every_literal_reason_is_in_the_closed_set() -> None:
    computed = set()
    for module, tree in _modules():
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not _is_error_reply(node):
                continue
            reason = node.args[0]
            literals = (
                [reason.body, reason.orelse] if isinstance(reason, ast.IfExp) else [reason]
            )
            if all(isinstance(r, ast.Constant) for r in literals):
                assert {r.value for r in literals} <= ERROR_REASONS, f"{module}:{node.lineno}"
            else:
                computed.add((module, _function(node, parents)))
    assert computed == _COMPUTED_REASON


def test_ranges_err_is_only_called_with_literal_reasons() -> None:
    tree = ast.parse((_ROOT / "tool_symbol" / "ranges.py").read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_err"
    ]
    assert calls
    for call in calls:
        assert isinstance(call.args[0], ast.Constant) and call.args[0].value in ERROR_REASONS


def test_error_reply_notes_the_reason_and_returns_the_payload() -> None:
    payload = {"symbol_id": "x", "error": "Symbol not found"}
    with interaction.begin("get_symbol") as live:
        assert error_reply("not_found", payload) is payload
    assert live.error_reason == "not_found"


def test_an_unknown_reason_is_never_recorded() -> None:
    with interaction.begin("get_symbol") as live:
        error_reply("Symbol not found: 'secret'", {"error": "x"})
    assert live.error_reason is None


# --- get_change_risk: each git failure gets its own reason --------------------


async def _guarded(exc: BaseException) -> tuple[str | None, dict | None]:
    from repowise.server.mcp_server.tool_change_risk.tool import _score_guarded

    def score() -> None:
        raise exc

    with interaction.begin("get_change_risk") as live:
        _, error = await _score_guarded("main..HEAD", score)
    return live.error_reason, error


def _git_error(stderr: str) -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(128, ["git", "diff"], stderr=stderr)


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (_git_error("fatal: ambiguous argument 'nosuch..HEAD': unknown revision"), "invalid_args"),
        (_git_error("fatal: bad revision 'x'"), "invalid_args"),
        (
            _git_error("fatal: not a git repository (or any of the parent directories): .git"),
            "git_unavailable",
        ),
        (_git_error("fatal: detected dubious ownership in repository at 'D:/r'"), "git_unavailable"),
        (_git_error("fatal: bad object 0123abcd"), "invalid_args"),
        (subprocess.TimeoutExpired(["git", "diff"], 30), "timeout"),
        (ValueError("Invalid revspec '-x': a ref cannot start with '-'."), "invalid_args"),
    ],
)
async def test_change_risk_errors_carry_a_specific_reason(exc, reason) -> None:
    noted, error = await _guarded(exc)
    assert error is not None and error["error"]
    assert noted == reason


async def test_change_risk_names_a_missing_git(monkeypatch) -> None:
    from repowise.server.mcp_server.tool_change_risk import tool

    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    noted, error = await _guarded(FileNotFoundError("git"))
    assert error is not None and "git was not found" in error["error"]
    assert noted == "git_unavailable"


def test_per_commit_refusals_are_invalid_args() -> None:
    from repowise.server.mcp_server.tool_change_risk.per_commit import _refusal

    for revspec, finding in (("HEAD", None), ("main..HEAD", "f1")):
        with interaction.begin("get_change_risk") as live:
            assert _refusal(revspec, finding)["error"]
        assert live.error_reason == "invalid_args"


def test_the_wrapper_reports_the_noted_reason_not_other() -> None:
    from repowise.server.mcp_server._savings import wrapper

    with interaction.begin("get_change_risk"):
        result = error_reply("timeout", {"error": "git timed out reading change 'HEAD'."})
        props = wrapper._telemetry_properties("get_change_risk", result, 1)
    assert props["status"] == "error"
    assert props["error_reason"] == "timeout"
