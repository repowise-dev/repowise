"""``ask`` / ``context`` / ``symbol`` / ``why`` — the four MCP-tool adapters.

Three things have to hold and each has tests below.

1. **The projection is the contract.** These commands emit a trimmed CLI
   projection by default and the raw tool dict under ``--full``, and the
   session-cost bake-off cites the difference. So the field mapping is pinned
   against realistic payloads rather than described: a test that only checked
   "some keys survived" would pass with the trim silently disabled, which is
   the exact regression that matters.
2. **Every exit emits a document.** A json path that returns after a stderr
   notice leaves stdout empty, and a caller cannot tell that from a crash.
3. **The tool is really reached.** The tool functions are imported inside each
   command's factory, so a typo there is invisible until the command is run
   against a real repo. ``_spy_run`` calls the factory for real and only then
   substitutes a canned payload.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.commands.ask_cmd import ask_command
from repowise.cli.commands.ask_cmd import project as project_ask
from repowise.cli.commands.context_cmd import context_command
from repowise.cli.commands.context_cmd import project as project_context
from repowise.cli.commands.symbol_cmd import project as project_symbol
from repowise.cli.commands.symbol_cmd import symbol_command
from repowise.cli.commands.why_cmd import project as project_why
from repowise.cli.commands.why_cmd import why_command

# --------------------------------------------------------------------------
# Payloads shaped like the real tools' responses, trimmed in breadth but not
# in kind — every key the projections branch on is present.
# --------------------------------------------------------------------------

ANSWER_PAYLOAD = {
    "answer": "The width is resolved once, in helpers.py.",
    "citations": ["packages/cli/src/repowise/cli/output.py"],
    "confidence": "high",
    "retrieval_quality": "strong",
    "fallback_targets": ["packages/cli/src/repowise/cli/helpers.py"],
    "retrieval": [
        {
            "path": "packages/cli/src/repowise/cli/output.py",
            "excerpt": "x" * 1500,
            "key_symbols": [{"name": "resolve_console_width"}],
        }
    ],
    "quotes": [
        {"path": "packages/cli/src/repowise/cli/output.py", "lines": [44, 44],
         "quote": "NON_TTY_WIDTH = 400"}
    ],
    "candidates": [{"path": "a.py", "lines": "1-20", "defines": "f:1"}],
    "symbol_bodies": {"a.py::f": "def f(): ..."},
    "best_guesses": [
        {"file": "a.py", "why_relevant": "names the constant", "score": 2.1,
         "domain_penalty": None, "excerpt": "y" * 1500}
    ],
    "next_action_hint": "Read a.py first.",
    "_meta": {"timing_ms": 12.5, "indexed_commit": "abc123", "live_head": "def456",
              "index_behind": True, "index_age_days": 2},
}

CONTEXT_PAYLOAD = {
    "targets": {
        "a.py": {
            "target": "a.py",
            "type": "file",
            "parent_page": {"title": "CLI", "target_path": "packages/cli"},
            "docs": {"title": "File: a.py", "summary": "Does a thing."},
            "hotspot": True,
            "fix_history": {"fix_count": 3, "last_fix_days_ago": 1, "bug_magnet": True},
            "freshness": {"confidence_score": 0.9, "is_stale": False},
            "architectural_layer": {"name": "CLI", "description": "d" * 150},
            "skeleton": {
                "mode": "smart", "tokens": 100, "full_tokens": 400,
                "pct_of_full": 25.0, "bodies_kept": ["f"], "text": "z" * 10000,
                "verified": True, "auto": True, "opt_out_hint": "h" * 100,
            },
            "episodes": 4,
        }
    },
    "truncated": False,
    "dropped_targets": ["b.py"],
    "dropped_symbols": {},
    "_meta": {"timing_ms": 3.0, "indexed_commit": "abc123", "live_head": "abc123",
              "index_behind": False, "index_age_days": 0},
}

SYMBOL_PAYLOAD = {
    "symbol_id": "a.py::f",
    "file": "a.py",
    "name": "f",
    "kind": "function",
    "qualified_name": "a.f",
    "signature": "def f() -> None",
    "language": "python",
    "start_line": 1,
    "end_line": 3,
    "symbol_start_line": 1,
    "symbol_end_line": 3,
    "source": "   1  def f() -> None:\n   2      return None",
    "truncated": True,
    "continuation": "a.py:4-40",
    "verified": True,
    "_meta": {"timing_ms": 1.0, "replaced_tokens": 9, "indexed_commit": "abc123",
              "live_head": "abc123", "index_behind": False},
}


def _decision(n: int) -> dict:
    return {
        "id": f"id{n}", "title": f"Decision {n}", "status": "active",
        "decision": "do the thing", "rationale": "because",
        "context": "c" * 300, "consequences": ["x", "y"], "alternatives": [],
        "lineage": [], "confidence": 0.9, "staleness_score": 0.1,
        "affected_files": [f"f{i}.py" for i in range(20)],
        "affected_files_total": 20,
    }


WHY_PATH_PAYLOAD = {
    "mode": "path",
    "path": "a.py",
    "decisions": [_decision(n) for n in range(8)],
    "decisions_total": 8,
    "alignment": {"score": "B", "explanation": "mostly governed"},
    "origin_story": {
        "available": True, "primary_author": "Raghav", "author_commit_pct": 80.0,
        "total_commits": 12, "first_commit": "2026-01-01", "last_commit": "2026-08-01",
        "age_days": 200, "summary": "s" * 2000,
        "contributors": [{"name": "Raghav", "email": "r@e", "commit_count": 10}],
        "key_commits": [
            {"sha": f"sha{i}", "date": "2026-01-01", "message": f"m{i}",
             "author": "Raghav", "body": "b" * 300}
            for i in range(7)
        ],
    },
    "episodes": [{"tier": "git", "kind": "code_fix", "subject": "s", "recorded": "r",
                  "evidence": "e", "scope": ["a.py"], "still_true": "yes"}],
    "truncated": True,
    "omission_marker": "[repowise#abc]",
    "_meta": {"indexed_commit": "abc123", "live_head": "def456",
              "stale_warning": "index is behind"},
}

WHY_DASHBOARD_PAYLOAD = {
    "mode": "health",
    "summary": "37 active",
    "counts": {"active": 37, "stale": 14},
    "stale_decisions": [{"id": f"s{i}", "title": f"Stale {i}"} for i in range(10)],
    "proposed_awaiting_review": [],
    "ungoverned_hotspots": [f"h{i}.md" for i in range(15)],
    "conflicts": [],
    "_meta": {"indexed_commit": "abc123", "live_head": "abc123"},
}


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """An indexed-looking repo the commands will accept."""
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _spy_run(payload: dict, calls: list):
    """Stand in for ``_tool_adapters.run``, but call the factory for real.

    The factory is where each command imports its tool function and binds the
    arguments, so calling it is what proves the wiring. Its coroutine is closed
    rather than awaited — running it would need a database.
    """

    def _run(repo_path, factory):
        coro = factory()
        calls.append((repo_path, coro.cr_code.co_qualname if hasattr(coro, "cr_code") else ""))
        coro.close()
        return payload

    return _run


def _invoke(monkeypatch, command, args, repo, payload, calls=None, expect_exit=0):
    monkeypatch.setattr(_ta, "run", _spy_run(payload, calls if calls is not None else []))
    result = CliRunner(mix_stderr=False).invoke(
        command, [*args, "--path", str(repo), "--no-workspace"]
    )
    assert result.exit_code == expect_exit, result.output + (result.stderr or "")
    return result


# --------------------------------------------------------------------------
# The projections — the field mapping, pinned
# --------------------------------------------------------------------------


def test_ask_projection_drops_the_bulk_and_keeps_the_evidence():
    out = project_ask(ANSWER_PAYLOAD, "q?")
    assert out["answer"] == ANSWER_PAYLOAD["answer"]
    assert out["citations"] == ANSWER_PAYLOAD["citations"]
    assert out["quotes"][0]["quote"] == "NON_TTY_WIDTH = 400"
    for dropped in ("retrieval", "candidates", "symbol_bodies", "_meta"):
        assert dropped not in out, f"{dropped} survived the trim"
    # The freshness half of _meta survives; the timing half does not.
    assert out["index"] == {"indexed_commit": "abc123", "live_head": "def456",
                            "index_behind": True, "index_age_days": 2}


def test_ask_projection_strips_the_excerpt_from_every_best_guess():
    """The abstain path carries a 1,500-char excerpt per guess.

    Dropping ``retrieval`` but keeping ``best_guesses`` whole would make the
    low-confidence answer the *largest* thing this command emits.
    """
    out = project_ask(ANSWER_PAYLOAD, "q?")
    assert out["best_guesses"] == [
        {"file": "a.py", "why_relevant": "names the constant", "score": 2.1}
    ]
    assert "excerpt" not in json.dumps(out)
    assert out["next_action_hint"] == "Read a.py first."


def test_ask_projection_is_a_fraction_of_the_payload():
    trimmed = len(json.dumps(project_ask(ANSWER_PAYLOAD, "q?")))
    full = len(json.dumps(ANSWER_PAYLOAD))
    assert trimmed * 3 < full, f"trim saved too little: {trimmed} of {full}"


def test_context_projection_keeps_the_skeleton_shape_without_its_text():
    out = project_context(CONTEXT_PAYLOAD, ("a.py",))
    card = out["targets"]["a.py"]
    assert card["title"] == "File: a.py"
    assert card["summary"] == "Does a thing."
    assert card["layer"] == "CLI"
    assert card["hotspot"] is True
    assert card["fix_history"]["bug_magnet"] is True
    assert card["stale"] is False
    assert card["episodes"] == 4
    assert card["skeleton"] == {
        "mode": "smart", "tokens": 100, "full_tokens": 400,
        "pct_of_full": 25.0, "verified": True, "bodies_kept": ["f"],
    }
    assert "z" * 100 not in json.dumps(out), "skeleton text survived the trim"
    assert "parent_page" not in card


def test_context_projection_keeps_a_targets_own_error():
    """A card carrying only ``error`` must not trim down to an empty card.

    An empty card reads as "indexed, nothing to say"; the error says the path
    does not exist. Found by running the command against a typo.
    """
    payload = {
        "targets": {"nope.py": {"target": "nope.py", "error": "Target not found: 'nope.py'"}},
        "_meta": {},
    }
    card = project_context(payload, ("nope.py",))["targets"]["nope.py"]
    assert card == {"target": "nope.py", "error": "Target not found: 'nope.py'"}


def test_context_projection_reports_a_target_the_tool_never_mentioned():
    """A typo'd path must not look like a path with nothing to say."""
    out = project_context(CONTEXT_PAYLOAD, ("a.py", "b.py", "typo.py"))
    # b.py is in dropped_targets, so it is accounted for; typo.py is not.
    assert out["dropped_targets"] == ["b.py"]
    assert out["not_found"] == ["typo.py"]


