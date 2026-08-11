"""``repowise search`` and ``repowise risk --target`` as MCP-tool adapters.

``search`` used to carry its own FTS query, its own LanceDB query, its own
``LIKE`` over ``wiki_symbols`` and its own workspace fan-out. It now calls
``search_codebase``; ``risk`` gained ``--target``, which calls ``get_risk``.
The same three things have to hold as for the other adapters:

1. **The projection is the contract**, and the fixtures below are built from
   the tools' response-construction code rather than from the projections. A
   fixture shaped to the projection omits exactly the keys the projection
   drops, so every dropped-answer bug passes it.
2. **A kept key owes a renderer.** A trimmed projection has two silent failure
   modes — a key dropped from the payload, and a key kept that nothing prints —
   and only the first is visible to a ``project()`` test. So the renderers are
   driven through ``CliRunner`` as well.
3. **Every exit emits a document**, and an error exits non-zero.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.commands.risk_cmd import project_risk, risk_command
from repowise.cli.commands.search_cmd import project, search_command

# --------------------------------------------------------------------------
# Payloads shaped like the real tools build them
# --------------------------------------------------------------------------

PAGE_HIT = {
    "page_id": "file:packages/cli/src/repowise/cli/output.py",
    "title": "File: packages/cli/src/repowise/cli/output.py",
    "page_type": "file_page",
    "snippet": "# " + "s" * 200,
    "relevance_score": 3.1416,
    "sources": ["fts", "vector"],
    "target_path": "packages/cli/src/repowise/cli/output.py",
    "confidence_score": 1.0,
}

SYMBOL_HIT = {
    "type": "symbol",
    "symbol_id": "packages/cli/src/repowise/cli/output.py::resolve_console_width",
    "name": "resolve_console_width",
    "kind": "function",
    "file": "packages/cli/src/repowise/cli/output.py",
    "start_line": 44,
    "end_line": 72,
    "signature": "def resolve_console_width(stream) -> int | None",
    "qualified_name": "repowise.cli.output.resolve_console_width",
    "language": "python",
    "score": 18.0,
    "next": "get_symbol",
}

FILE_HIT = {
    "type": "file",
    "page_id": "file:packages/cli/src/repowise/cli/helpers.py",
    "title": "File: packages/cli/src/repowise/cli/helpers.py",
    "file": "packages/cli/src/repowise/cli/helpers.py",
    "score": 140.0,
    "next": "get_context",
}

META = {
    "timing_ms": 12.5,
    "indexed_commit": "abc123",
    "live_head": "def456",
    "index_behind": True,
    "index_age_days": 2,
}

CONCEPT_PAYLOAD = {
    "results": [PAGE_HIT],
    "candidates": [{"path": "packages/cli/src/repowise/cli/output.py"}],
    "_meta": META,
}

SYMBOL_PAYLOAD = {
    "results": [SYMBOL_HIT],
    "mode": "symbol",
    "candidates": [{"path": "packages/cli/src/repowise/cli/output.py"}],
    "exact_match": True,
    "_meta": META,
}

#: The shape a *miss* takes: no exact symbol, so the tool attaches the note
#: that stops a caller anchoring on a fuzzy neighbour, and a grep hint.
SYMBOL_MISS_PAYLOAD = {
    "results": [],
    "mode": "symbol",
    "exact_match": False,
    "note": (
        "No indexed symbol exactly matches 'reslove_width'. The results are fuzzy "
        "neighbours ranked by token overlap — confirm a hit names what you meant."
    ),
    "grep_hint": (
        "No indexed match for identifier 'reslove_width'. Retry with mode=\"symbol\"; "
        "if you need every literal usage, Grep is the right tool for that."
    ),
    "_meta": META,
}

HYBRID_PAYLOAD = {"results": [SYMBOL_HIT, {**PAGE_HIT, "type": "page"}], "mode": "hybrid"}

PATH_PAYLOAD = {"results": [FILE_HIT], "mode": "path"}

RISK_PAYLOAD = {
    "targets": {
        "packages/core/src/repowise/core/pipeline/persist.py": {
            "target": "packages/core/src/repowise/core/pipeline/persist.py",
            "hotspot_score": 1.0,
            "dependents_count": 52,
            "co_change_partners": [
                {
                    "file_path": "packages/core/src/repowise/core/persistence/models.py",
                    "count": 19.13,
                    "last_co_change": "2026-08-01",
                    "has_import_link": True,
                }
            ],
            "primary_owner": "Raghav Chamadiya",
            # Stored as a fraction here; the other git-metadata path stores a
            # percentage, and a raw render shows a sole owner as "0.75%".
            "owner_pct": 0.75,
            "recent_owner": "Raghav Chamadiya",
            "recent_owner_pct": 1.0,
            "bus_factor": 1,
            "contributor_count": 2,
            "trend": "increasing",
            "risk_type": "bug-prone",
            "change_pattern": "fix-heavy",
            "change_magnitude": {"lines_added_90d": 900, "lines_deleted_90d": 400,
                                 "avg_commit_size": 61.2},
            "impact_surface": {"transitive_dependents": ["x.py"] * 200},
            # A {name: fix_count} dict, not a list — `_top_fix_symbols` builds
            # it from the persisted counts JSON.
            "defect_profile": {"fix_count": 22, "last_fix_days_ago": 1, "bug_magnet": True,
                               "top_symbols": {"_prune_stale_file_rows": 9,
                                               "mark_tombstone_pages": 4}},
            "health_score": 3.2,
            "coverage_pct": 61.0,
            "top_biomarkers": [
                {"biomarker_type": "nested_complexity", "severity": "high",
                 "function_name": "persist_analysis", "impact": 0.71},
                {"biomarker_type": "coverage_gradient", "severity": "low",
                 "function_name": None, "impact": 0.58},
            ],
            "test_gap": False,
            "security_signals": [{"kind": "sql-injection", "severity": "high",
                                  "snippet": "sa_text(f'...')"}],
            "commit_count_capped": False,
            "episodes": 22,
            "_base_dep_count": 52,
            "risk_summary": "persist.py — 22 bug fixes in 6mo, hotspot score 100% (increasing)",
        }
    },
    "global_hotspots": [
        {"file_path": "packages/core/src/repowise/core/pipeline/incremental.py",
         "hotspot_score": 0.99, "primary_owner": "Raghav Chamadiya", "fix_count": 17}
    ],
    "_meta": META,
}

PR_RISK_PAYLOAD = {
    "targets": RISK_PAYLOAD["targets"],
    "directive": {
        "will_break": ["packages/core/src/repowise/core/pipeline/orchestrator.py"],
        "will_break_tests": ["tests/unit/persistence/test_models.py"],
        "missing_cochanges": ["packages/core/src/repowise/core/persistence/models.py"],
        "missing_tests": ["packages/core/src/repowise/core/pipeline/persist.py"],
        "tests_to_run": ["tests/unit/pipeline/test_persist.py::test_tombstone"],
        # Every dict-valued block below is keyed the way its construction site
        # in tool_risk/directives.py keys it. Inventing a `summary` key here is
        # what let four of the six render as raw Python dict reprs: the fixture
        # was written to match the renderer's fallback chain, not the tool.
        "will_break_consumers": [
            {"repo": "backend", "service": "billing-api", "distance": 1, "score": 0.8,
             "via": "import"}
        ],
        "missing_cross_repo_cochanges": [{"repo": "web", "service": "ui", "score": 0.4}],
        "breaking_changes": [
            {"contract_id": "api:GET /v1/x", "type": "openapi", "kind": "removed_route",
             "severity": "high", "detail": "route removed",
             "impacted_consumers": [{"repo": "web", "service": "ui", "file": "a.ts"}]}
        ],
        "conformance_violations": [
            {"source": "cli", "target": "core", "rule": "cli !-> core",
             "edge_kind": "import", "description": "layering rule"}
        ],
        "dependency_cycles": [{"nodes": ["a.py", "b.py", "a.py"], "length": 2}],
        "governance_risk": [
            {"file": "persist.py", "decision_id": "dr-7", "title": "persist tombstones",
             "status": "accepted", "reason": "stale_governance"}
        ],
        "overall_risk_score": 7.4,
        "summary": "PR touches 1 file(s). ~1 downstream file(s) likely affected.",
    },
    "pr_blast_radius": {
        "transitive_affected": ["packages/core/src/repowise/core/pipeline/orchestrator.py"],
        "recommended_reviewers": [{"name": "Raghav Chamadiya", "commits": 40}],
        "test_gaps": ["packages/core/src/repowise/core/pipeline/persist.py"],
        "overall_risk_score": 7.4,
    },
    "_meta": META,
}


# --------------------------------------------------------------------------
# Helpers, matching test_tool_adapter_commands.py
# --------------------------------------------------------------------------


def _spy_run(payload, calls: list):
    """Stand in for ``_tool_adapters.run``, but call the factory for real.

    The factory is where the command imports its tool function and binds the
    arguments, so calling it is what proves the wiring. A typo in the import is
    otherwise invisible until the command runs against a real repo.
    """

    def _run(repo_path, factory, tool_name):
        coro = factory()
        calls.append((repo_path, coro.cr_code.co_qualname, tool_name))
        coro.close()
        return payload(repo_path) if callable(payload) else payload

    return _run


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _invoke(monkeypatch, command, args, repo, payload, calls=None, expect_exit=0):
    """``risk`` takes its repo as ``--path``; it has no ``--no-workspace``."""
    monkeypatch.setattr(_ta, "run", _spy_run(payload, calls if calls is not None else []))
    result = CliRunner(mix_stderr=False).invoke(command, [*args, "--path", str(repo)])
    assert result.exit_code == expect_exit, result.output + (result.stderr or "")
    return result


def _search(monkeypatch, args, repo, payload, calls=None, expect_exit=0):
    """``search`` takes its repo as a trailing positional, not ``--path``."""
    monkeypatch.setattr(_ta, "run", _spy_run(payload, calls if calls is not None else []))
    result = CliRunner(mix_stderr=False).invoke(
        search_command, [*args, str(repo), "--no-workspace"]
    )
    assert result.exit_code == expect_exit, result.output + (result.stderr or "")
    return result


# --------------------------------------------------------------------------
# search — the projection
# --------------------------------------------------------------------------


def test_page_results_keep_exactly_the_payload_this_command_always_emitted():
    """The collapse is payload-neutral by default, which is the whole point:
    ``search`` is the command the session-cost eval's CLI arm measured."""
    out = project(CONCEPT_PAYLOAD, "width", multi=False)

    assert out["results"] == [
        {
            "type": "page",
            "score": 3.1416,
            "title": PAGE_HIT["title"],
            "page_type": "file_page",
            "path": "packages/cli/src/repowise/cli/output.py",
            "snippet": PAGE_HIT["snippet"],
        }
    ]
    for dropped in ("page_id", "sources", "confidence_score"):
        assert dropped not in json.dumps(out["results"]), f"{dropped} survived the trim"


