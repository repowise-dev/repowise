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
    "grounding": "extracted",
    "note": "symbol_bodies carries the full live body — cite that directly.",
    "code_rationale": [{"path": "a.py", "lines": [1, 2], "comment": "c" * 400}],
    "more_definitions": ["a.py::g"],
    "omission_marker": "[repowise#ask1]",
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
            # Blocks only an --include can produce. They must survive the trim:
            # a flag that changes nothing the caller can see is not a flag.
            "ownership": {"primary_owner": "Raghav", "bus_factor": 1},
            "last_change": {"date": "2026-08-01", "author": "Raghav"},
            "decisions": [{"id": "d1", "title": "Decision 1", "rationale": "because"}],
            "community": {"id": 3, "neighbors": ["b.py"]},
            "callers": ["b.py::g"],
            "metrics": {"pagerank": 0.4},
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

#: The tool's *other* two response shapes. ``get_symbol`` serves three, and a
#: projection tested only against the first is a projection tested against the
#: implementation rather than the contract.
OMISSION_PAYLOAD = {
    "symbol_id": "repowise#a1b2c3d4e5f6",
    "ref": "a1b2c3d4e5f6",
    "kind": "omission",
    # ``source`` here is the *command* the content was banked from, not a body.
    "source": "git log --stat",
    "original_tokens": 4200,
    "content": "THE ACTUAL OMITTED TEXT",
    "created_at": "2026-08-10T00:00:00+00:00",
    "_meta": {"timing_ms": 1.0},
}

SUGGESTION_PAYLOAD = {
    "symbol_id": "x.py::login",
    "error": (
        "Symbol not found: 'x.py::login'. A symbol with this name exists at "
        "the path(s) below — retry with one of these exact symbol_ids."
    ),
    "suggestions": ["a/b.py::login", "c/d.py::login"],
    "_meta": {"timing_ms": 1.0},
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
    "dropped_decisions": ["id7"],
    "_meta": {"indexed_commit": "abc123", "live_head": "def456",
              "stale_warning": "index is behind"},
}

