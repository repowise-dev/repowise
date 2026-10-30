"""``repowise decision push``: local agent and session records to repowise.dev.

What protects the user here: only records leave (agent and session sources,
accepted unless candidates are asked for), each carries the verbatim quote it
rests on, a record that cannot cite evidence is skipped with the reason, a
dry run sends nothing, every answer reads back per record, the per-minute
limit is waited out, and a refusal that would refuse everything stops the run.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.main import cli
from repowise.cli.platform import credentials, store
from repowise.cli.platform import decision_push as dp
from repowise.cli.platform.client import PlatformClient
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DecisionAcceptance,
    DecisionEvidence,
    DecisionRecord,
    Repository,
)

_REPO_ID = "push-repo"
_SHA = "a1b2c3d4e5f6"
_SESSION = "0f8e2a6c-1b2d-4e5f-9a8b-7c6d5e4f3a2b"

AGENT_ACCEPTED = "a" * 32
SESSION_ACCEPTED = "b" * 32
AGENT_CANDIDATE = "c" * 32
CLI_ACCEPTED = "d" * 32
NO_EVIDENCE = "e" * 32
DISMISSED = "f" * 32
COMMIT_ON_ACCEPTANCE = "1" * 32


def _record(rid: str, title: str, source: str, *, commits: list[str], status: str = "proposed"):
    return DecisionRecord(
        id=rid,
        repository_id=_REPO_ID,
        title=title,
        decision=f"{title} decided",
        rationale="because it is simpler",
        context="",
        status=status,
        source=source,
        identity_quote=f"{title} decided",
        affected_files_json=json.dumps(["src/app.py"]),
        evidence_commits_json=json.dumps(commits),
    )


def _accept(rid: str, evidence: tuple[str, ...] = (_SHA,)) -> DecisionAcceptance:
    return DecisionAcceptance(
        repository_id=_REPO_ID,
        decision_id=rid,
        reason="reviewed",
        scope_json=json.dumps(["src/app.py"]),
        evidence_json=json.dumps(list(evidence)),
        accepter="claude_code",
        accepter_kind="agent",
    )


def _seed(repo: Path) -> None:
    async def _build() -> None:
        db_path = repo / ".repowise" / "wiki.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        await init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Repository(id=_REPO_ID, name=repo.name, local_path=str(repo)))
            session.add_all(
                [
                    # "not a sha" is dropped from the evidence, the sha kept.
                    _record(AGENT_ACCEPTED, "Use JWT", "agent", commits=[_SHA, "not a sha"]),
                    _record(SESSION_ACCEPTED, "Keep sqlite", "session", commits=[_SESSION]),
                    _record(AGENT_CANDIDATE, "Cache tokens", "agent", commits=[_SHA]),
                    _record(CLI_ACCEPTED, "Typed by a person", "cli", commits=[_SHA]),
                    _record(NO_EVIDENCE, "No evidence", "agent", commits=[]),
                    _record(DISMISSED, "Withdrawn", "agent", commits=[_SHA], status="dismissed"),
                    _record(COMMIT_ON_ACCEPTANCE, "Pin deps", "agent", commits=[]),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    _accept(AGENT_ACCEPTED),
                    _accept(SESSION_ACCEPTED),
                    _accept(CLI_ACCEPTED),
                    _accept(NO_EVIDENCE, ("accepted by web",)),
                    _accept(COMMIT_ON_ACCEPTANCE),
                    DecisionEvidence(
                        decision_id=SESSION_ACCEPTED,
                        source="session",
                        source_rank=8,
                        evidence_commit=_SESSION,
                        source_quote="let's keep sqlite, no server to run",
                    ),
                ]
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_build())


@pytest.fixture(autouse=True)
def isolated_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(credentials, "_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setattr(dp.time, "sleep", lambda s: pytest.fail("slept for real"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "widget"
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", "git@github.com:acme/widget.git"],
        check=True,
    )
    _seed(path)
    return path


def _sign_in() -> None:
    credentials.save(
        {
            "token_kind": "oauth",
            "client_id": "repowise-cli",
            "access_token": "at-1",
            "access_expires_at": int(time.time()) + 3600,
            "refresh_token": "rt-1",
        }
    )


def _no_network(monkeypatch) -> None:
    def boom(*_a, **_k):
        pytest.fail("the network was used")

    monkeypatch.setattr(PlatformClient, "get_json", boom)
    monkeypatch.setattr(PlatformClient, "post_json", boom)


def _push(repo: Path, *args: str):
    return CliRunner().invoke(cli, ["decision", "push", str(repo), "--format", "json", *args])


def _by_id(payload: dict) -> dict[str, dict]:
    return {r["id"]: r for r in payload["results"]}


class TestDryRun:
    def test_sends_nothing_and_shows_each_payload(self, repo, monkeypatch):
        _no_network(monkeypatch)
        result = _push(repo, "--dry-run")

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["repo"] == "acme/widget"
        rows = _by_id(payload)
        # Accepted agent and session records only; not a person's, not a
        # candidate without the flag, never a dismissed one.
        assert set(rows) == {AGENT_ACCEPTED, SESSION_ACCEPTED, NO_EVIDENCE, COMMIT_ON_ACCEPTANCE}

        agent = rows[AGENT_ACCEPTED]["payload"]
        assert agent["source"] == "agent"
        assert agent["evidence_commits"] == [_SHA]
        assert agent["source_quote"] == "Use JWT decided"
        assert agent["affected_files"] == ["src/app.py"]

        session = rows[SESSION_ACCEPTED]["payload"]
        assert session["evidence_commits"] == [_SESSION]
        assert session["source_quote"] == "let's keep sqlite, no server to run"

    def test_a_record_without_evidence_is_skipped_with_the_reason(self, repo, monkeypatch):
        _no_network(monkeypatch)
        row = _by_id(json.loads(_push(repo, "--dry-run").output))[NO_EVIDENCE]

        assert row["result"] == "skipped"
        assert "evidence" in row["reason"]
        assert row["payload"] is None

    def test_a_commit_cited_only_at_acceptance_counts_as_evidence(self, repo, monkeypatch):
        _no_network(monkeypatch)
        row = _by_id(json.loads(_push(repo, "--dry-run").output))[COMMIT_ON_ACCEPTANCE]

        assert row["result"] == "would_push"
        assert row["payload"]["evidence_commits"] == [_SHA]

    def test_candidates_go_only_when_asked(self, repo, monkeypatch):
        _no_network(monkeypatch)
        rows = _by_id(json.loads(_push(repo, "--dry-run", "--include-candidates").output))

        assert rows[AGENT_CANDIDATE]["accepted"] is False
        assert rows[AGENT_CANDIDATE]["result"] == "would_push"
        assert DISMISSED not in rows

    def test_table_output_names_what_would_go(self, repo, monkeypatch):
        _no_network(monkeypatch)
        result = CliRunner().invoke(cli, ["decision", "push", str(repo), "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Would push to acme/widget" in result.output
        assert "Use JWT" in result.output
        assert "Nothing was sent" in result.output


class TestPush:
    def _hosted(self, monkeypatch, answers: dict[str, tuple[int, dict]]) -> list:
        """Answer each proposal by its title; one not named is created."""
        sent: list = []

        def fake_get(self, path, *, params=None, timeout=None):
            assert path == "repos/acme/widget/latest"
            return 200, {"short_id": "snap1", "status": "ready"}

        def fake_post(self, path, payload, *, timeout=None):
            sent.append((path, payload))
            return answers.get(payload["title"], (201, {"id": "9" * 32, "created": True}))

        monkeypatch.setattr(PlatformClient, "get_json", fake_get)
        monkeypatch.setattr(PlatformClient, "post_json", fake_post)
        return sent

    def test_each_record_reads_back_as_created_duplicate_or_refused(self, repo, monkeypatch):
        _sign_in()
        sent = self._hosted(
            monkeypatch,
            {
                "Use JWT": (201, {"id": "1" * 32, "status": "proposed", "created": True}),
                "Keep sqlite": (
                    200,
                    {"id": "2" * 32, "status": "active", "currency": "active", "created": False},
                ),
            },
        )
        result = _push(repo)

        assert result.exit_code == 0, result.output
        assert {p for p, _ in sent} == {"snapshots/snap1/decisions"}
        rows = _by_id(json.loads(result.output))
        assert rows[AGENT_ACCEPTED]["result"] == "created"
        assert rows[AGENT_ACCEPTED]["hosted_id"] == "1" * 32
        assert rows[SESSION_ACCEPTED]["result"] == "duplicate"
        assert rows[SESSION_ACCEPTED]["hosted_status"] == "active"
        assert rows[NO_EVIDENCE]["result"] == "skipped"
        assert "payload" not in rows[AGENT_ACCEPTED]

    def test_a_validation_refusal_names_the_field_and_fails_the_run(self, repo, monkeypatch):
        _sign_in()
        self._hosted(
            monkeypatch,
            {"Use JWT": (422, {"detail": [{"loc": ["body", "source_quote"], "msg": "required"}]})},
        )
        result = _push(repo)

        assert result.exit_code == 1
        rows = _by_id(json.loads(result.output))
        assert rows[AGENT_ACCEPTED]["result"] == "refused"
        assert rows[AGENT_ACCEPTED]["reason"] == "source_quote: required"
        # One bad record does not stop the others.
        assert rows[SESSION_ACCEPTED]["result"] == "created"

    def test_signed_out_is_refused_without_a_browser(self, repo, monkeypatch):
        _no_network(monkeypatch)
        result = _push(repo)

        assert result.exit_code == 1
        assert json.loads(result.output)["error"] == "signed_out"

    def test_a_repo_not_on_repowise_dev_points_at_publish(self, repo, monkeypatch):
        _sign_in()
        monkeypatch.setattr(PlatformClient, "get_json", lambda *a, **k: (404, {}))
        monkeypatch.setattr(
            PlatformClient, "post_json", lambda *a, **k: pytest.fail("posted anyway")
        )
        result = _push(repo)

        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["error"] == "no_snapshot"
        assert "repowise publish" in payload["remedy"]


class _Client:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def post_json(self, path, payload, *, timeout=None):
        self.calls += 1
        return self.answers.pop(0)


def _items(n: int) -> list[dp.PushResult]:
    return [
        dp.PushResult(id=str(i) * 32, title=f"t{i}", source="agent", accepted=True,
                      result="would_push", payload={"title": f"t{i}"})
        for i in range(n)
    ]


class TestRateLimits:
    def test_the_per_minute_limit_is_waited_out(self):
        slept: list[float] = []
        client = _Client([(429, {"detail": "Rate limit exceeded"}), (201, {"created": True})])
        run = dp.push("s", _items(1), client=client, sleep=slept.append, now=lambda: 125.0)

        assert run.results[0].result == "created"
        assert slept == [pytest.approx(56.0)]
        assert client.calls == 2

    def test_the_open_proposal_cap_stops_the_run(self):
        cap = "This repository already has 1000 proposed decisions awaiting review."
        client = _Client([(201, {"created": True}), (429, {"detail": cap})])
        run = dp.push("s", _items(3), client=client, sleep=lambda s: pytest.fail("waited"))

        assert [r.result for r in run.results] == ["created", "refused", "not_sent"]
        assert run.stopped == cap
        assert run.results[2].reason == cap
        assert client.calls == 2

    def test_a_limit_that_never_lifts_is_refused_after_its_retries(self):
        client = _Client([(429, {"detail": "Rate limit exceeded"})] * 3)
        run = dp.push("s", _items(1), client=client, sleep=lambda s: None, now=lambda: 0.0)

        assert run.results[0].result == "refused"
        assert client.calls == 3


def test_evidence_refs_keep_only_what_the_endpoint_accepts():
    assert dp.evidence_refs("agent", ["ABC1234", "abc1234", "nope", "../x"]) == ["abc1234"]
    assert dp.evidence_refs("session", [_SESSION, "has space", "/etc"]) == [_SESSION]