def test_a_symbol_spotlight_hit_carries_the_openable_file_beside_its_page_id():
    """``target_path`` on a symbol_spotlight is ``a.py::Foo`` — a page id, not
    something a reader can open — which is why the tool attaches ``file``."""
    hit = {**PAGE_HIT, "page_type": "symbol_spotlight",
           "target_path": "packages/cli/src/repowise/cli/output.py::resolve_console_width",
           "file": "packages/cli/src/repowise/cli/output.py"}
    row = project({"results": [hit]}, "q", multi=False)["results"][0]

    assert row["path"].endswith("::resolve_console_width"), "the payload changed shape"
    assert row["file"] == "packages/cli/src/repowise/cli/output.py"


def test_a_plain_file_hit_does_not_carry_a_redundant_file_key():
    assert "file" not in project(CONCEPT_PAYLOAD, "q", multi=False)["results"][0]


def test_a_bracketed_query_does_not_take_the_table_down(monkeypatch, repo):
    """A Table title is markup-parsed like any cell."""
    result = _search(monkeypatch, ["list[/bold]"], repo, CONCEPT_PAYLOAD)
    assert "list[/bold]" in result.output


def test_the_snippet_is_not_clipped_in_the_payload():
    """The 50-char clip fits a table column; a JSON consumer has no column."""
    out = project(CONCEPT_PAYLOAD, "width", multi=False)
    assert out["results"][0]["snippet"] == PAGE_HIT["snippet"]