#: The path mode as it actually arrives for an *ungoverned* file: no decisions,
#: and the three fallback blocks the tool substitutes instead. This is the shape
#: behind "falls back to git archaeology, so it is never empty", and a
#: projection that only ever sees the governed shape cannot test it.
WHY_UNGOVERNED_PAYLOAD = {
    "mode": "path",
    "path": "a.py",
    "decisions": [],
    "alignment": {"score": "none", "explanation": "This file is ungoverned."},
    "origin_story": {"available": False},
    "git_archaeology": {
        "triggered": True,
        "summary": "No architectural decisions found for a.py, but git archaeology recovered 7.",
        "file_commits": [
            {"sha": f"sha{i}", "message": f"m{i}", "author": "Raghav", "date": "2026-01-01"}
            for i in range(7)
        ],
        "cross_references": [
            {"source_file": "b.py", "sha": "shax", "message": "mentions a.py",
             "author": "Raghav", "date": "2026-02-01", "matched_terms": ["a"]}
        ],
        "git_log": [{"sha": "shay", "message": "live", "author": "Raghav", "date": "2026-03-01"}],
    },
    "code_rationale": [{"path": "a.py", "lines": [10, 12], "comment": "why it is this way",
                        "matched_terms": ["why"]}],
    "target_context": {
        "b.py": {
            "governing_decisions": [{"title": "Decision 1", "status": "active"}],
            "origin": {"available": True, "primary_author": "Raghav", "total_commits": 3,
                       "age_days": 40, "summary": "s" * 2000},
        }
    },
    "_meta": {"indexed_commit": "abc123", "live_head": "abc123"},
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

    def _run(repo_path, factory, tool_name):
        coro = factory()
        calls.append((repo_path, coro.cr_code.co_qualname, tool_name))
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


def test_ask_projection_names_the_blocks_it_dropped():
    """``note`` points at blocks by name ("symbol_bodies carries the body").

    Keeping the note while silently removing what it names leaves a dangling
    instruction, so the trim reports which of them the tool actually returned.
    """
    out = project_ask(ANSWER_PAYLOAD, "q?")
    assert out["note"].startswith("symbol_bodies")
    assert set(out["dropped_blocks"]) == {
        "retrieval", "candidates", "symbol_bodies", "code_rationale", "more_definitions",
    }
    assert out["grounding"] == "extracted"
    assert out["omission_marker"] == "[repowise#ask1]"


def test_ask_projection_is_a_fraction_of_the_payload():
    trimmed = len(json.dumps(project_ask(ANSWER_PAYLOAD, "q?")))
    full = len(json.dumps(ANSWER_PAYLOAD))
    assert trimmed * 3 < full, f"trim saved too little: {trimmed} of {full}"


def test_context_projection_passes_a_requested_skeleton_through_whole():
    """A skeleton in the card was asked for by name, so it survives the trim.

    This used to strip ``skeleton.text``, and that was right while the tool
    auto-upgraded every file target above 80 lines: the text was 73-91% of the
    payload and nobody had requested it. The tool no longer auto-upgrades, so
    the only way a skeleton reaches this function is ``--include skeleton``,
    and trimming its text back out would make that flag inert — the same
    failure the include passthrough two tests below exists to prevent.
    """
    out = project_context(CONTEXT_PAYLOAD, ("a.py",))
    card = out["targets"]["a.py"]
    assert card["title"] == "File: a.py"
    assert card["summary"] == "Does a thing."
    assert card["layer"] == "CLI"
    assert card["hotspot"] is True
    assert card["fix_history"]["bug_magnet"] is True
    assert card["stale"] is False
    # ``is_stale: null`` means the tool could not judge, not "current".
    unknown = {"target": "a.py", "freshness": {"is_stale": None}}
    assert "stale" not in project_context({"targets": {"a.py": unknown}}, ("a.py",))["targets"]["a.py"]
    assert card["episodes"] == 4
    assert card["skeleton"] == CONTEXT_PAYLOAD["targets"]["a.py"]["skeleton"]
    assert "z" * 100 in card["skeleton"]["text"], "the requested source went missing"
    assert "parent_page" not in card


def test_context_projection_keeps_every_include_block():
    """A flag that changes nothing the caller can see is not a flag.

    Each ``--include`` lands under its own key, so an allowlist that misses
    one makes ``repowise context f.py --include ownership`` print exactly what
    a bare call prints.
    """
    card = project_context(CONTEXT_PAYLOAD, ("a.py",))["targets"]["a.py"]
    assert card["ownership"] == {"primary_owner": "Raghav", "bus_factor": 1}
    assert card["last_change"] == {"date": "2026-08-01", "author": "Raghav"}
    assert card["decisions"][0]["rationale"] == "because"
    assert card["community"] == {"id": 3, "neighbors": ["b.py"]}
    assert card["callers"] == ["b.py::g"]
    assert card["metrics"] == {"pagerank": 0.4}


def test_context_projection_keeps_a_tombstones_redirect():
    """The successor path is the whole point of a tombstone card."""
    payload = {
        "targets": {
            "old.py": {
                "target": "old.py",
                "error": "'old.py' was deleted or renamed after indexing",
                "successor_paths": ["new.py"],
                "hint": "Content moved; call get_context on 'new.py' instead.",
            }
        },
        "_meta": {},
    }
    card = project_context(payload, ("old.py",))["targets"]["old.py"]
    assert card["successor_paths"] == ["new.py"]
    assert "new.py" in card["hint"]


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


def test_symbol_projection_keeps_an_omission_refs_content():
    """``source`` on this shape is the command, not a body — ``content`` is.

    Keeping ``source`` and dropping ``content`` makes ``repowise symbol
    repowise#<ref>`` print the provenance label where the text should be, and
    the renderer prints it through the body branch, so it looks like one.
    """
    out = project_symbol(OMISSION_PAYLOAD)
    assert out["content"] == "THE ACTUAL OMITTED TEXT"
    assert out["ref"] == "a1b2c3d4e5f6"
    assert out["kind"] == "omission"
    assert out["original_tokens"] == 4200


def test_symbol_projection_keeps_the_did_you_mean_list():
    """The error ends 'retry with one of these exact symbol_ids'."""
    out = project_symbol(SUGGESTION_PAYLOAD)
    assert out["suggestions"] == ["a/b.py::login", "c/d.py::login"]


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


def test_why_projection_keeps_the_blocks_that_answer_an_ungoverned_path():
    """`get_why` is documented as never empty; the fallbacks are why.

    It sets git_archaeology and code_rationale *because* nothing governs the
    path. Dropping them leaves `repowise why <ungoverned file>` printing one
    alignment line and nothing else.
    """
    out = project_why(WHY_UNGOVERNED_PAYLOAD)
    arch = out["git_archaeology"]
    assert arch["summary"].startswith("No architectural decisions found")
    assert len(arch["file_commits"]) == 5, "the per-layer cap did not apply"
    assert arch["cross_references"][0]["source_file"] == "b.py"
    assert arch["git_log"][0]["sha"] == "shay"
    assert out["code_rationale"][0]["comment"] == "why it is this way"


def test_why_projection_keeps_target_context_and_trims_it_the_same_way():
    """--target's entire product is target_context. Dropping it inerts the flag."""
    out = project_why(WHY_UNGOVERNED_PAYLOAD)
    entry = out["target_context"]["b.py"]
    assert entry["governing_decisions"] == [{"title": "Decision 1", "status": "active"}]
    # The per-target origin carries the same ~2K-char prose as the top level.
    assert "summary" not in entry["origin"]
    assert entry["origin"]["primary_author"] == "Raghav"


def test_why_projection_keeps_the_handles_on_what_truncation_removed():
    out = project_why(WHY_PATH_PAYLOAD)
    assert out["omission_marker"] == "[repowise#abc]"
    assert out["dropped_decisions"] == ["id7"]


def test_why_projection_keeps_evidence_identity_and_provenance():
    ref = {
        "id": "ev_shared",
        "repository": "default",
        "kind": "commit",
        "commit": "a" * 40,
        "provenance": "historical",
        "source": "episode",
    }
    payload = {
        "mode": "search",
        "decisions": [
            {
                "id": "d1",
                "title": "Decision",
                "source": "session",
                "provenance": "human_decision",
                "evidence_refs": [ref],
                "restates": ["d0"],
            }
        ],
        "episodes": [
            {
                "kind": "code_fix",
                "provenance": "historical",
                "evidence_refs": [ref],
            }
        ],
        "git_archaeology": {
            "git_log": [
                {
                    "sha": "a" * 12,
                    "provenance": "historical",
                    "evidence_refs": [ref],
                }
            ]
        },
        "code_rationale": [
            {
                "path": "a.py",
                "lines": [1, 2],
                "comment": "because",
                "provenance": "extracted_rationale",
                "evidence_refs": [ref],
            }
        ],
    }

    out = project_why(payload)

    assert out["decisions"][0]["evidence_refs"] == [ref]
    assert out["decisions"][0]["restates"] == ["d0"]
    assert out["episodes"][0]["evidence_refs"] == [ref]
    assert out["git_archaeology"]["git_log"][0]["evidence_refs"] == [ref]
    assert out["code_rationale"][0]["evidence_refs"] == [ref]


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
    ("command", "args"),
    [
        (ask_command, ["q?"]),
        (context_command, ["a.py"]),
        (symbol_command, ["a.py::f"]),
        (why_command, ["a.py"]),
    ],
)
def test_full_exits_non_zero_on_an_error_too(monkeypatch, repo, command, args):
    """``--full`` is exactly the spelling a script reaches for."""
    result = _invoke(
        monkeypatch, command, [*args, "--full"], repo, {"error": "nope"}, expect_exit=1
    )
    assert json.loads(result.stdout) == {"error": "nope"}


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
    ("command", "args", "tool"),
    [
        (ask_command, ["q?"], "get_answer"),
        (context_command, ["a.py"], "get_context"),
        (symbol_command, ["a.py::f"], "get_symbol"),
        (why_command, ["a.py"], "get_why"),
    ],
)
def test_the_command_builds_the_coroutine_of_the_tool_it_names(
    monkeypatch, repo, command, args, tool
):
    """The tool import lives inside the factory, so only calling it proves it.

    A wrong module or attribute name there raises nothing until the command
    runs against a real repo — the same class of defect the lazy command table
    has its own test for. Asserting the coroutine's own qualname rather than
    "the module is in sys.modules" is what makes this order-independent: by
    the fourth parametrized case all four tool modules are imported, so a
    presence check would pass for a command wired to the wrong tool.
    """
    calls: list = []
    _invoke(monkeypatch, command, args, repo, {"_meta": {}}, calls=calls)
    assert len(calls) == 1
    repo_path, qualname, tool_name = calls[0]
    assert repo_path == repo
    assert qualname == tool
    # The same name reaches the bridge, which uses it to shape an internal
    # error the way the MCP failure shield would have.
    assert tool_name == tool


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
    monkeypatch.setattr("repowise.cli.tool_bridge.call_tool", lambda p, f, t: {"_meta": {}})
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


