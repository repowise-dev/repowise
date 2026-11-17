"""A fast re-init over an index with documentation keeps the pages and what state says about them."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from repowise.cli.commands.init_cmd import _prepare
from repowise.cli.commands.init_cmd.persistence import keep_prior_documentation
from repowise.core.index_scope import resolve_index_scope, stamp_index_scope


def _model_state() -> dict:
    state = {
        "last_sync_commit": "abc",
        "docs_mode": "llm",
        "docs_enabled": True,
        "total_pages": 12,
        "provider": "mock",
        "model": "mock-model",
        "run_mode": "standard",
        "git_tier": "full",
    }
    stamp_index_scope(
        state,
        run_mode="standard",
        content_provenance="model",
        git_tier="full",
        file_pages={"configured_cap": None, "eligible": 20, "generated": 10, "omitted": 10},
        provider={"name": "mock", "model": "mock-model", "model_cost_possible": True},
        search={"full_text": "available", "semantic": "unavailable"},
        analysis={"unavailable": [], "skipped": []},
    )
    return state


def _fast_stamp(prior: dict) -> dict:
    """What the fast run stamps before the documentation is restored."""
    state = json.loads(json.dumps(prior))
    state.update(docs_mode="none", docs_enabled=False, total_pages=0, run_mode="fast")
    state["git_tier"] = "essential"
    stamp_index_scope(
        state,
        run_mode="fast",
        content_provenance="none",
        git_tier="essential",
        file_pages={"generated": 0},
        provider={"name": None, "model": None},
        search={"full_text": "unavailable"},
        analysis={"unavailable": [], "skipped": ["generation"]},
    )
    return state


def test_fast_run_keeps_the_written_documentation() -> None:
    prior = _model_state()
    state = _fast_stamp(prior)

    keep_prior_documentation(state, prior, "fast")

    assert state["docs_mode"] == "llm"
    assert state["total_pages"] == 12
    assert (state["provider"], state["model"]) == ("mock", "mock-model")
    scope = resolve_index_scope(state)
    assert scope["content_provenance"] == "model"
    assert scope["file_pages"]["generated"] == 10
    assert scope["search"]["full_text"] == "available"
    assert scope["analysis"]["skipped"] == []
    # Fast re-indexed git at the essential tier, so that stays this run's.
    assert scope["git_tier"] == "essential"
    assert scope["run_mode"] == "fast"


def test_a_state_older_than_index_scope_keeps_its_provenance() -> None:
    prior = {"last_sync_commit": "abc", "docs_enabled": True, "total_pages": 3}
    state = _fast_stamp(prior)

    keep_prior_documentation(state, prior, "fast")

    assert state["docs_mode"] == "llm"
    assert resolve_index_scope(state)["content_provenance"] == "model"


def test_standard_run_and_first_fast_run_are_untouched() -> None:
    prior = _model_state()
    standard = _fast_stamp(prior)
    snapshot = json.loads(json.dumps(standard))
    keep_prior_documentation(standard, prior, "standard")
    assert standard == snapshot

    first = _fast_stamp({})
    snapshot = json.loads(json.dumps(first))
    keep_prior_documentation(first, {}, "fast")
    assert first == snapshot


def _run(tmp_path: Path, **opts) -> SimpleNamespace:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text(json.dumps(_model_state()))
    defaults = {"run_mode": "standard", "index_only": True, "force": False, "yes": False}
    return SimpleNamespace(repo_path=tmp_path, opts=SimpleNamespace(**{**defaults, **opts}))


def test_fast_reinit_does_not_warn_about_replacing_pages(tmp_path: Path, monkeypatch) -> None:
    printed: list[str] = []
    monkeypatch.setattr(_prepare.console, "print", lambda *a, **k: printed.append(str(a)))
    monkeypatch.setattr(_prepare.sys.stdin, "isatty", lambda: False)
    run = _run(tmp_path, run_mode="fast")

    assert _prepare._confirm_wiki_overwrite(run) is True
    assert not any("template" in line for line in printed)
    assert any("Keeping the documentation" in line for line in printed)


def test_template_reinit_still_refuses_without_confirmation(tmp_path: Path, monkeypatch) -> None:
    import click
    import pytest

    monkeypatch.setattr(_prepare.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(_prepare.sys.stdin, "isatty", lambda: False)
    run = _run(tmp_path)

    with pytest.raises(click.ClickException):
        _prepare._confirm_wiki_overwrite(run)