def test_a_symbol_hit_keeps_the_id_that_makes_it_followable():
    out = project(SYMBOL_PAYLOAD, "resolve_console_width", multi=False)
    assert out["results"][0]["symbol_id"] == SYMBOL_HIT["symbol_id"]
    assert out["results"][0]["line"] == 44
    assert out["mode"] == "symbol"


def test_hybrid_rows_say_which_shape_they_are():
    """The old payload needed no ``type``: every row in a response was a page.
    A hybrid interleave mixes shapes, so without it a consumer cannot tell
    which keys a row even has."""
    out = project(HYBRID_PAYLOAD, "where is resolve_console_width", multi=False)
    assert [r["type"] for r in out["results"]] == ["symbol", "page"]


def test_a_path_hit_projects_to_a_path():
    out = project(PATH_PAYLOAD, "helpers.py", multi=False)
    assert out["results"] == [
        {"type": "file", "score": 140.0, "title": FILE_HIT["title"],
         "path": "packages/cli/src/repowise/cli/helpers.py"}
    ]


@pytest.mark.parametrize("key", ["candidates", "exact_match", "note", "grep_hint"])
def test_the_keys_that_change_the_answer_survive_the_trim(key):
    """Each of these changes what the answer *means*, not how big it is:
    which files to open, whether a symbol really matched, and the recovery
    path when nothing did."""
    payload = {**SYMBOL_MISS_PAYLOAD, "candidates": [{"path": "a.py"}], "exact_match": False}
    assert key in project(payload, "reslove_width", multi=False)