# --------------------------------------------------------------------------
# The renderers. A projection test cannot see a block that survives the trim
# and is then never printed, and "kept but never rendered" is a defect this
# file has already had to fix once.
# --------------------------------------------------------------------------


def test_context_renders_every_include_block_it_kept(monkeypatch, repo):
    result = _invoke(monkeypatch, context_command, ["a.py"], repo, CONTEXT_PAYLOAD)
    for expected in ("Ownership", "Last Change", "Community", "Metrics", "Raghav"):
        assert expected in result.stdout, f"{expected} survived the trim but was not rendered"


def test_context_renders_a_symbol_target(monkeypatch, repo):
    """A symbol target's whole card lives under ``docs``.

    Dropping ``docs`` wholesale projected it to ``{target, type}`` and printed
    a one-row table, on the id spelling ``repowise symbol``'s help points at.
    """
    payload = {
        "targets": {
            "a.py::Login": {
                "target": "a.py::Login",
                "type": "symbol",
                "docs": {
                    "name": "Login",
                    "signature": "class Login(Base)",
                    "docstring": "Session start.",
                    "file_path": "a.py",
                    "used_by": ["b.py"],
                },
            }
        },
        "_meta": {},
    }
    result = _invoke(monkeypatch, context_command, ["a.py::Login"], repo, payload)
    assert "class Login(Base)" in result.stdout
    assert "Session start." in result.stdout


