"""Regression: every health metric writer must stamp analyzed_commit (#1864).

The full pipeline, the update re-score, ``repowise health`` persistence and
the fast-index upgrade pass all recompute health; each must record the commit
it analyzed against, or the table fills with NULL provenance and get_health
cannot report stale health data. This pins the ``repowise health`` writer.
"""

from __future__ import annotations

from repowise.cli.commands.health_cmd import persist as health_persist


def test_persist_health_stamps_analyzed_commit(tmp_path, monkeypatch) -> None:
    """``repowise health`` persistence must pass the live HEAD as
    analyzed_commit, not leave the metric rows NULL (issue #1864)."""
    import git as gitpython

    # A real repo with one commit; get_head_commit reads it off disk.
    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "a.py").write_text("x = 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("feat: add a")
    live_head = repo.head.commit.hexsha
    repo.close()

    recorded: dict = {}

    async def _fake_save_health_metrics(session, repository_id, metrics, **kwargs):
        recorded["commit"] = kwargs.get("analyzed_commit")

    class _FakeRepo:
        id = "repo-1"

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            return None

    async def _fake_get_repository_by_path(session, local_path):
        return _FakeRepo()

    class _FakeReport:
        def __init__(self) -> None:
            self.metrics: list = []
            self.findings: list = []
            self.kpis: dict = {}

    monkeypatch.setattr(
        "repowise.core.persistence.crud.save_health_metrics", _fake_save_health_metrics
    )
    monkeypatch.setattr(
        "repowise.core.persistence.crud.get_repository_by_path",
        _fake_get_repository_by_path,
    )
    async def _fake_save_health_findings(*a, **k):
        return None

    async def _fake_save_health_snapshot(*a, **k):
        return None

    monkeypatch.setattr(
        "repowise.core.persistence.crud.save_health_findings",
        _fake_save_health_findings,
    )
    monkeypatch.setattr(
        "repowise.core.persistence.crud.save_health_snapshot",
        _fake_save_health_snapshot,
    )
    monkeypatch.setattr(
        "repowise.cli.helpers.get_db_url_for_repo", lambda p: "sqlite:///:memory:"
    )
    async def _fake_reconcile(url):
        return None

    monkeypatch.setattr(
        "repowise.cli.helpers.reconcile_schema_best_effort", _fake_reconcile
    )
    monkeypatch.setattr("repowise.core.persistence.create_engine", lambda url: object())
    monkeypatch.setattr(
        "repowise.core.persistence.create_session_factory", lambda engine: object()
    )
    monkeypatch.setattr("repowise.core.persistence.get_session", lambda sf: _FakeSession())

    health_persist._persist_health(tmp_path, report=_FakeReport())

    assert recorded["commit"] == live_head