def test_symbol_projection_keeps_the_body_and_the_continuation():
    """``symbol``'s payload *is* its answer, so only the envelope is dropped."""
    out = project_symbol(SYMBOL_PAYLOAD)
    assert out["source"] == SYMBOL_PAYLOAD["source"]
    assert out["continuation"] == "a.py:4-40"
    assert out["truncated"] is True
    assert "_meta" not in out
    assert out["index"]["indexed_commit"] == "abc123"


def test_why_path_projection_caps_lists_and_says_what_it_capped():
    out = project_why(WHY_PATH_PAYLOAD)
    assert len(out["decisions"]) == 5
    assert out["decisions_total"] == 8
    decision = out["decisions"][0]
    assert decision["rationale"] == "because"
    assert len(decision["affected_files"]) == 5
    assert decision["affected_files_total"] == 20
    for dropped in ("context", "consequences", "alternatives", "lineage",
                    "confidence", "staleness_score"):
        assert dropped not in decision


def test_why_path_projection_drops_the_prose_retelling_and_commit_bodies():
    out = project_why(WHY_PATH_PAYLOAD)
    origin = out["origin_story"]
    assert origin["primary_author"] == "Raghav"
    assert "summary" not in origin, "the 2K-char origin prose survived"
    assert "contributors" not in origin
    assert len(origin["key_commits"]) == 5
    assert all("body" not in c for c in origin["key_commits"])
    assert out["truncated"] is True
    assert out["omission_marker"] == "[repowise#abc]"
    assert out["index"]["stale_warning"] == "index is behind"