def test_context_does_not_let_rich_eat_a_bracket_in_tool_text(monkeypatch, repo):
    """Rich markup-parses a table cell, so `list[str]` renders as `list`."""
    payload = {
        "targets": {
            "a.py": {
                "target": "a.py",
                "type": "file",
                "docs": {"summary": "Returns list[str], not dict[str, int]."},
            }
        },
        "_meta": {},
    }
    result = _invoke(monkeypatch, context_command, ["a.py"], repo, payload)
    assert "list[str]" in result.stdout
    assert "dict[str, int]" in result.stdout


def test_context_prints_the_marker_that_recovers_truncated_content(monkeypatch, repo):
    """The marker opens with a bracket, which rich deletes as a style tag."""
    marker = "[repowise#abc123: 120 lines omitted; restore: repowise expand abc123]"
    payload = {"targets": {}, "truncated": True, "omission_marker": marker, "_meta": {}}
    result = _invoke(monkeypatch, context_command, ["a.py"], repo, payload)
    assert "repowise#abc123" in result.stdout
    assert "repowise expand abc123" in result.stdout


def test_why_renders_the_archaeology_it_falls_back_to(monkeypatch, repo):
    result = _invoke(monkeypatch, why_command, ["a.py"], repo, WHY_UNGOVERNED_PAYLOAD)
    assert "Git archaeology" in result.stdout
    assert "mentions a.py" in result.stdout
    assert "5 of 7" in result.stdout, "the cap was applied but never disclosed"