def test_exact_match_false_is_not_mistaken_for_absent():
    """``if payload.get(key)`` would drop the one value that matters."""
    out = project(SYMBOL_MISS_PAYLOAD, "reslove_width", multi=False)
    assert out["exact_match"] is False


def test_the_freshness_half_of_meta_survives_and_the_timing_half_does_not():
    out = project(CONCEPT_PAYLOAD, "width", multi=False)
    assert out["index"] == {"indexed_commit": "abc123", "live_head": "def456",
                            "index_behind": True, "index_age_days": 2}
    assert "timing_ms" not in json.dumps(out)


def test_an_absent_mode_means_concept_not_unknown():
    """Only the structured branches report a mode. The concept branch and the
    federated one are concept by construction and report nothing."""
    assert project({"results": []}, "q", multi=False)["mode"] == "concept"


# --------------------------------------------------------------------------
# search — the command, through the renderers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "sent"),
    [("fulltext", "concept"), ("semantic", "concept"), ("symbol", "symbol"),
     ("auto", "auto"), ("path", "path"), ("hybrid", "hybrid")],
)
def test_search_reaches_the_tool_with_the_mapped_mode(monkeypatch, repo, requested, sent):
    """The argument actually bound, read off the coroutine's own frame.

    Asserting only that ``search_codebase`` was reached would pass with the
    whole mode mapping deleted, which is the half of this collapse that decides
    what the command returns.
    """
    seen: dict = {}

    def _run(repo_path, factory, tool_name):
        coro = factory()
        seen.update(coro.cr_frame.f_locals)
        coro.close()
        return CONCEPT_PAYLOAD

    monkeypatch.setattr(_ta, "run", _run)
    result = CliRunner(mix_stderr=False).invoke(
        search_command, ["width", "--mode", requested, str(repo), "--no-workspace"]
    )
    assert result.exit_code == 0, result.output
    assert seen["mode"] == sent
    assert seen["query"] == "width"


@pytest.mark.parametrize("mode", ["fulltext", "semantic", "symbol", "auto", "concept",
                                  "path", "hybrid"])
def test_every_mode_is_accepted_by_the_command(monkeypatch, repo, mode):
    """The legacy spellings are still the documented ones; nobody's script
    breaks. The tool's own spellings are accepted alongside them."""
    _search(monkeypatch, ["width", "--mode", mode], repo, CONCEPT_PAYLOAD)


def test_the_table_path_prints_the_note_and_the_grep_hint(monkeypatch, repo):
    """A kept key that no renderer prints is the second silent failure mode of
    a trimmed projection, and the one a projection test cannot see."""
    result = _search(monkeypatch, ["reslove_width", "--mode", "symbol"], repo,
                     SYMBOL_MISS_PAYLOAD)
    assert "No indexed symbol exactly matches" in result.output
    assert "Retry with" in result.output


def test_the_grep_hint_is_rewritten_into_cli_vocabulary(monkeypatch, repo):
    """The tools write their hints for an agent holding the MCP surface."""
    payload = {**SYMBOL_MISS_PAYLOAD,
               "grep_hint": "Nothing matched; pipe the hit into get_symbol for its body."}
    result = _search(monkeypatch, ["x", "--mode", "symbol"], repo, payload)
    assert "repowise symbol" in result.output
    assert "get_symbol" not in result.output


def test_the_table_path_lists_the_files_to_read(monkeypatch, repo):
    result = _search(monkeypatch, ["width"], repo, CONCEPT_PAYLOAD)
    assert "Files to read" in result.output
    assert "packages/cli/src/repowise/cli/output.py" in result.output