def test_why_dashboard_projection_caps_each_list_with_its_total():
    out = project_why(WHY_DASHBOARD_PAYLOAD)
    assert out["counts"] == {"active": 37, "stale": 14}
    assert len(out["stale_decisions"]) == 5
    assert out["stale_decisions_total"] == 10
    assert len(out["ungoverned_hotspots"]) == 5
    assert out["ungoverned_hotspots_total"] == 15
    # Empty lists are not reported as capped-to-zero.
    assert "conflicts" not in out
    assert "proposed_awaiting_review" not in out


# --------------------------------------------------------------------------
# --full, and the commands end to end
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "args", "payload"),
    [
        (ask_command, ["q?"], ANSWER_PAYLOAD),
        (context_command, ["a.py"], CONTEXT_PAYLOAD),
        (symbol_command, ["a.py::f"], SYMBOL_PAYLOAD),
        (why_command, ["a.py"], WHY_PATH_PAYLOAD),
    ],
)
def test_full_emits_the_raw_tool_dict_without_asking_for_json(
    monkeypatch, repo, command, args, payload
):
    """``--full`` implies json — a raw tool dict has no table rendering."""
    result = _invoke(monkeypatch, command, [*args, "--full"], repo, payload)
    assert json.loads(result.stdout) == payload