def test_why_renders_a_targets_own_history(monkeypatch, repo):
    """``origin.summary`` exists only when there is *no* history.

    Rendering only that line left a target with real history showing its name
    and nothing else.
    """
    payload = {
        **WHY_UNGOVERNED_PAYLOAD,
        "target_context": {
            "b.py": {
                "governing_decisions": [],
                "origin": {"available": True, "primary_author": "Raghav",
                           "total_commits": 9, "age_days": 40, "summary": "s" * 2000},
            }
        },
    }
    result = _invoke(monkeypatch, why_command, ["a.py"], repo, payload)
    assert "9 commits over 40 days" in result.stdout
    assert "s" * 100 not in result.stdout, "the 2K-char prose reached the screen"


def test_why_does_not_say_nothing_recorded_under_what_it_just_recorded(monkeypatch, repo):
    """Search mode with --target sets none of the path-mode blocks."""
    payload = {
        "mode": "search",
        "query": "q",
        "code_rationale": [{"path": "b.py", "lines": [1, 2], "comment": "because"}],
        "target_context": {"b.py": {"governing_decisions": [], "origin": {"available": False}}},
        "_meta": {},
    }
    result = _invoke(monkeypatch, why_command, ["q", "--target", "b.py"], repo, payload)
    assert "because" in result.stdout
    assert "Nothing recorded" not in result.stdout


def test_symbol_renders_an_omission_refs_banked_content(monkeypatch, repo):
    result = _invoke(
        monkeypatch, symbol_command, ["repowise#a1b2c3d4e5f6"], repo, OMISSION_PAYLOAD
    )
    assert "THE ACTUAL OMITTED TEXT" in result.stdout
    assert "git log --stat" in result.stdout


def test_symbol_does_not_wrap_a_long_source_line(monkeypatch, repo):
    """``console.print`` wraps at the console width even with markup off.

    A wrapped body strands the ``   1  `` prefix on its own row, which is not
    the file.
    """
    long_line = "   1  " + "x" * 600
    payload = {**SYMBOL_PAYLOAD, "source": long_line, "truncated": False}
    result = _invoke(monkeypatch, symbol_command, ["a.py::f"], repo, payload)
    assert long_line in result.stdout


def test_a_did_you_mean_error_prints_its_suggestions(monkeypatch, repo):
    """The error's last words are 'retry with one of these exact symbol_ids'."""
    result = _invoke(
        monkeypatch, symbol_command, ["x.py::login"], repo, SUGGESTION_PAYLOAD, expect_exit=1
    )
    printed = result.stdout + (result.stderr or "")
    assert "a/b.py::login" in printed and "c/d.py::login" in printed


def test_an_unindexed_repos_shaped_error_prints_its_remedy(monkeypatch, repo):
    """The shield's ``remedy`` is the only part that says what to do."""
    payload = {
        "error": "This repository has no repowise index yet.",
        "remedy": "The user can build one by running 'repowise init --yes'.",
        "guidance": "Until an index exists, every repowise tool returns this.",
    }
    result = _invoke(monkeypatch, ask_command, ["q?"], repo, payload, expect_exit=1)
    printed = result.stdout + (result.stderr or "")
    assert "repowise init --yes" in printed
    assert "Until an index exists" in printed


def test_ask_renders_the_note_and_names_what_it_left_out(monkeypatch, repo):
    result = _invoke(monkeypatch, ask_command, ["q?"], repo, ANSWER_PAYLOAD)
    assert "symbol_bodies carries the full live body" in result.stdout
    assert "Not shown:" in result.stdout and "retrieval" in result.stdout
    assert "repowise#ask1" in result.stdout


def test_why_renders_a_dominant_author_as_a_percentage_not_a_fraction():
    """``author_commit_pct`` is a 0-1 fraction or a 0-100 percentage.

    Its source stores either, so printing it raw shows a sole author as
    "0.99%".
    """
    from repowise.cli.commands.why_cmd import _owner_share

    assert _owner_share(0.9956) == "100%"
    assert _owner_share(80.0) == "80%"
    assert _owner_share(None) == "?"