def test_a_bracketed_snippet_is_not_eaten_by_rich(monkeypatch, repo):
    """A table cell is markup-parsed, so tool text has to be escaped: a
    snippet reading ``list[str]`` would otherwise render as ``list``, and a
    stray closing tag would raise MarkupError and take the command down."""
    payload = {"results": [{**PAGE_HIT, "snippet": "list[str] not dict[/x]"}]}
    result = _search(monkeypatch, ["width"], repo, payload)
    assert "list[str]" in result.output


def test_symbol_mode_renders_the_symbol_table_not_the_page_table(monkeypatch, repo):
    result = _search(monkeypatch, ["resolve_console_width", "--mode", "symbol"], repo,
                     SYMBOL_PAYLOAD)
    assert "Qualified Name" in result.output
    assert "repowise.cli.output.resolve_console_width" in result.output


def test_hybrid_renders_both_shapes(monkeypatch, repo):
    result = _search(monkeypatch, ["where is it", "--mode", "hybrid"], repo, HYBRID_PAYLOAD)
    assert "Qualified Name" in result.output, "the symbol group is missing"
    assert "Snippet" in result.output, "the page group is missing"


def test_json_emits_one_parseable_document(monkeypatch, repo):
    result = _search(monkeypatch, ["width", "--format", "json"], repo, CONCEPT_PAYLOAD)
    assert json.loads(result.output)["results"][0]["path"].endswith("output.py")


def test_full_emits_the_raw_tool_dict(monkeypatch, repo):
    result = _search(monkeypatch, ["width", "--full"], repo, CONCEPT_PAYLOAD)
    payload = json.loads(result.output)
    assert payload["results"][0]["page_id"] == PAGE_HIT["page_id"]
    assert payload["_meta"]["timing_ms"] == 12.5


def test_an_error_payload_emits_a_document_and_exits_one(monkeypatch, repo):
    """A json path that exits after only a stderr notice is indistinguishable
    from a crash to whatever is reading the pipe."""
    payload = {"error": "no index yet", "remedy": "Run 'repowise init'."}
    result = _search(monkeypatch, ["width", "--format", "json"], repo, payload,
                     expect_exit=1)
    assert json.loads(result.output)["error"] == "no index yet"


def test_full_also_exits_one_on_an_error(monkeypatch, repo):
    """``--full`` is exactly the spelling a script reaches for."""
    result = _search(monkeypatch, ["width", "--full"], repo, {"error": "no index yet"},
                     expect_exit=1)
    assert json.loads(result.output)["error"] == "no index yet"


def test_no_results_still_emits_a_document_and_says_so(monkeypatch, repo):
    result = _search(monkeypatch, ["zzz", "--format", "json"], repo, {"results": []})
    assert json.loads(result.output) == {"query": "zzz", "mode": "concept", "results": []}