@pytest.mark.parametrize(
    ("command", "args", "payload"),
    [
        (ask_command, ["q?"], ANSWER_PAYLOAD),
        (context_command, ["a.py"], CONTEXT_PAYLOAD),
        (symbol_command, ["a.py::f"], SYMBOL_PAYLOAD),
        (why_command, ["a.py"], WHY_PATH_PAYLOAD),
    ],
)
def test_json_is_the_projection_and_stdout_is_one_document(
    monkeypatch, repo, command, args, payload
):
    result = _invoke(monkeypatch, command, [*args, "--format", "json"], repo, payload)
    parsed = json.loads(result.stdout)
    assert parsed != payload, "--format json emitted the raw dict, not the projection"
    assert len(result.stdout) < len(json.dumps(payload, indent=2))


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (ask_command, ["q?"]),
        (context_command, ["a.py"]),
        (symbol_command, ["a.py::f"]),
        (why_command, ["a.py"]),
    ],
)
def test_a_tool_error_still_leaves_a_document_on_stdout(monkeypatch, repo, command, args):
    """An empty stdout is indistinguishable from a crash to whatever reads it.

    The document is emitted *and* the exit code is non-zero: a failed lookup
    that exits 0 reads as a hit to any script checking only the status.
    """
    result = _invoke(
        monkeypatch,
        command,
        [*args, "--format", "json"],
        repo,
        {"error": "no such repo"},
        expect_exit=1,
    )
    assert json.loads(result.stdout)["error"] == "no such repo"


def test_a_tool_error_is_rewritten_into_commands_a_cli_user_can_run(monkeypatch, repo):
    """``get_symbol``'s not-found hint names get_context, which is not a command."""
    result = _invoke(
        monkeypatch,
        symbol_command,
        ["a.py::Nope"],
        repo,
        {"error": "Symbol not found. Use get_context to list available symbols."},
        expect_exit=1,
    )
    printed = result.stdout + (result.stderr or "")
    assert "repowise context" in printed
    assert "get_context" not in printed


