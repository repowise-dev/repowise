"""An index written under the keyword fix rule converges on the typed rule at the next update."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
from repowise.core.ingestion.git_indexer import prior_defects as prior_defects_module
from repowise.core.ingestion.git_indexer._constants import is_fix_commit

_REFACTOR = "refactor(context): resolve get_context targets through an ordered resolver list"


def _repo(tmp_path):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    repo.config_writer().set_value("user", "name", "T").release()
    return repo


def _write(repo, tmp_path, name: str, body: str, message: str) -> str:
    (tmp_path / name).write_text(body, encoding="utf-8")
    repo.index.add([name])
    return repo.index.commit(message).hexsha


def _history(tmp_path) -> str:
    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "def f():\n    return 1\n", "feat: add a")
    _write(repo, tmp_path, "b.py", "y = 0\n", "feat: add b")
    return _write(repo, tmp_path, "a.py", "def f():\n    return 2\n", _REFACTOR)


@pytest.mark.asyncio
async def test_an_untouched_files_prior_defect_count_converges(tmp_path, monkeypatch) -> None:
    _history(tmp_path)
    with monkeypatch.context() as old_rule:
        old_rule.setattr(prior_defects_module, "is_typed_fix_commit", is_fix_commit)
        _summary, meta = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    assert {m["file_path"]: m["prior_defect_count"] for m in meta}["a.py"] == 1

    repo = _repo(tmp_path)
    _write(repo, tmp_path, "b.py", "y = 1\n", "feat: touch b")
    sink: dict[str, dict] = {}
    await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_changed_files(
        ["b.py"], all_files={"a.py", "b.py"}, idle_decay_sink=sink
    )

    assert sink["a.py"]["prior_defect_count"] == 0


@pytest.mark.asyncio
async def test_a_fix_event_the_rule_no_longer_names_is_retracted(tmp_path, monkeypatch) -> None:
    refactor_sha = _history(tmp_path)
    indexer = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    with monkeypatch.context() as old_rule:
        old_rule.setattr(prior_defects_module, "is_typed_fix_commit", is_fix_commit)
        seeded, _, _, _ = indexer.capture_new_fix_events()
    assert {r["fix_sha"] for r in seeded} == {refactor_sha}

    rows, _, _, retracted = indexer.capture_new_fix_events(known_shas={refactor_sha})

    assert rows == []
    assert retracted == {refactor_sha}
