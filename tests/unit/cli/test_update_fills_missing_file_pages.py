"""`repowise update` renders eligible files that never got a page.

The cascade budget defers a page by marking it stale, and a later run heals
it. A file past the budget with no page had nothing to mark, so it stayed
missing and the index scope reported ``partial`` on every response.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import command, persistence
from tests.unit.cli.test_update_file_page_scope import _Builder, _parsed, _stored


@pytest.fixture
def no_config(monkeypatch):
    monkeypatch.setattr(persistence, "load_config", lambda _repo: {})
    monkeypatch.setattr("repowise.core.repo_config.load_repo_config", lambda _repo: {})


def test_lists_eligible_files_without_a_page(monkeypatch, no_config) -> None:
    monkeypatch.setattr(persistence, "_load_page_paths_and_hotspots", _stored({"a.py"}, set()))
    parsed = [
        _parsed("a.py", symbols=("fa",)),
        _parsed("new.py", symbols=("fn",)),
        _parsed("t.py", is_test=True),
    ]

    assert persistence.file_paths_without_page("repo", parsed, _Builder(), {}) == ["new.py"]


def test_unknown_store_lists_nothing(monkeypatch, no_config) -> None:
    async def unreadable(_repo):
        return None

    monkeypatch.setattr(persistence, "_load_page_paths_and_hotspots", unreadable)
    assert persistence.file_paths_without_page("repo", [_parsed("a.py")], _Builder(), {}) == []


def test_plan_renders_unpaged_files_past_the_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.file_paths_without_page",
        lambda *_a: ["new.py"],
    )
    affected = SimpleNamespace(regenerate=["a.py"], decay_only=["new.py"], stale_due_to_budget=1)
    run = SimpleNamespace(
        repo_path="repo",
        parsed_files=[],
        graph_builder=None,
        git_meta_map={},
        state={},
        cfg={},
        head="h2",
        affected=affected,
        stale_db_paths=[],
        stale_deterministic_ids=set(),
    )

    command._add_stale_pages(run, [], command._unpaged_file_paths(run))

    assert affected.regenerate == ["a.py", "new.py"]
    assert affected.decay_only == []
    assert run.state[command.FILE_PAGE_FILL_KEY] == "h2"


def _scope_run(file_pages: dict, **state) -> SimpleNamespace:
    return SimpleNamespace(
        state={"index_scope": {"file_pages": file_pages}, **state}, cfg={}, head="h1"
    )


_OMITTED = {"configured_cap": None, "effective_cap": None, "eligible": 30, "generated": 25}


@pytest.fixture
def can_render(monkeypatch):
    monkeypatch.setattr(command, "can_regenerate_pages", lambda _run, _opts: True)


def test_up_to_date_index_with_omitted_pages_fills_once(can_render) -> None:
    assert command._file_pages_to_fill(_scope_run({**_OMITTED, "omitted": 5}), None)
    tried = _scope_run({**_OMITTED, "omitted": 5}, **{command.FILE_PAGE_FILL_KEY: "h1"})
    assert not command._file_pages_to_fill(tried, None)


def test_pages_a_cap_omits_are_left_alone(can_render) -> None:
    capped = {**_OMITTED, "effective_cap": 25, "omitted": 5}
    assert not command._file_pages_to_fill(_scope_run(capped), None)


def test_plan_leaves_capped_out_files_alone(monkeypatch) -> None:
    def must_not_run(*_a):
        raise AssertionError("cap reached: no lookup")

    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.file_paths_without_page", must_not_run
    )
    run = _scope_run({**_OMITTED, "effective_cap": 25, "omitted": 5})
    assert command._unpaged_file_paths(run) == []


def test_complete_scope_does_not_wake_the_pipeline(can_render) -> None:
    assert not command._file_pages_to_fill(_scope_run({**_OMITTED, "omitted": 0}), None)


def test_mode_that_writes_no_pages_does_not_fill(monkeypatch) -> None:
    monkeypatch.setattr(command, "can_regenerate_pages", lambda _run, _opts: False)
    assert not command._file_pages_to_fill(_scope_run({**_OMITTED, "omitted": 5}), None)