def test_the_json_error_payload_keeps_the_tools_own_vocabulary(monkeypatch, repo):
    """A json consumer is reading the tool's projection, tool names and all."""
    result = _invoke(
        monkeypatch,
        symbol_command,
        ["a.py::Nope", "--format", "json"],
        repo,
        {"error": "Use get_context to list available symbols."},
        expect_exit=1,
    )
    assert "get_context" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (ask_command, ["q?"]),
        (context_command, ["a.py"]),
        (symbol_command, ["a.py::f"]),
        (why_command, ["a.py"]),
    ],
)
def test_an_empty_result_still_renders_on_the_table_path(monkeypatch, repo, command, args):
    result = _invoke(monkeypatch, command, args, repo, {"_meta": {}})
    assert result.stdout.strip(), "table path printed nothing at all"


@pytest.mark.parametrize(
    ("command", "args", "tool_module"),
    [
        (ask_command, ["q?"], "repowise.server.mcp_server.tool_answer"),
        (context_command, ["a.py"], "repowise.server.mcp_server.tool_context"),
        (symbol_command, ["a.py::f"], "repowise.server.mcp_server.tool_symbol"),
        (why_command, ["a.py"], "repowise.server.mcp_server.tool_why"),
    ],
)
def test_the_command_really_builds_its_tool_coroutine(
    monkeypatch, repo, command, args, tool_module
):
    """The tool import lives inside the factory, so only calling it proves it.

    A wrong module or attribute name there raises nothing until the command
    runs against a real repo — the same class of defect the lazy command table
    has its own test for.
    """
    calls: list = []
    _invoke(monkeypatch, command, args, repo, {"_meta": {}}, calls=calls)
    assert len(calls) == 1
    assert calls[0][0] == repo
    import sys

    assert tool_module in sys.modules


def test_an_unindexed_repo_is_refused_before_any_tool_runs(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(_ta, "run", _spy_run({}, calls))
    result = CliRunner(mix_stderr=False).invoke(
        ask_command, ["q?", "--path", str(tmp_path), "--no-workspace"]
    )
    assert result.exit_code != 0
    assert "not indexed" in (result.stderr or result.output)
    assert calls == []


@pytest.mark.parametrize("args", [[], ["--format", "json"], ["--full"]])
def test_logs_are_silenced_at_every_format_not_only_the_machine_ones(
    monkeypatch, repo, args
):
    """``ask`` synthesises through a provider that logs to *stdout*.

    ``format_option``'s callback only fires for a machine-readable format, so
    it would leave three timestamped structlog lines in the middle of the human
    answer — and inside anything reading it through ``repowise distill``.
    """
    silenced: list = []
    monkeypatch.setattr(
        "repowise.cli.helpers.silence_logs_for_machine_output",
        lambda: silenced.append(True),
    )
    monkeypatch.setattr("repowise.cli.tool_bridge.call_tool", lambda p, f: {"_meta": {}})
    result = CliRunner(mix_stderr=False).invoke(
        ask_command, ["q?", *args, "--path", str(repo), "--no-workspace"]
    )
    assert result.exit_code == 0, result.output
    assert silenced, "the tool call ran with logging still pointed at stdout"


def test_ask_echoes_the_answer_verbatim_rather_than_rendering_markdown(monkeypatch, repo):
    """Rich would centre and pad every heading to the 400-column non-TTY width.

    Echoing the source keeps the bytes, the markdown, and the grep.
    """
    payload = {**ANSWER_PAYLOAD, "answer": "## A heading\n\nA [bracketed] body."}
    result = _invoke(monkeypatch, ask_command, ["q?"], repo, payload)
    assert "## A heading" in result.stdout
    assert "[bracketed]" in result.stdout


def test_why_renders_a_dominant_author_as_a_percentage_not_a_fraction():
    """``author_commit_pct`` is a 0-1 fraction or a 0-100 percentage.

    Its source stores either, so printing it raw shows a sole author as
    "0.99%".
    """
    from repowise.cli.commands.why_cmd import _owner_share

    assert _owner_share(0.9956) == "100%"
    assert _owner_share(80.0) == "80%"
    assert _owner_share(None) == "?"
