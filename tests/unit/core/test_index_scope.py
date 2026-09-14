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
