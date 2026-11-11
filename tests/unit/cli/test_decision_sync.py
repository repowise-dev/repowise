"""``decision push`` / ``pull`` and the same pair inside ``update``.

What protects the user here: nothing moves unless the repo turned sync on and
this machine is signed in with a GitHub origin; a repo that never asked hears
nothing about it; a record goes up once and never comes back down as a second
copy; a pulled decision is mirrored under its hosted id and version, updated
when that version rises, retired only when the answer read the whole index,
and never taken when the hosted index found it (the local index finds those
itself); a network failure is one quiet line, never a failed update.

The HTTP layer is faked at ``PlatformClient``; the store and git are real.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.commands import decision_sync_cmd
from repowise.cli.commands.decision_sync_cmd import build_item, sync_on_update
from repowise.cli.main import cli
from repowise.cli.platform import credentials, store
from repowise.cli.platform import decision_sync as ds
from repowise.cli.platform.client import PlatformClient
from repowise.core.persistence.database import init_db
from repowise.core.persistence.decision_pull import PULL_ARTIFACT
from repowise.core.persistence.models import (
    DecisionAcceptance,
    DecisionEvidence,
    DecisionRecord,
    Repository,
)

_REPO_ID = "sync-repo"
_SHA = "a1b2c3d4e5f6"
_SESSION = "0f8e2a6c-1b2d-4e5f-9a8b-7c6d5e4f3a2b"

AGENT_ACCEPTED = "a" * 32
SESSION_ACCEPTED = "b" * 32
AGENT_CANDIDATE = "c" * 32
CLI_ACCEPTED = "d" * 32
NO_EVIDENCE = "e" * 32

HOSTED = "h" * 32
HOSTED_INDEX = "i" * 32
HOSTED_OF_OURS = "9" * 32


def _record(rid: str, title: str, source: str, *, commits: list[str]):
    return DecisionRecord(
        id=rid,
        repository_id=_REPO_ID,
        title=title,
        decision=f"{title} decided",
        rationale="because it is simpler",
        context="",
        status="proposed",
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


def _db(repo: Path) -> str:
    return f"sqlite+aiosqlite:///{(repo / '.repowise' / 'wiki.db').as_posix()}"


def _seed(repo: Path) -> None:
    async def _build() -> None:
        (repo / ".repowise").mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(_db(repo))
        await init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Repository(id=_REPO_ID, name=repo.name, local_path=str(repo)))
            session.add_all(
                [
                    _record(AGENT_ACCEPTED, "Use JWT", "agent", commits=[_SHA, "not a sha"]),
                    _record(SESSION_ACCEPTED, "Keep sqlite", "session", commits=[_SESSION]),
                    _record(AGENT_CANDIDATE, "Cache tokens", "agent", commits=[_SHA]),
                    _record(CLI_ACCEPTED, "Typed by a person", "cli", commits=[_SHA]),
                    _record(NO_EVIDENCE, "No evidence", "agent", commits=[]),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    _accept(AGENT_ACCEPTED),
                    _accept(SESSION_ACCEPTED),
                    _accept(CLI_ACCEPTED),
                    _accept(NO_EVIDENCE, ("accepted by web",)),
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


def _rows(repo: Path, model, **where) -> list:
    async def _read():
        engine = create_async_engine(_db(repo))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                q = select(model)
                for key, value in where.items():
                    q = q.where(getattr(model, key) == value)
                return list((await session.execute(q)).scalars())
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _set_config(repo: Path, block: str) -> None:
    (repo / ".repowise" / "config.yaml").write_text(block, encoding="utf-8")


def _sync_on(repo: Path) -> None:
    _set_config(repo, "decisions:\n  sync: true\n")


@pytest.fixture(autouse=True)
def isolated_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(credentials, "_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.delenv("REPOWISE_OFFLINE", raising=False)
    # Only this module's waits are refused: patching ``time.sleep`` itself
    # also breaks subprocess, which polls with it on POSIX while git runs.
    monkeypatch.setattr(
        ds, "time", SimpleNamespace(sleep=lambda s: pytest.fail("slept for real"), time=time.time)
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "widget"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "remote", "add", "origin", "git@github.com:acme/widget.git")
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


def _pulled(
    hosted_id: str = HOSTED,
    *,
    seq: int = 1,
    origin: str = "recorded",
    title: str = "Retry webhooks with backoff",
    reason: str = "A reviewer said so on PR 12",
    accepter_kind: str = "person",
    files: list[str] | None = None,
) -> dict:
    return {
        "id": hosted_id,
        "title": title,
        "decision": "Webhook delivery retries with exponential backoff",
        "context": "",
        "rationale": "Receivers rate limit bursts",
        "alternatives": [],
        "consequences": [],
        "affected_files": files if files is not None else ["src/hooks.py"],
        "affected_modules": [],
        "evidence_commits": [_SHA],
        "kind": "architectural",
        "source": "pr_review",
        "source_quote": "please always back off on retries",
        "source_url": None,
        "evidence_file": None,
        "evidence_line": None,
        "origin": origin,
        "currency": "active",
        "created_at": "2026-11-01T10:00:00+00:00",
        "updated_at": "2026-11-01T10:00:00+00:00",
        "acceptance": {
            "seq": seq,
            "action": "accepted",
            "currency": "active",
            "reason": reason,
            "scope": files if files is not None else ["src/hooks.py"],
            "evidence": [_SHA],
            "accepter": "octocat",
            "accepter_kind": accepter_kind,
            "accepter_session": "",
            "artifact": "",
            "auto_accepted": False,
            "accepted_at": "2026-11-01T10:00:00+00:00",
        },
    }


class _Hosted:
    """A fake repowise.dev: one ready snapshot, a pull answer, posts recorded."""

    def __init__(self, monkeypatch, *, decisions=None, index_read=True, pull_status=200,
                 latest_status=200, post=None):
        self.decisions = list(decisions or [])
        self.index_read = index_read
        self.pull_status = pull_status
        self.latest_status = latest_status
        self.posts: list[dict] = []
        self.post = post or (lambda payload: (201, {"id": HOSTED_OF_OURS, "created": True}))
        monkeypatch.setattr(PlatformClient, "get_json", self._get)
        monkeypatch.setattr(PlatformClient, "post_json", self._post)

    def _get(self, path, *, params=None, timeout=None):
        if path == "repos/acme/widget/latest":
            if self.latest_status != 200:
                return self.latest_status, {}
            return 200, {"short_id": "snap1", "status": "ready"}
        assert path == "snapshots/snap1/decisions/pull", path
        if self.pull_status != 200:
            return self.pull_status, {}
        return 200, {
            "repo_id": "r",
            "snapshot_id": "snap1",
            "index_read": self.index_read,
            "total": len(self.decisions),
            "decisions": self.decisions,
        }

    def _post(self, path, payload, *, timeout=None):
        assert path == "snapshots/snap1/decisions"
        self.posts.append(payload)
        return self.post(payload)


def _cli(repo: Path, *args: str):
    return CliRunner().invoke(cli, ["decision", *args, str(repo), "--format", "json"])


def _latest(repo: Path, decision_id: str) -> DecisionAcceptance | None:
    rows = _rows(repo, DecisionAcceptance, decision_id=decision_id)
    return max(rows, key=lambda r: r.seq) if rows else None


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestGate:
    def test_sync_off_refuses_push_without_touching_the_network(self, repo, monkeypatch):
        _no_network(monkeypatch)
        _sign_in()
        result = _cli(repo, "push")

        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["error"] == "sync_off"
        assert "decision config sync --on" in payload["remedy"]

    def test_sync_off_refuses_pull(self, repo, monkeypatch):
        _no_network(monkeypatch)
        _sign_in()

        assert json.loads(_cli(repo, "pull").output)["error"] == "sync_off"

    def test_sync_off_refuses_push_even_with_nothing_to_send(self, repo, monkeypatch):
        _no_network(monkeypatch)
        _sign_in()
        (repo / ".repowise" / "decision_sync.json").write_text(
            json.dumps({"sent": {AGENT_ACCEPTED: "", SESSION_ACCEPTED: ""}}), encoding="utf-8"
        )

        assert json.loads(_cli(repo, "push").output)["error"] == "sync_off"

    def test_signed_out_is_not_linked(self, repo, monkeypatch):
        _no_network(monkeypatch)
        _sync_on(repo)

        assert json.loads(_cli(repo, "push").output)["error"] == "signed_out"
        assert json.loads(_cli(repo, "pull").output)["error"] == "signed_out"

    def test_no_github_origin_is_not_linked(self, repo, monkeypatch):
        _no_network(monkeypatch)
        _sign_in()
        _sync_on(repo)
        _git(repo, "remote", "remove", "origin")

        assert json.loads(_cli(repo, "pull").output)["error"] == "not_github"

    def test_a_non_boolean_sync_reads_as_off(self, repo, monkeypatch):
        _no_network(monkeypatch)
        _sign_in()
        _set_config(repo, "decisions:\n  sync: 'yes'\n")

        assert json.loads(_cli(repo, "pull").output)["error"] == "sync_off"

    def test_config_sync_switches_it_and_a_preset_keeps_it(self, repo):
        assert _cli(repo, "config", "sync", "--on").exit_code == 0
        assert json.loads(_cli(repo, "config", "show").output)["policy"]["sync"] is True
        assert _cli(repo, "config", "preset", "balanced").exit_code == 0
        assert json.loads(_cli(repo, "config", "show").output)["policy"]["sync"] is True
        assert _cli(repo, "config", "sync", "--off").exit_code == 0
        assert json.loads(_cli(repo, "config", "show").output)["policy"]["sync"] is False


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


class TestPush:
    def test_dry_run_needs_no_switch_and_sends_nothing(self, repo, monkeypatch):
        _no_network(monkeypatch)
        result = _cli(repo, "push", "--dry-run")

        assert result.exit_code == 0, result.output
        rows = {r["id"]: r for r in json.loads(result.output)["results"]}
        # In force, agent or session, never a person's record, never a candidate.
        assert set(rows) == {AGENT_ACCEPTED, SESSION_ACCEPTED, NO_EVIDENCE}
        assert rows[AGENT_ACCEPTED]["payload"]["evidence_commits"] == [_SHA]
        assert rows[SESSION_ACCEPTED]["payload"]["source_quote"].startswith("let's keep")
        assert rows[NO_EVIDENCE]["result"] == "skipped"

    def test_a_record_goes_up_once(self, repo, monkeypatch):
        _sign_in()
        _sync_on(repo)
        hosted = _Hosted(monkeypatch)
        result = _cli(repo, "push")

        assert result.exit_code == 0, result.output
        assert sorted(p["title"] for p in hosted.posts) == ["Keep sqlite", "Use JWT"]
        sent = json.loads((repo / ".repowise" / "decision_sync.json").read_text())["sent"]
        assert set(sent) == {AGENT_ACCEPTED, SESSION_ACCEPTED}

        hosted.posts.clear()
        again = _cli(repo, "push")
        assert again.exit_code == 0, again.output
        assert hosted.posts == []

    def test_a_refusal_fails_the_run_and_is_not_recorded_as_sent(self, repo, monkeypatch):
        _sign_in()
        _sync_on(repo)
        _Hosted(
            monkeypatch,
            post=lambda p: (422, {"detail": [{"loc": ["body", "title"], "msg": "bad"}]})
            if p["title"] == "Use JWT"
            else (201, {"id": HOSTED_OF_OURS, "created": True}),
        )
        result = _cli(repo, "push")

        assert result.exit_code == 1
        sent = json.loads((repo / ".repowise" / "decision_sync.json").read_text())["sent"]
        assert set(sent) == {SESSION_ACCEPTED}

    def test_a_pulled_copy_never_goes_back_up(self, repo, monkeypatch):
        _sign_in()
        _sync_on(repo)
        pulled = _pulled()
        pulled["source"] = "agent"
        _Hosted(monkeypatch, decisions=[pulled])
        assert _cli(repo, "pull").exit_code == 0

        _no_network(monkeypatch)
        rows = {r["id"] for r in json.loads(_cli(repo, "push", "--dry-run").output)["results"]}
        assert HOSTED not in rows


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------


class TestPull:
    def _pull(self, repo, monkeypatch, **hosted):
        _sign_in()
        _sync_on(repo)
        _Hosted(monkeypatch, **hosted)
        result = _cli(repo, "pull")
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    def test_a_recorded_decision_lands_in_force_under_its_hosted_id(self, repo, monkeypatch):
        out = self._pull(repo, monkeypatch, decisions=[_pulled()])

        assert out["inserted"] == [HOSTED]
        (record,) = _rows(repo, DecisionRecord, id=HOSTED)
        assert record.title == "Retry webhooks with backoff"
        assert json.loads(record.affected_files_json) == ["src/hooks.py"]
        latest = _latest(repo, HOSTED)
        assert latest.artifact == PULL_ARTIFACT
        assert latest.accepter == "octocat"
        assert latest.accepter_kind == "import"
        assert latest.currency == "active"
        # Delivered like any decision: the governs-this-file read finds it.
        shown = json.loads(_cli(repo, "show", HOSTED).output)["decision"]
        assert shown["id"] == HOSTED

    def test_a_higher_version_updates_the_copy_and_the_same_one_does_not(
        self, repo, monkeypatch
    ):
        self._pull(repo, monkeypatch, decisions=[_pulled(seq=1)])
        same = self._pull(repo, monkeypatch, decisions=[_pulled(seq=1, reason="edited")])
        assert same["unchanged"] == [HOSTED]
        assert _latest(repo, HOSTED).reason == "A reviewer said so on PR 12"

        newer = self._pull(
            repo, monkeypatch, decisions=[_pulled(seq=2, reason="Narrowed in review")]
        )
        assert newer["updated"] == [HOSTED]
        assert _latest(repo, HOSTED).reason == "Narrowed in review"
        assert len(_rows(repo, DecisionRecord, id=HOSTED)) == 1

    def test_a_copy_gone_upstream_is_retired_when_the_index_was_read(self, repo, monkeypatch):
        self._pull(repo, monkeypatch, decisions=[_pulled()])
        out = self._pull(repo, monkeypatch, decisions=[], index_read=True)

        assert out["retired"] == [HOSTED]
        latest = _latest(repo, HOSTED)
        assert latest.action == "deprecated"
        assert latest.currency == "dismissed"

    def test_a_copy_gone_upstream_is_kept_when_the_index_was_not_read(
        self, repo, monkeypatch
    ):
        self._pull(repo, monkeypatch, decisions=[_pulled()])
        out = self._pull(repo, monkeypatch, decisions=[], index_read=False)

        assert out["retired"] == []
        assert _latest(repo, HOSTED).currency == "active"

    def test_a_retired_copy_that_returns_is_back_in_force(self, repo, monkeypatch):
        self._pull(repo, monkeypatch, decisions=[_pulled()])
        self._pull(repo, monkeypatch, decisions=[], index_read=True)
        out = self._pull(repo, monkeypatch, decisions=[_pulled()])

        assert out["updated"] == [HOSTED]
        assert _latest(repo, HOSTED).currency == "active"

    def test_an_indexed_decision_is_not_taken(self, repo, monkeypatch):
        out = self._pull(repo, monkeypatch, decisions=[_pulled(HOSTED_INDEX, origin="index")])

        assert out["inserted"] == []
        assert _rows(repo, DecisionRecord, id=HOSTED_INDEX) == []

    def test_the_hosted_copy_of_a_pushed_record_is_not_pulled_back(self, repo, monkeypatch):
        _sign_in()
        _sync_on(repo)
        _Hosted(monkeypatch)
        assert _cli(repo, "push").exit_code == 0

        ours = _pulled(HOSTED_OF_OURS, title="Use JWT")
        ours["source"] = "agent"
        out = self._pull(repo, monkeypatch, decisions=[ours])
        assert out["inserted"] == []
        assert _rows(repo, DecisionRecord, id=HOSTED_OF_OURS) == []

    def test_a_record_already_here_is_left_alone(self, repo, monkeypatch):
        out = self._pull(repo, monkeypatch, decisions=[_pulled(CLI_ACCEPTED)])

        assert out["inserted"] == [] and out["updated"] == []
        assert _latest(repo, CLI_ACCEPTED).artifact == ""

    def test_with_approval_only_a_persons_acceptance_is_taken(self, repo, monkeypatch):
        _set_config(repo, "decisions:\n  sync: true\n  require_approval: true\n")
        _sign_in()
        _Hosted(monkeypatch, decisions=[_pulled(accepter_kind="agent")])
        out = json.loads(_cli(repo, "pull").output)

        assert out["inserted"] == []
        assert "person" in out["skipped"][0]["reason"]

    def test_untrusted_fields_are_checked(self, repo, monkeypatch):
        bad_id = _pulled("x" * 40)
        unsafe = _pulled(files=["../etc/passwd", "/abs.py", "src/ok.py"])
        out = self._pull(repo, monkeypatch, decisions=[bad_id, unsafe])

        assert out["inserted"] == [HOSTED]
        assert any("id" in s["reason"] for s in out["skipped"])
        (record,) = _rows(repo, DecisionRecord, id=HOSTED)
        assert json.loads(record.affected_files_json) == ["src/ok.py"]

    def test_a_row_this_store_cannot_read_does_not_retire_its_copy(self, repo, monkeypatch):
        self._pull(repo, monkeypatch, decisions=[_pulled()])
        broken = _pulled(seq=2)
        broken["acceptance"]["seq"] = "two"
        out = self._pull(repo, monkeypatch, decisions=[broken], index_read=True)

        assert out["retired"] == []
        assert _latest(repo, HOSTED).currency == "active"

    def test_a_new_title_taken_here_skips_that_copy_and_keeps_the_rest(
        self, repo, monkeypatch
    ):
        second = "g" * 32
        self._pull(
            repo, monkeypatch, decisions=[_pulled(), _pulled(second, title="Second decision")]
        )
        renamed = _pulled(seq=2, title="Second decision")
        newer = _pulled(second, seq=2, title="Second decision", reason="Still holds")
        out = self._pull(repo, monkeypatch, decisions=[renamed, newer])

        assert out["updated"] == [second]
        assert [s["id"] for s in out["skipped"]] == [HOSTED]
        (record,) = _rows(repo, DecisionRecord, id=HOSTED)
        assert record.title == "Retry webhooks with backoff"

    def test_an_older_server_without_the_route_says_nothing_is_available(
        self, repo, monkeypatch
    ):
        _sign_in()
        _sync_on(repo)
        _Hosted(monkeypatch, pull_status=404)
        result = CliRunner().invoke(cli, ["decision", "pull", str(repo)])

        assert result.exit_code == 0, result.output
        assert "Nothing is available to pull" in result.output

    def test_a_server_error_is_refused_and_writes_nothing(self, repo, monkeypatch):
        _sign_in()
        _sync_on(repo)
        _Hosted(monkeypatch, pull_status=503, decisions=[_pulled()])
        result = _cli(repo, "pull")

        assert result.exit_code == 1
        assert json.loads(result.output)["error"] == "pull_failed"
        assert _rows(repo, DecisionRecord, id=HOSTED) == []


# ---------------------------------------------------------------------------
# Inside update
# ---------------------------------------------------------------------------


class TestDuringUpdate:
    def test_sync_off_is_silent_and_offline(self, repo, monkeypatch, capsys):
        _no_network(monkeypatch)
        _sign_in()
        sync_on_update(repo)

        assert capsys.readouterr().out == ""
        assert not (repo / ".repowise" / "decision_sync.json").exists()

    def test_not_linked_is_one_quiet_line(self, repo, monkeypatch, capsys):
        _no_network(monkeypatch)
        _sync_on(repo)
        sync_on_update(repo)

        out = capsys.readouterr().out
        assert out.count("\n") == 1
        assert "Not signed in" in out

    def test_a_network_failure_is_one_line_and_never_raises(self, repo, monkeypatch, capsys):
        _sign_in()
        _sync_on(repo)
        monkeypatch.setattr(PlatformClient, "get_json", lambda *a, **k: (0, {}))
        monkeypatch.setattr(
            PlatformClient, "post_json", lambda *a, **k: pytest.fail("posted anyway")
        )
        sync_on_update(repo)

        out = capsys.readouterr().out
        assert out.count("\n") == 1
        assert "Couldn't reach repowise.dev" in out

    def test_an_unexpected_error_is_one_line_and_never_raises(self, repo, monkeypatch, capsys):
        _sign_in()
        _sync_on(repo)

        def explode(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(decision_sync_cmd, "_sync_once", explode)
        sync_on_update(repo)

        assert "boom" in capsys.readouterr().out

    def test_on_and_linked_it_pushes_then_pulls(self, repo, monkeypatch, capsys):
        _sign_in()
        _sync_on(repo)
        hosted = _Hosted(monkeypatch, decisions=[_pulled()])

        def limited(payload):
            # The per-minute limit inside update is not waited out.
            return (429, {"detail": "Rate limit exceeded"})

        hosted.post = limited
        sync_on_update(repo)

        out = capsys.readouterr().out
        assert "received 1" in out
        assert len(hosted.posts) == 1
        assert _latest(repo, HOSTED).artifact == PULL_ARTIFACT
        # Nothing sent is recorded as sent, so the next update tries again.
        assert not (repo / ".repowise" / "decision_sync.json").exists()

    def test_a_pull_with_no_route_says_nothing_during_update(self, repo, monkeypatch, capsys):
        _sign_in()
        _sync_on(repo)
        _Hosted(monkeypatch, pull_status=404)
        sync_on_update(repo)

        out = capsys.readouterr().out
        assert "sent 2" in out
        assert "Nothing" not in out


# ---------------------------------------------------------------------------
# Transport units carried over from the push command
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def post_json(self, path, payload, *, timeout=None):
        self.calls += 1
        return self.answers.pop(0)


def _items(n: int) -> list[ds.PushResult]:
    return [
        ds.PushResult(
            id=str(i) * 32, title=f"t{i}", source="agent", accepted=True,
            result="would_push", payload={"title": f"t{i}"},
        )
        for i in range(n)
    ]


class TestRateLimits:
    def test_the_per_minute_limit_is_waited_out_on_request(self):
        slept: list[float] = []
        client = _Client([(429, {"detail": "Rate limit exceeded"}), (201, {"created": True})])
        run = ds.push("s", _items(1), client=client, sleep=slept.append, now=lambda: 125.0)

        assert run.results[0].result == "created"
        assert slept == [pytest.approx(56.0)]

    def test_without_waiting_the_limit_stops_the_run(self):
        client = _Client([(429, {"detail": "Rate limit exceeded"})])
        run = ds.push("s", _items(2), client=client, wait=False,
                      sleep=lambda s: pytest.fail("waited"))

        assert [r.result for r in run.results] == ["refused", "not_sent"]
        assert client.calls == 1

    def test_the_open_proposal_cap_stops_the_run(self):
        cap = "This repository already has 1000 proposed decisions awaiting review."
        client = _Client([(201, {"created": True}), (429, {"detail": cap})])
        run = ds.push("s", _items(3), client=client, sleep=lambda s: pytest.fail("waited"))

        assert [r.result for r in run.results] == ["created", "refused", "not_sent"]


def test_evidence_refs_keep_only_what_the_endpoint_accepts():
    assert ds.evidence_refs("agent", ["ABC1234", "abc1234", "nope", "../x"]) == ["abc1234"]
    assert ds.evidence_refs("session", [_SESSION, "has space", "/etc"]) == [_SESSION]


def _rec(**over):
    fields = {
        "id": "7" * 32,
        "title": "Use JWT",
        "decision": "Use JWT decided",
        "context": "",
        "rationale": "",
        "source": "agent",
        "kind": "architectural",
        "identity_quote": "Use JWT decided",
        "evidence_commits_json": json.dumps([_SHA]),
        "alternatives_json": "[]",
        "consequences_json": "[]",
        "affected_files_json": "[]",
    }
    fields.update(over)
    return SimpleNamespace(**fields)


class TestHostedLimits:
    @pytest.mark.parametrize(("field", "size"), [("title", 201), ("decision", 4001)])
    def test_text_over_its_limit_is_skipped_not_cut(self, field, size):
        item = build_item(_rec(**{field: "x" * size}), [])

        assert item.result == "skipped"
        assert f"{field} is longer than" in item.reason

    def test_the_quote_is_clipped_to_a_verbatim_prefix(self):
        quote = "q" * 1999 + "tail"
        item = build_item(_rec(identity_quote=quote), [])

        assert item.payload["source_quote"] == quote[:2000]

    def test_paths_are_normalised_and_unsafe_ones_dropped(self):
        files = [r"src\app.py", "./lib/x.py", "/etc/passwd", r"C:\x\y.py", "../out.py", "a/../b"]
        item = build_item(_rec(affected_files_json=json.dumps(files)), [])

        assert item.payload["affected_files"] == ["src/app.py", "lib/x.py"]


class TestParsePull:
    def test_each_unreadable_row_is_refused_with_its_reason(self):
        no_version = _pulled("v" * 32)
        no_version["acceptance"]["seq"] = -1
        retired = _pulled("r" * 32)
        retired["acceptance"]["currency"] = "superseded"
        payload = {
            "decisions": [
                "not a row",
                _pulled("bad id!"),
                no_version,
                retired,
                _pulled("t" * 32, title="   "),
                _pulled(),
            ]
        }

        kept, refused = ds.parse_pull(payload)

        assert [k.id for k in kept] == [HOSTED]
        assert refused == [
            ("bad id!", "not a decision id this store can hold"),
            ("v" * 32, "no acceptance version"),
            ("r" * 32, "not in force"),
            ("t" * 32, "no title"),
        ]

    def test_a_kept_row_carries_its_version_and_currency(self):
        (kept,), _ = ds.parse_pull({"decisions": [_pulled(seq=3)]})

        assert (kept.id, kept.seq, kept.currency, kept.scope) == (
            HOSTED,
            3,
            "active",
            ["src/hooks.py"],
        )


@pytest.mark.parametrize(
    ("pushed", "got", "problem", "line"),
    [
        (0, {}, None, None),
        (
            0,
            {},
            "Couldn't reach repowise.dev.",
            "Decision sync incomplete: Couldn't reach repowise.dev.",
        ),
        (
            2,
            {"inserted": ["x"], "updated": [], "retired": ["y", "z"]},
            None,
            "Decisions synced with repowise.dev: sent 2, received 1, retired 2.",
        ),
        (
            0,
            {"updated": ["x"]},
            "Rate limited.",
            "Decisions synced with repowise.dev: updated 1. Rate limited.",
        ),
    ],
)
def test_the_update_line_names_only_what_moved(pushed, got, problem, line):
    assert decision_sync_cmd._sync_line(pushed, got, problem) == line


# ---------------------------------------------------------------------------
# decisions.mode
# ---------------------------------------------------------------------------


class TestMode:
    def test_off_refuses_both_directions_without_the_network(self, repo, monkeypatch):
        _no_network(monkeypatch)
        _sign_in()
        _set_config(repo, "decisions:\n  mode: 'off'\n  sync: true\n")

        assert json.loads(_cli(repo, "push").output)["error"] == "decisions_off"
        assert json.loads(_cli(repo, "pull").output)["error"] == "decisions_off"

    def test_off_is_silent_during_update(self, repo, monkeypatch, capsys):
        _no_network(monkeypatch)
        _sign_in()
        _set_config(repo, "decisions:\n  mode: 'off'\n  sync: true\n")
        sync_on_update(repo)

        assert capsys.readouterr().out == ""

    def test_read_only_refuses_push(self, repo, monkeypatch):
        _sign_in()
        _set_config(repo, "decisions:\n  mode: read_only\n  sync: true\n")
        hosted = _Hosted(monkeypatch)

        payload = json.loads(_cli(repo, "push").output)

        assert payload["error"] == "read_only"
        assert hosted.posts == []

    def test_read_only_pulls_and_sends_nothing_during_update(self, repo, monkeypatch, capsys):
        _sign_in()
        _set_config(repo, "decisions:\n  mode: read_only\n  sync: true\n")
        hosted = _Hosted(monkeypatch, decisions=[_pulled()])
        sync_on_update(repo)

        assert hosted.posts == []
        assert "received 1" in capsys.readouterr().out
        assert _rows(repo, DecisionRecord, id=HOSTED)