def test_an_unindexed_repo_emits_a_document(monkeypatch, tmp_path):
    result = CliRunner(mix_stderr=False).invoke(
        search_command, ["q", str(tmp_path), "--no-workspace", "--format", "json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.output) == {"query": "q", "mode": "concept", "results": []}


# --------------------------------------------------------------------------
# search — the workspace fan-out
# --------------------------------------------------------------------------
#
# The tool federates through a workspace registry the CLI does not build, so
# ``--all`` calls it once per repo and fuses here. These drive ``_fan_out``
# directly: a real two-repo fan-out needs two indexed repos.


class _Notices:
    def __init__(self) -> None:
        self.said: list[str] = []

    def print(self, *args: object) -> None:
        self.said.append(" ".join(str(a) for a in args))


def _fan(monkeypatch, tmp_path, per_repo, *, limit=10, fmt="table", full=False, mode="fulltext"):
    """Run ``_fan_out`` over fake repos, one canned payload each."""
    from repowise.cli.commands import search_cmd

    paths = []
    for name in per_repo:
        p = tmp_path / name
        (p / ".repowise").mkdir(parents=True)
        paths.append(p)
    monkeypatch.setattr(
        search_cmd, "_run_search", lambda rp, q, lim, tm: per_repo[rp.name]
    )
    notices = _Notices()
    search_cmd._fan_out(paths, "q", limit, mode, "concept", fmt, full, notices)
    return notices


def _hit(path: str, score: float) -> dict:
    return {**PAGE_HIT, "target_path": path, "relevance_score": score}


def test_the_fan_out_fuses_on_rank_not_on_raw_score(monkeypatch, tmp_path, capsys):
    """Each repo scores its own list independently, so the numbers are only
    comparable within a repo. Sorting them together lets one repo's scale
    evict every hit from the other."""
    per_repo = {
        "api": {"results": [_hit("api/a.py", 0.9), _hit("api/b.py", 0.8)]},
        # Much larger raw scores: a score sort would take both of these first.
        "web": {"results": [_hit("web/a.py", 90.0), _hit("web/b.py", 80.0)]},
    }
    _fan(monkeypatch, tmp_path, per_repo, fmt="json", limit=2)

    rows = json.loads(capsys.readouterr().out)["results"]
    assert {r["repo"] for r in rows} == {"api", "web"}, "one repo evicted the other"


def test_the_fan_out_tags_every_row_with_its_repo(monkeypatch, tmp_path, capsys):
    per_repo = {"api": {"results": [_hit("a.py", 1.0)]}, "web": {"results": [_hit("b.py", 1.0)]}}
    _fan(monkeypatch, tmp_path, per_repo, fmt="json")

    assert sorted(r["repo"] for r in json.loads(capsys.readouterr().out)["results"]) == [
        "api",
        "web",
    ]


def test_the_fan_out_honours_the_limit_across_repos(monkeypatch, tmp_path, capsys):
    per_repo = {
        "api": {"results": [_hit(f"a{i}.py", 1.0) for i in range(5)]},
        "web": {"results": [_hit(f"w{i}.py", 1.0) for i in range(5)]},
    }
    _fan(monkeypatch, tmp_path, per_repo, fmt="json", limit=3)

    assert len(json.loads(capsys.readouterr().out)["results"]) == 3


def test_one_failing_repo_does_not_take_the_fan_out_down(monkeypatch, tmp_path, capsys):
    per_repo = {"api": {"error": "no index yet"}, "web": {"results": [_hit("b.py", 1.0)]}}
    notices = _fan(monkeypatch, tmp_path, per_repo, fmt="json")

    assert json.loads(capsys.readouterr().out)["results"], "the healthy repo lost its answer"
    assert any("search failed for api" in s for s in notices.said)


def test_every_repo_failing_is_an_error_not_an_empty_result(monkeypatch, tmp_path):
    """"Nothing matched" and "nothing could be searched" are different answers,
    and the second one is a failure the exit code has to carry."""
    import click

    per_repo = {"api": {"error": "no index yet"}, "web": {"error": "no index yet"}}
    with pytest.raises(click.exceptions.Exit) as excinfo:
        _fan(monkeypatch, tmp_path, per_repo, fmt="json")
    assert excinfo.value.exit_code == 1


def test_a_hint_survives_only_when_every_repo_agrees(monkeypatch, tmp_path, capsys):
    """A grep hint is attached only when *that* repo found nothing, so it is
    true of the fan-out only if no repo found anything."""
    per_repo = {
        "api": {"results": [], "grep_hint": "nothing matched"},
        "web": {"results": [_hit("b.py", 1.0)]},
    }
    _fan(monkeypatch, tmp_path, per_repo, fmt="json")

    assert "grep_hint" not in json.loads(capsys.readouterr().out)


def test_full_in_the_fan_out_returns_every_repos_own_payload(monkeypatch, tmp_path, capsys):
    """There is no single tool call here to return the payload *of*, so
    synthesising one would hand back a dict no tool ever produced."""
    per_repo = {"api": {"results": [_hit("a.py", 1.0)], "_meta": META}, "web": {"results": []}}
    _fan(monkeypatch, tmp_path, per_repo, full=True)

    out = json.loads(capsys.readouterr().out)
    assert set(out["repos"]) == {"api", "web"}
    assert out["repos"]["api"]["_meta"]["timing_ms"] == 12.5


def test_the_fan_out_reports_its_worst_repos_freshness(monkeypatch, tmp_path, capsys):
    """Without this the answer drawn from the most repos is the one that says
    nothing about staleness, while the single-repo path warns."""
    per_repo = {
        "api": {"results": [_hit("a.py", 1.0)], "_meta": {"index_age_days": 0}},
        "web": {"results": [], "_meta": {"index_behind": True, "indexed_commit": "aaa",
                                         "live_head": "bbb", "index_age_days": 9}},
    }
    _fan(monkeypatch, tmp_path, per_repo, fmt="json")

    index = json.loads(capsys.readouterr().out)["index"]
    assert index["index_behind"] is True
    assert index["index_age_days"] == 9


def test_a_bracketed_error_does_not_take_the_fan_out_down(monkeypatch, tmp_path, capsys):
    """A shaped error interpolates the exception verbatim, and exception text
    routinely carries brackets. Unescaped, a stray closing tag raises
    MarkupError and the command dies with an empty stdout.

    Driven through a *real* rich Console: a fake that just records strings
    never parses the markup, so it cannot see this bug at all.
    """
    import io

    from rich.console import Console

    from repowise.cli.commands import search_cmd

    buf = io.StringIO()
    real = Console(file=buf, width=200, no_color=True)
    per_repo = {"api": {"error": "bad type list[/x] here"},
                "web": {"results": [_hit("b.py", 1.0)]}}
    paths = []
    for name in per_repo:
        p = tmp_path / name
        (p / ".repowise").mkdir(parents=True)
        paths.append(p)
    monkeypatch.setattr(search_cmd, "_run_search", lambda rp, q, lim, tm: per_repo[rp.name])

    search_cmd._fan_out(paths, "q", 10, "fulltext", "concept", "json", False, real)

    assert json.loads(capsys.readouterr().out)["results"], "the healthy repo lost its answer"
    assert "list[/x]" in buf.getvalue()


def test_full_in_the_fan_out_exits_one_when_no_repo_answered(monkeypatch, tmp_path, capsys):
    import click

    per_repo = {"api": {"error": "no index yet"}}
    with pytest.raises(click.exceptions.Exit) as excinfo:
        _fan(monkeypatch, tmp_path, per_repo, full=True)
    assert excinfo.value.exit_code == 1
    assert "error" in json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------
# risk --target
# --------------------------------------------------------------------------


def test_risk_without_a_target_still_scores_a_revspec(monkeypatch, repo):
    """The collapse must not move the command's default subject."""
    called: list = []
    monkeypatch.setattr(
        "repowise.cli.commands.risk_cmd.score_live_change",
        lambda *a, **k: called.append(a) or (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = CliRunner(mix_stderr=False).invoke(risk_command, ["HEAD", "--path", str(repo)])
    assert called, "a bare `repowise risk` must still reach the change scorer"
    assert result.exit_code != 0  # our own boom


def test_risk_target_reaches_get_risk(monkeypatch, repo):
    calls: list = []
    _invoke(monkeypatch, risk_command, ["--target", "a.py"], repo, RISK_PAYLOAD, calls)
    assert calls[0][1] == "get_risk"


def test_risk_projection_keeps_the_card_and_drops_only_the_bulk():
    out = project_risk(RISK_PAYLOAD)
    card = out["targets"]["packages/core/src/repowise/core/pipeline/persist.py"]
    # A denylist: the failure mode of an allowlist is a silently discarded
    # answer, and this card is scalars rather than source.
    for kept in ("defect_profile", "co_change_partners", "security_signals", "test_gap",
                 "change_magnitude", "bus_factor", "trend", "risk_type", "episodes"):
        assert kept in card, f"{kept} was discarded"
    assert "impact_surface" not in card
    assert "_base_dep_count" not in card
    assert out["global_hotspots"] == RISK_PAYLOAD["global_hotspots"]
    assert out["index"]["indexed_commit"] == "abc123"


def test_risk_pr_mode_keeps_the_whole_directive():
    """The tool's own docstring tells a reader to lead with the directive, so
    every block of it is the answer rather than its size."""
    out = project_risk(PR_RISK_PAYLOAD)
    assert out["directive"] == PR_RISK_PAYLOAD["directive"]


def test_risk_table_renders_the_directive_first(monkeypatch, repo):
    result = _invoke(
        monkeypatch, risk_command,
        ["--target", "packages/core/src/repowise/core/pipeline/persist.py",
         "--changed-file", "packages/core/src/repowise/core/pipeline/persist.py"],
        repo, PR_RISK_PAYLOAD,
    )
    out = result.output
    # Unconditional: a guarded `assert A < B if cond else True` degrades to
    # `assert True` the moment rich wraps the line the guard looked for.
    assert out.index("Directive") < out.index("Co-changes with")
    for expected in ("Will break", "Tests to run", "Missing co-changes",
                     "Conformance violations", "cli !-> core", "layering rule",
                     "persist tombstones", "billing-api", "removed_route",
                     "a.py -> b.py -> a.py"):
        assert expected in out, f"{expected!r} is in the payload and nothing printed it"
    # The generic fallback used to print these blocks as Python dict reprs.
    assert "'contract_id':" not in out
    assert "'nodes':" not in out


def test_risk_table_normalises_the_ownership_share(monkeypatch, repo):
    """``owner_pct`` is a fraction *or* a percentage depending on which
    git-metadata path filled it in; printing it raw shows a sole owner
    as "0.75%"."""
    result = _invoke(monkeypatch, risk_command, ["--target", "a.py"], repo, RISK_PAYLOAD)
    assert "75%" in result.output
    assert "0.75%" not in result.output


def test_risk_table_prints_the_security_signal(monkeypatch, repo):
    result = _invoke(monkeypatch, risk_command, ["--target", "a.py"], repo, RISK_PAYLOAD)
    assert "sql-injection" in result.output


def test_risk_names_a_target_the_tool_returned_no_card_for(monkeypatch, repo):
    """An excluded path is filtered out before the tool sees it. Rendering
    nothing reads as "clean"; it is not, it is "not assessed"."""
    result = _invoke(monkeypatch, risk_command, ["--target", "vendor/x.py"], repo,
                     {"targets": {}, "_meta": META})
    assert "vendor/x.py" in result.output
    assert "not indexed" in result.output


def test_risk_target_json_is_one_document(monkeypatch, repo):
    result = _invoke(monkeypatch, risk_command, ["--target", "a.py", "--format", "json"],
                     repo, RISK_PAYLOAD)
    targets = json.loads(result.output)["targets"]
    assert list(targets) == ["packages/core/src/repowise/core/pipeline/persist.py"]


def test_risk_target_error_exits_one(monkeypatch, repo):
    result = _invoke(monkeypatch, risk_command, ["--target", "a.py", "--format", "json"],
                     repo, {"error": "no index yet"}, expect_exit=1)
    assert json.loads(result.output)["error"] == "no index yet"


def test_risk_projection_keeps_the_reviewers_nothing_else_names():
    """``pr_blast_radius`` is the only carrier of ``recommended_reviewers``,
    and the tool has already capped its noisy lists before the CLI sees it."""
    out = project_risk(PR_RISK_PAYLOAD)
    assert out["pr_blast_radius"]["recommended_reviewers"] == [
        {"name": "Raghav Chamadiya", "commits": 40}
    ]


def test_risk_table_renders_the_health_and_coverage_it_keeps(monkeypatch, repo):
    result = _invoke(monkeypatch, risk_command, ["--target", "a.py"], repo, RISK_PAYLOAD)
    assert "health 3.2/10" in result.output
    assert "coverage 61%" in result.output
    # Keyed ``biomarker_type``; a generic key guess prints the whole dict repr.
    assert "nested_complexity (high) in persist_analysis" in result.output
    assert "'biomarker_type':" not in result.output


def test_risk_table_keeps_the_fix_counts_on_top_symbols(monkeypatch, repo):
    """``top_symbols`` is a {name: count} dict; iterating it bare prints the
    names and throws the counts away."""
    result = _invoke(monkeypatch, risk_command, ["--target", "a.py"], repo, RISK_PAYLOAD)
    assert "_prune_stale_file_rows (x9)" in result.output


def test_risk_full_on_a_revspec_is_json_not_a_table(monkeypatch, repo):
    """``--full`` means "the complete payload, as JSON" on every command that
    has it; silently ignoring it hands a script asking for JSON a rich table."""
    from repowise.core.analysis.change_risk import ChangeRiskResult

    monkeypatch.setattr(
        "repowise.cli.commands.risk_cmd.change_risk_payload", lambda r: {"risk": 4.2}
    )
    monkeypatch.setattr(
        "repowise.cli.commands.risk_cmd.score_live_change",
        lambda *a, **k: _FakeChangeResult(),
    )
    result = CliRunner(mix_stderr=False).invoke(
        risk_command, ["HEAD", "--path", str(repo), "--full", "--baseline", "0"]
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert json.loads(result.output) == {"risk": 4.2}
    assert ChangeRiskResult is not None  # import is the contract this leans on


class _FakeFeatures:
    nf = 3
    ref = "HEAD"
    subject = "a commit"
    la = 10
    ld = 2
    nd = 1
    ns = 1
    entropy = 0.5
    exp = 40
    is_fix = False


class _FakeChangeResult:
    features = _FakeFeatures()
    risk = type("R", (), {"score": 4.2, "level": "moderate", "top_drivers": []})()
    percentile = None
    priority = None
    request_excludes: tuple = ()


def test_changed_file_without_a_target_is_a_usage_error(monkeypatch, repo):
    """It is a modifier on ``--target``, and silently scoring HEAD instead
    would answer a question nobody asked."""
    result = CliRunner(mix_stderr=False).invoke(
        risk_command, ["--changed-file", "a.py", "--path", str(repo)]
    )
    assert result.exit_code == 2
