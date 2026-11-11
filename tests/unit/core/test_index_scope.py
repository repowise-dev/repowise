from types import SimpleNamespace

from repowise.core.index_scope import file_page_scope, resolve_index_scope, stamp_index_scope


def test_legacy_scope_does_not_invent_completeness() -> None:
    scope = resolve_index_scope({"docs_enabled": True, "total_pages": 12}, {"commit_limit": 500})
    assert scope["run_mode"] == "unknown"
    assert scope["content_provenance"] == "unknown"
    assert scope["git_tier"] == "unknown"
    assert scope["git_commit_cap"] == 500
    assert scope["git_history_coverage"] is None
    assert scope["file_pages"]["generated"] is None
    assert scope["search"]["semantic"] == "unknown"


def test_scope_keeps_configured_caps_separate_from_achieved_counts() -> None:
    state = {"run_mode": "fast", "git_tier": "essential", "docs_mode": "none"}
    stamp_index_scope(
        state,
        {"commit_limit": 500, "max_file_pages": 2},
        file_pages=file_page_scope(
            configured_cap=2,
            eligible=5,
            generated_pages=[SimpleNamespace(page_type="file_page") for _ in range(2)],
        ),
        git_history_coverage={"files_eligible": 5, "files_measured": 3},
    )
    scope = resolve_index_scope(state, {"commit_limit": 999})
    assert scope["git_commit_cap"] == 500
    assert scope["git_history_coverage"]["files_measured"] == 3
    assert scope["file_pages"] == {
        "configured_cap": 2,
        "effective_cap": 2,
        "eligible": 5,
        "generated": 2,
        "omitted": 3,
    }


def test_scope_surfaces_degraded_features_as_unavailable() -> None:
    scope = resolve_index_scope(
        {
            "index_scope": {"analysis": {"unavailable": ["health"], "skipped": []}},
            "degraded": ["Execution flow: parser failed"],
        }
    )
    assert scope["analysis"]["unavailable"] == [
        "Execution flow: parser failed",
        "health",
    ]


def test_mixed_provenance_survives_resolution() -> None:
    # A wiki where only some pages carry model prose is "mixed", not "model".
    state = {"run_mode": "standard", "docs_mode": "llm"}
    stamp_index_scope(state, {}, content_provenance="mixed")
    assert resolve_index_scope(state, {})["content_provenance"] == "mixed"


def test_dropped_generated_files_are_named_in_the_scope() -> None:
    from repowise.core.index_scope import dropped_files_scope

    stats = SimpleNamespace(generated_files=[f"gen/f{i:02}.go" for i in range(25, -1, -1)])
    state: dict = {}
    stamp_index_scope(state, dropped_files=dropped_files_scope(stats))
    dropped = resolve_index_scope(state)["dropped_files"]["generated"]
    assert dropped["count"] == 26
    assert dropped["paths"][:2] == ["gen/f00.go", "gen/f01.go"]
    assert len(dropped["paths"]) == 20
    assert dropped["truncated"] is True


def test_scope_without_traversal_does_not_claim_nothing_dropped() -> None:
    from repowise.core.index_scope import dropped_files_scope

    assert dropped_files_scope(None) is None
    assert resolve_index_scope({})["dropped_files"] is None


def test_scope_drops_legacy_init_tips_and_size_skips() -> None:
    scope = resolve_index_scope(
        {
            "index_scope": {"analysis": {"unavailable": [], "skipped": []}},
            "degraded": [
                "Graph build can take several minutes on a first run. Safe to Ctrl-C — "
                "re-run 'repowise init --resume' to continue where it stopped.",
                "  Not indexed: src/TestData.g.cs (9,745 KB, over the 2,048 KB limit)",
                "  ...and more source files skipped on size",
                "Git indexing skipped: boom",
            ],
        }
    )
    assert scope["analysis"]["unavailable"] == ["Git indexing skipped: boom"]


def test_scope_with_only_legacy_tips_reports_complete() -> None:
    from repowise.core.index_scope import compact_index_scope

    state = {
        "run_mode": "standard",
        "git_tier": "full",
        "docs_mode": "llm",
        "index_scope": {
            "git_commit_cap": 500,
            "file_pages": {"eligible": 10, "generated": 10, "omitted": 0},
            "provider": {"name": "openai", "model": "a-model", "embedder": "openai"},
            "search": {"full_text": "available", "semantic": "available"},
        },
        "git_history_coverage": {"eligible_files": 10, "retained_commits": 50},
        "full_upgrade": {"status": "complete", "completed_stages": ["generation"]},
        "degraded": [
            "Graph build can take several minutes on a first run. Safe to Ctrl-C — "
            "re-run 'repowise init --resume' to continue where it stopped.",
            "  Not indexed: big.cs (9,745 KB, over the 2,048 KB limit)",
            "  ...and more source files skipped on size",
        ],
    }
    scope = resolve_index_scope(state)
    compact = compact_index_scope(scope)
    assert compact is not None
    assert compact["status"] == "complete"
    assert "degraded_analyses" not in compact

