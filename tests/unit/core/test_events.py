"""Component snapshots and events: diff rules, the stamp guard, compaction, the wire form."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repowise.core.events import diff as diff_mod
from repowise.core.events import snapshot as snapshot_mod
from repowise.core.events.diff import diff_snapshots
from repowise.core.events.model import KINDS, EngineEvent, event_id, to_cloudevent
from repowise.core.events.store import EventSettings, load_event_settings


def _facts(**over):
    base = {
        "definition": "d1",
        "criticality": None,
        "tags": [],
        "declared_owner": None,
        "declared_owner_key": None,
        "key_id": "k1",
        "file_count": 20,
        "bus_factor": {"basis": "measured", "value": 2, "owned_files": 20},
        "test_reach": {"basis": "inferred", "share": 0.6, "production_files": 15},
        "dead_code": {"basis": "inferred", "findings": 10, "lines": 100},
        "hotspots": ["a.py"],
        "ownership": {
            "owned_files": 20,
            "owner_count": 3,
            "main_owners": ["aaa", "bbb"],
            "active_main_owners": 2,
            "recent_commits": 5,
            "window_days": 90,
        },
        "decisions": {"governing": 1, "stale": []},
        "contracts": {"basis": "measured", "provided": {"http::GET::/a": 2}},
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def _kinds(before, after):
    return [(k, s) for k, _c, s, _e in diff_snapshots({"c": before}, {"c": after})]


# ---------------------------------------------------------------------------
# Diff rules
# ---------------------------------------------------------------------------


def test_identical_snapshots_produce_nothing():
    assert diff_snapshots({"c": _facts()}, {"c": _facts()}) == []


def test_a_redrawn_or_new_component_is_not_diffed():
    assert diff_snapshots({"c": _facts()}, {"c": _facts(definition="d2", hotspots=["x"])}) == []
    assert diff_snapshots({}, {"c": _facts(hotspots=["x"])}) == []


def test_declared_owner_change_is_a_shift():
    found = diff_snapshots(
        {"c": _facts(declared_owner="team-a", declared_owner_key="x1")},
        {"c": _facts(declared_owner="team-b", declared_owner_key="x2")},
    )
    assert [(k, s) for k, _, s, _ in found] == [("ownership_shift", "declared")]
    assert found[0][3] == {"basis": "declared", "previous_owner": "team-a", "owner": "team-b"}


def test_an_owner_that_is_not_a_group_is_never_named():
    # declared_owner is None for a user ref: only its keyed digest moved.
    found = diff_snapshots(
        {"c": _facts(declared_owner="team-a", declared_owner_key="x1")},
        {"c": _facts(declared_owner=None, declared_owner_key="x2")},
    )
    assert found[0][3] == {"basis": "declared", "previous_owner": "team-a"}


def test_owner_digests_under_another_key_are_not_compared():
    after = _facts(key_id="k2", ownership={"main_owners": ["ccc", "ddd"], "active_main_owners": 0})
    assert _kinds(_facts(), after) == []
    renamed = _facts(key_id="k2", declared_owner_key="x2")
    assert _kinds(_facts(declared_owner_key="x1"), renamed) == []


def test_a_capped_recent_window_does_not_drive_activity():
    gone = {"active_main_owners": 0, "recent_capped": True}
    assert _kinds(_facts(), _facts(ownership=gone)) == []


def test_observed_owner_turnover_needs_enough_owned_files():
    after = {"main_owners": ["ccc", "ddd"]}
    assert _kinds(_facts(), _facts(ownership=after)) == [("ownership_shift", "observed")]
    small = {"owned_files": diff_mod.MIN_OWNED_FILES - 1}
    assert _kinds(_facts(ownership=small), _facts(ownership={**after, **small})) == []
    # A set that grew around the same owners is not a shift.
    grew = {"main_owners": ["aaa", "bbb", "ccc"]}
    assert _kinds(_facts(), _facts(ownership=grew)) == []


def _owned_by(counts):
    paths, rows = [], {}
    for person, count in counts.items():
        for _ in range(count):
            path = f"src/file{len(paths)}.py"
            paths.append(path)
            rows[path] = {"primary_owner_name": person, "primary_owner_email": None}

    class Resolve:
        def __call__(self, name, email):
            return name

        def is_bot(self, person):
            return False

    return snapshot_mod._ownership(paths, rows, [], Resolve(), b"k" * 32, False)


def test_alphabetical_churn_at_ownership_margin_is_not_a_shift():
    singles = dict.fromkeys("bcdefghij", 1)
    before = _owned_by({"alice": 9, **singles})
    after = _owned_by({"alice": 9, **singles, "aaron": 1})
    assert before["main_owners"] != after["main_owners"]
    assert _kinds(_facts(ownership=before), _facts(ownership=after)) == []


def test_old_snapshot_does_not_report_a_new_tie_as_a_handover():
    singles = dict.fromkeys("bcdefghij", 1)
    before = _owned_by({"alice": 9, **singles})
    before.pop("margin_tied_owners")  # Persisted before the margin fact existed.
    after = _owned_by({"alice": 9, **singles, "aaron": 1})
    assert _kinds(_facts(ownership=before), _facts(ownership=after)) == []


def test_real_owner_handover_survives_a_tied_margin():
    singles = dict.fromkeys("bcdefghij", 1)
    before = _owned_by({"alice": 9, **singles})
    after = _owned_by({"zoe": 9, **singles})
    assert _kinds(_facts(ownership=before), _facts(ownership=after)) == [
        ("ownership_shift", "observed")
    ]


def test_non_tied_multi_file_owner_handover_still_fires():
    before = _owned_by({"alice": 9, "bob": 3, **dict.fromkeys("cdefghi", 1)})
    after = _owned_by({"alice": 9, "zoe": 3, **dict.fromkeys("cdefghi", 1)})
    assert _kinds(_facts(ownership=before), _facts(ownership=after)) == [
        ("ownership_shift", "observed")
    ]


def test_owner_inactive_only_while_others_still_change_it():
    gone = {"active_main_owners": 0}
    assert _kinds(_facts(), _facts(ownership=gone)) == [("owner_inactive", "")]
    quiet = {"active_main_owners": 0, "recent_commits": 0}
    assert _kinds(_facts(), _facts(ownership=quiet)) == []


def test_bus_factor_drop_only_on_a_critical_component_with_measured_figures():
    drop = {"value": 1}
    assert _kinds(_facts(), _facts(bus_factor=drop)) == []
    crit = {"criticality": "critical"}
    assert _kinds(_facts(**crit), _facts(**crit, bus_factor=drop)) == [("bus_factor_drop", "")]
    inferred = {"basis": "inferred", "value": 1}
    assert _kinds(_facts(**crit), _facts(**crit, bus_factor=inferred)) == []


def test_new_hotspots_name_the_files():
    found = diff_snapshots({"c": _facts()}, {"c": _facts(hotspots=["a.py", "b.py"])})
    assert found[0][0] == "new_hotspot" and found[0][3]["files"] == ["b.py"]
    assert _kinds(_facts(hotspots=["a.py", "b.py"]), _facts()) == []


def test_contracts_added_and_broken_only_with_consumers():
    after = _facts(contracts={"provided": {"http::GET::/b": 0}})
    assert _kinds(_facts(), after) == [
        ("contract_added", "http::GET::/b"),
        ("contract_broken", "http::GET::/a"),
    ]
    unread = _facts(contracts={"provided": {"http::GET::/a": 0}})
    assert _kinds(unread, _facts(contracts={"provided": {}})) == []
    unknown = _facts(contracts={"basis": "unavailable"})
    assert _kinds(unknown, after) == [] and _kinds(after, unknown) == []


def test_a_decision_turning_stale_is_one_event():
    assert _kinds(_facts(), _facts(decisions={"stale": ["d9"]})) == [("decision_stale", "d9")]
    assert _kinds(_facts(decisions={"stale": ["d9"]}), _facts(decisions={"stale": ["d9"]})) == []


def test_dead_code_growth_needs_both_floors():
    assert _kinds(_facts(), _facts(dead_code={"findings": 15})) == [("dead_code_growth", "")]
    assert _kinds(_facts(), _facts(dead_code={"findings": 14})) == []  # +4
    big = {"findings": 100}
    assert _kinds(_facts(dead_code=big), _facts(dead_code={"findings": 120})) == []  # +20%


def test_test_reach_drop_by_share_or_measured_coverage():
    assert _kinds(_facts(), _facts(test_reach={"share": 0.5})) == [("test_reach_drop", "")]
    assert _kinds(_facts(), _facts(test_reach={"share": 0.55})) == []
    cov = {"line_coverage": {"pct": 80.0}}
    fell = {"line_coverage": {"pct": 70.0}}
    found = diff_snapshots({"c": _facts(test_reach=cov)}, {"c": _facts(test_reach=fell)})
    assert found[0][3] == {"line_coverage": {"basis": "measured", "previous_pct": 80.0, "pct": 70.0}}


# ---------------------------------------------------------------------------
# Wire form and settings
# ---------------------------------------------------------------------------


def _event(**over) -> EngineEvent:
    fields = {
        "id": event_id("r", "a", "b", "new_hotspot", "core", "x.py"),
        "kind": "new_hotspot",
        "repo_id": "r1",
        "component": "core",
        "severity": "critical",
        "occurred_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
        "evidence": {"files": ["x.py"]},
        "stamp": "h40.p8",
        "commit_sha": "b" * 40,
        "base_commit_sha": "a" * 40,
        "source": "https://example.com/acme/app",
        "vcs": {"vcs.change.id": "pr-12"},
    }
    fields.update(over)
    return EngineEvent(**fields)


def test_cloudevents_envelope_shape():
    ce = to_cloudevent(_event())
    assert set(ce) == {
        "specversion",
        "id",
        "source",
        "type",
        "time",
        "datacontenttype",
        "subject",
        "data",
    }
    assert ce["specversion"] == "1.0"
    assert ce["type"] == "dev.repowise.hotspot.added.0.1.0"
    assert ce["time"] == "2026-10-01T12:00:00Z"
    assert ce["subject"] == "component:core"
    assert ce["source"] == "https://example.com/acme/app"
    data = ce["data"]
    assert data["vcs.change.id"] == "pr-12"
    assert data["vcs.ref.head.revision"] == "b" * 40
    assert data["vcs.ref.base.revision"] == "a" * 40
    assert data["repowise.stamp"] == "h40.p8" and data["evidence"] == {"files": ["x.py"]}


def test_envelope_without_component_or_url():
    ce = to_cloudevent(_event(component=None, source="", vcs={}, base_commit_sha=None))
    assert "subject" not in ce
    assert ce["source"] == "/repos/r1"
    assert "vcs.ref.base.revision" not in ce["data"]


def test_every_kind_has_a_versioned_type():
    for kind in KINDS.values():
        assert kind.type.startswith("dev.repowise.") and kind.type.endswith(".0.1.0")


def test_event_ids_are_stable():
    assert event_id("r", "a", "b", "k") == event_id("r", "a", "b", "k")
    assert event_id("r", "a", "b", "k") != event_id("r", "a", "c", "k")
    assert len(event_id("x")) == 32


def test_settings_defaults_and_overrides(tmp_path):
    assert load_event_settings(tmp_path) == EventSettings()
    assert "new_hotspot" not in EventSettings().kinds
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "events:\n  retention_days: 30\n  compact_after_days: -1\n"
        "  kinds: {new_hotspot: true, dead_code_growth: false, bogus: true}\n"
        "  sinks: [stdout]\n",
        encoding="utf-8",
    )
    s = load_event_settings(tmp_path)
    assert s.retention_days == 30 and s.compact_after_days == 30
    assert "new_hotspot" in s.kinds and "dead_code_growth" not in s.kinds
    assert "bogus" not in s.kinds and s.sinks == ("stdout",)
    assert [e.split(":")[0] for e in s.errors] == [
        "events.kinds.bogus",
        "events.compact_after_days",
    ]


def test_settings_name_every_wrong_value(tmp_path):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "events:\n  enabled: maybe\n  retention_days: '30'\n  kinds: [new_hotspot]\n"
        "  sinks: jsonl\n  colour: red\n",
        encoding="utf-8",
    )
    s = load_event_settings(tmp_path)
    assert s.enabled is True and s.retention_days == 365 and s.sinks == ()
    assert sorted(e.split(":")[0] for e in s.errors) == [
        "events.colour",
        "events.enabled",
        "events.kinds",
        "events.retention_days",
        "events.sinks",
    ]


def test_a_config_naming_a_kind_that_no_longer_exists_warns_and_keeps_defaults(tmp_path):
    assert "attribution_jump" not in KINDS
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "events:\n  kinds: {attribution_jump: true, new_hotspot: true}\n", encoding="utf-8"
    )
    s = load_event_settings(tmp_path)
    assert [e.split(":")[0] for e in s.errors] == ["events.kinds.attribution_jump"]
    assert s.kinds == EventSettings().kinds | {"new_hotspot"}


def test_doctor_reports_a_wrong_events_block(tmp_path):
    from repowise.cli.commands.doctor_cmd.repo_checks import _events_config_check

    assert _events_config_check(tmp_path) is None
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "events:\n  kinds: {nope: true}\n", encoding="utf-8"
    )
    check = _events_config_check(tmp_path)
    assert check is not None and not check.ok and "events.kinds.nope" in check.detail


def test_owner_key_is_kept_and_digests_are_not_plain_hashes(tmp_path):
    import hashlib

    key = snapshot_mod.owner_key(tmp_path)
    assert key is not None and snapshot_mod.owner_key(tmp_path) == key
    # Some repos commit .repowise/: the key gets its own ignore rule, kept
    # inside .repowise/ so nothing outside it changes.
    ignored = (tmp_path / ".repowise" / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/events.key" in ignored
    assert not (tmp_path / ".gitignore").exists()
    digest = snapshot_mod._keyed(key, "ann@example.com")
    assert digest != hashlib.sha256(b"ann@example.com").hexdigest()[: len(digest)]


def test_owner_key_leaves_a_gitignore_that_already_covers_it(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".repowise/\n", encoding="utf-8")
    assert snapshot_mod.owner_key(tmp_path) is not None
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == ".repowise/\n"
    assert not (tmp_path / ".repowise" / ".gitignore").exists()


def test_owner_key_is_still_gitignored_when_only_info_exclude_covers_it(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git" / "info" / "exclude").write_text(".repowise/\n", encoding="utf-8")
    assert snapshot_mod.owner_key(tmp_path) is not None
    ignored = (tmp_path / ".repowise" / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/events.key" in ignored


def test_owner_key_leaves_the_working_tree_clean_outside_repowise(tmp_path):
    """A committed .repowise/ still keeps the key out, and the root is untouched."""
    import subprocess

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "init")

    assert snapshot_mod.owner_key(tmp_path) is not None
    assert git("status", "--porcelain", "--untracked-files=all") == "?? .repowise/.gitignore\n"


def test_main_owners_are_digested_under_the_repo_key(tmp_path):
    key = snapshot_mod.owner_key(tmp_path)

    def resolve(name, email):
        return email

    resolve.is_bot = lambda person: False
    rows = {
        "a.py": {"primary_owner_name": "Ann", "primary_owner_email": "ann@example.com"},
        "b.py": {"primary_owner_name": "Ann", "primary_owner_email": "ann@example.com"},
        "c.py": {"primary_owner_name": "Bob", "primary_owner_email": "bob@example.com"},
    }
    commits = [{"sha": "s1", "author_name": "Ann", "author_email": "ann@example.com"}]
    out = snapshot_mod._ownership(list(rows), rows, commits, resolve, key, False)
    assert out["main_owners"] == [snapshot_mod._keyed(key, "ann@example.com")]
    assert (out["owned_files"], out["owner_count"], out["active_main_owners"]) == (3, 2, 1)


def test_credentials_never_leave_in_the_source_url():
    from repowise.core.events.store import _public_url

    assert _public_url("https://user:tok@github.com/a/b.git") == "https://github.com/a/b.git"
    assert _public_url("https://github.com/a/b") == "https://github.com/a/b"
    assert _public_url(None) == ""


# ---------------------------------------------------------------------------
# Store: stamp guard, sinks, compaction
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo_db(tmp_path: Path):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import get_session

    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    (root / ".repowise" / "config.yaml").write_text(
        "events:\n  kinds: {new_hotspot: true}\n", encoding="utf-8"
    )
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    await init_db(engine)
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        repo = await upsert_repository(session, name="repo", local_path=str(root))
        repo_id = repo.id
    yield factory, repo_id, root
    await engine.dispose()


async def _run(factory, repo_id, *, head, facts, monkeypatch, stamp="s1", now=None):
    from repowise.core.events.store import record_snapshot
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import Repository

    async def _collect(*_a, **_k):
        return facts

    monkeypatch.setattr(snapshot_mod, "collect_facts", _collect)
    monkeypatch.setattr(snapshot_mod, "current_stamp", lambda: stamp)
    async with get_session(factory) as session:
        (await session.get(Repository, repo_id)).head_commit = head
        return await record_snapshot(session, repo_id, now=now)


@pytest.mark.asyncio
async def test_same_stamp_runs_diff_and_reach_the_jsonl_sink(repo_db, monkeypatch):
    from repowise.core.events.store import list_events
    from repowise.core.persistence.database import get_session

    factory, repo_id, root = repo_db
    assert await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch) == []
    after = {"c": _facts(hotspots=["a.py", "b.py"])}
    events = await _run(factory, repo_id, head="b" * 40, facts=after, monkeypatch=monkeypatch)
    assert [e.kind for e in events] == ["new_hotspot"]
    assert events[0].base_commit_sha == "a" * 40 and events[0].severity == "info"

    lines = (root / ".repowise" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == [events[0].id]

    # The same commit and stamp again is an update with nothing to do.
    assert await _run(factory, repo_id, head="b" * 40, facts=after, monkeypatch=monkeypatch) == []
    async with get_session(factory) as session:
        stored = await list_events(session, repo_id)
        assert [e.id for e in stored] == [events[0].id]
        assert stored[0].evidence["files"] == ["b.py"]
        assert await list_events(session, repo_id, kinds=["owner_inactive"]) == []


@pytest.mark.asyncio
async def test_a_run_without_a_snapshot_breaks_the_chain(repo_db, monkeypatch):
    from repowise.core.events.store import record_gap
    from repowise.core.persistence.database import get_session

    factory, repo_id, root = repo_db
    cfg = root / ".repowise" / "config.yaml"
    on = "events:\n  kinds: {new_hotspot: true}\n"
    hot = {"c": _facts(hotspots=["a.py", "b.py"])}
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch)
    cfg.write_text("events:\n  enabled: false\n", encoding="utf-8")
    await _run(factory, repo_id, head="b" * 40, facts=hot, monkeypatch=monkeypatch)
    cfg.write_text(on, encoding="utf-8")
    assert await _run(factory, repo_id, head="c" * 40, facts=hot, monkeypatch=monkeypatch) == []

    # A failed snapshot leaves the same marker.
    async with get_session(factory) as session:
        await record_gap(session, repo_id, "failed")
    hotter = {"c": _facts(hotspots=["a.py", "b.py", "c.py"])}
    assert await _run(factory, repo_id, head="d" * 40, facts=hotter, monkeypatch=monkeypatch) == []
    more = {"c": _facts(hotspots=["a.py", "b.py", "c.py", "d.py"])}
    events = await _run(factory, repo_id, head="e" * 40, facts=more, monkeypatch=monkeypatch)
    assert [e.evidence["files"] for e in events] == [["d.py"]]


@pytest.mark.asyncio
async def test_a_commit_pair_seen_again_is_a_new_event(repo_db, monkeypatch):
    factory, repo_id, _ = repo_db
    quiet, hot = {"c": _facts()}, {"c": _facts(hotspots=["a.py", "b.py"])}
    ids = []
    for head, facts in (("a", quiet), ("b", hot), ("a", quiet), ("b", hot)):
        events = await _run(factory, repo_id, head=head * 40, facts=facts, monkeypatch=monkeypatch)
        ids += [e.id for e in events]
    assert len(ids) == 2 and len(set(ids)) == 2

    # Runs with no commit at all still diff, each as its own occurrence.
    hotter = {"c": _facts(hotspots=["a.py", "b.py", "c.py"])}
    await _run(factory, repo_id, head=None, facts=hot, monkeypatch=monkeypatch)
    first = await _run(factory, repo_id, head=None, facts=hotter, monkeypatch=monkeypatch)
    await _run(factory, repo_id, head=None, facts=hot, monkeypatch=monkeypatch)
    second = await _run(factory, repo_id, head=None, facts=hotter, monkeypatch=monkeypatch)
    assert len(first) == len(second) == 1 and first[0].id != second[0].id


@pytest.mark.asyncio
async def test_a_stamp_change_starts_a_new_baseline(repo_db, monkeypatch):
    factory, repo_id, _ = repo_db
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch)
    moved = {"c": _facts(hotspots=["a.py", "b.py"], dead_code={"findings": 50})}
    assert (
        await _run(factory, repo_id, head="b" * 40, facts=moved, monkeypatch=monkeypatch, stamp="s2")
        == []
    )
    # Against the new baseline the same facts are quiet.
    assert (
        await _run(factory, repo_id, head="c" * 40, facts=moved, monkeypatch=monkeypatch, stamp="s2")
        == []
    )


@pytest.mark.asyncio
async def test_disabled_kinds_and_disabled_events_store_nothing(repo_db, monkeypatch):
    factory, repo_id, root = repo_db
    (root / ".repowise" / "config.yaml").write_text("events:\n  enabled: false\n", encoding="utf-8")
    assert await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch) == []
    (root / ".repowise" / "config.yaml").write_text("", encoding="utf-8")
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch)
    after = {"c": _facts(hotspots=["a.py", "b.py"])}  # new_hotspot is off by default
    assert await _run(factory, repo_id, head="b" * 40, facts=after, monkeypatch=monkeypatch) == []


@pytest.mark.asyncio
async def test_compaction_keeps_one_run_per_week_and_drops_past_retention(repo_db, monkeypatch):
    from sqlalchemy import select

    from repowise.core.events.store import compact
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import (
        ComponentSnapshot,
        EngineEventRecord,
        SnapshotRun,
    )

    factory, repo_id, _ = repo_db
    now = datetime(2026, 10, 14, tzinfo=UTC)  # a Wednesday
    days = [0, 1, 40, 41, 42, 50, 400]
    async with get_session(factory) as session:
        for d in days:
            run = SnapshotRun(
                repository_id=repo_id, taken_at=now - timedelta(days=d), commit_sha=f"{d}", stamp="s"
            )
            session.add(run)
            await session.flush()
            session.add(ComponentSnapshot(run_id=run.id, component="c", facts_json="{}"))
        for d in (10, 400):
            session.add(
                EngineEventRecord(
                    id=f"e{d}",
                    repository_id=repo_id,
                    kind="new_hotspot",
                    severity="info",
                    occurred_at=now - timedelta(days=d),
                    stamp="s",
                )
            )
        await session.flush()
        await compact(
            session, repo_id, EventSettings(retention_days=365, compact_after_days=30), now=now
        )
    async with get_session(factory) as session:
        kept = {
            r.commit_sha: r.compacted for r in (await session.scalars(select(SnapshotRun))).all()
        }
        # 40/41/42 days back share an ISO week (Sep 2-4); 50 is the week before.
        assert kept == {"0": False, "1": False, "40": True, "50": True}
        assert len((await session.scalars(select(ComponentSnapshot))).all()) == 4
        assert [e.id for e in (await session.scalars(select(EngineEventRecord))).all()] == ["e10"]


@pytest.mark.asyncio
async def test_a_failing_sink_leaves_the_event_stored(repo_db, monkeypatch):
    from repowise.core.events.sinks import event_sinks
    from repowise.core.events.store import list_events
    from repowise.core.persistence.database import get_session

    class Broken:
        async def deliver(self, events, *, repo_root):
            raise OSError("down")

    factory, repo_id, root = repo_db
    event_sinks.register("broken-test", Broken())
    (root / ".repowise" / "config.yaml").write_text(
        "events:\n  kinds: {new_hotspot: true}\n  sinks: [broken-test]\n", encoding="utf-8"
    )
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch)
    after = {"c": _facts(hotspots=["a.py", "b.py"])}
    events = await _run(factory, repo_id, head="b" * 40, facts=after, monkeypatch=monkeypatch)
    async with get_session(factory) as session:
        assert [e.id for e in await list_events(session, repo_id)] == [events[0].id]


@pytest.mark.asyncio
async def test_a_stored_event_of_a_kind_no_longer_known_still_reads(repo_db):
    from repowise.core.events.store import list_events
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import EngineEventRecord

    factory, repo_id, _root = repo_db
    async with get_session(factory) as session:
        session.add(
            EngineEventRecord(
                id="x" * 32,
                repository_id=repo_id,
                kind="retired_kind",
                component="c",
                severity="info",
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                stamp="s1",
                payload_json=json.dumps({"evidence": {"basis": "measured"}}),
                schema_version=1,
            )
        )
    async with get_session(factory) as session:
        (event,) = await list_events(session, repo_id)
    assert to_cloudevent(event)["type"] == "dev.repowise.retired_kind.0.1.0"


# ---------------------------------------------------------------------------
# Contract facts measured after the workspace extracts contracts
# ---------------------------------------------------------------------------


async def _refresh(factory, repo_id, monkeypatch, measured, stamp="s1"):
    from repowise.core.events.store import refresh_contract_facts
    from repowise.core.persistence.database import get_session

    async def _contracts(*_a, **_k):
        return measured

    monkeypatch.setattr(snapshot_mod, "contract_facts", _contracts)
    monkeypatch.setattr(snapshot_mod, "current_stamp", lambda: stamp)
    async with get_session(factory) as session:
        return await refresh_contract_facts(session, repo_id)


async def _stored_contracts(factory, repo_id):
    from sqlalchemy import select

    from repowise.core.events.store import run_facts
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import SnapshotRun

    async with get_session(factory) as session:
        runs = await session.scalars(
            select(SnapshotRun.id).where(SnapshotRun.repository_id == repo_id)
            .order_by(SnapshotRun.taken_at, SnapshotRun.id)
        )
        return [(await run_facts(session, r))["c"]["contracts"] for r in runs]


@pytest.mark.asyncio
async def test_contracts_extracted_after_the_snapshot_fill_the_same_run(repo_db, monkeypatch):
    from repowise.core.events.store import list_events
    from repowise.core.persistence.database import get_session

    factory, repo_id, _root = repo_db
    unavailable = {"basis": "unavailable"}
    t0 = datetime(2026, 5, 1, tzinfo=UTC)
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch, now=t0)
    # The update's snapshot runs before extraction reaches the new commit.
    later = {"c": {**_facts(), "contracts": unavailable}}
    t1 = t0 + timedelta(hours=1)
    assert await _run(factory, repo_id, head="b" * 40, facts=later, monkeypatch=monkeypatch, now=t1) == []

    # Still unavailable after the pass (no workspace): nothing is written.
    assert await _refresh(factory, repo_id, monkeypatch, None) == []
    assert (await _stored_contracts(factory, repo_id))[1] == unavailable

    now = {"basis": "measured", "provided": {"http::GET::/b": 0}}
    events = await _refresh(factory, repo_id, monkeypatch, {"c": ("d1", now)})
    assert sorted((e.kind, e.evidence["contract_id"]) for e in events) == [
        ("contract_added", "http::GET::/b"),
        ("contract_broken", "http::GET::/a"),
    ]
    assert {e.commit_sha for e in events} == {"b" * 40}
    assert (await _stored_contracts(factory, repo_id))[1] == now
    # Idempotent: a second pass over the same run stores nothing new.
    assert await _refresh(factory, repo_id, monkeypatch, {"c": ("d1", now)}) == []
    async with get_session(factory) as session:
        assert len(await list_events(session, repo_id)) == 2


@pytest.mark.asyncio
async def test_contract_refresh_leaves_a_redrawn_component_or_an_old_run_alone(
    repo_db, monkeypatch
):
    factory, repo_id, _root = repo_db
    unavailable = {"basis": "unavailable"}
    await _run(factory, repo_id, head="a" * 40, facts={"c": {**_facts(), "contracts": unavailable}}, monkeypatch=monkeypatch)
    measured = {"basis": "measured", "provided": {}}
    assert await _refresh(factory, repo_id, monkeypatch, {"c": ("d2", measured)}) == []
    assert await _stored_contracts(factory, repo_id) == [unavailable]
    # Under another stamp the run is not this analyzer's to fill.
    assert await _refresh(factory, repo_id, monkeypatch, {"c": ("d1", measured)}, stamp="s2") == []
    assert await _stored_contracts(factory, repo_id) == [unavailable]


@pytest.mark.asyncio
async def test_contract_facts_are_unavailable_outside_a_workspace(tmp_path):
    assert await snapshot_mod.contract_facts(None, "r", tmp_path, head_commit="a" * 40) is None


@pytest.mark.asyncio
async def test_contract_kinds_skip_runs_whose_contracts_were_unavailable(repo_db, monkeypatch):
    factory, repo_id, _root = repo_db
    t0 = datetime(2026, 5, 1, tzinfo=UTC)
    gone = {"basis": "measured", "provided": {}}
    unavailable = {"basis": "unavailable"}

    async def run(head, contracts, hours, **over):
        facts = {"c": {**_facts(**over), "contracts": contracts}}
        now = t0 + timedelta(hours=hours)
        return await _run(factory, repo_id, head=head * 40, facts=facts, monkeypatch=monkeypatch, now=now)

    await run("a", _facts()["contracts"], 0)
    # An index-only run in between: never read as every contract removed.
    assert await run("b", unavailable, 1) == []
    events = await run("c", gone, 2)
    assert [(e.kind, e.base_commit_sha) for e in events] == [("contract_broken", "a" * 40)]
    # A component re-drawn in between ends the search.
    await run("d", unavailable, 3, definition="d2")
    assert await run("e", _facts()["contracts"], 4) == []


@pytest.mark.asyncio
async def test_contract_refresh_compares_with_the_last_measured_run(repo_db, monkeypatch):
    from repowise.core.events.store import list_events
    from repowise.core.persistence.database import get_session

    factory, repo_id, _root = repo_db
    t0 = datetime(2026, 5, 1, tzinfo=UTC)
    unavailable = {"c": {**_facts(), "contracts": {"basis": "unavailable"}}}
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch, now=t0)
    for i, head in enumerate("bc", start=1):
        await _run(factory, repo_id, head=head * 40, facts=unavailable, monkeypatch=monkeypatch,
                   now=t0 + timedelta(hours=i))
    now = {"basis": "measured", "provided": {"http::GET::/a": 2, "http::GET::/b": 0}}
    events = await _refresh(factory, repo_id, monkeypatch, {"c": ("d1", now)})
    assert [(e.kind, e.base_commit_sha, e.commit_sha) for e in events] == [
        ("contract_added", "a" * 40, "c" * 40)
    ]
    assert await _refresh(factory, repo_id, monkeypatch, {"c": ("d1", now)}) == []
    async with get_session(factory) as session:
        assert len(await list_events(session, repo_id)) == 1


@pytest.mark.asyncio
async def test_events_reach_the_sinks_only_after_the_commit(repo_db, monkeypatch):
    from repowise.core.events.store import list_events, record_snapshot
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import Repository

    factory, repo_id, root = repo_db
    sink = root / ".repowise" / "events.jsonl"
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch)
    hot = {"c": _facts(hotspots=["a.py", "b.py"])}

    async def _collect(*_a, **_k):
        return hot

    monkeypatch.setattr(snapshot_mod, "collect_facts", _collect)
    with pytest.raises(RuntimeError):
        async with get_session(factory) as session:
            (await session.get(Repository, repo_id)).head_commit = "b" * 40
            assert [e.kind for e in await record_snapshot(session, repo_id)] == ["new_hotspot"]
            assert not sink.exists()
            raise RuntimeError("the caller's commit fails")
    assert not sink.exists()
    async with get_session(factory) as session:
        assert await list_events(session, repo_id) == []
    # The retry stores and delivers the same event once.
    (event,) = await _run(factory, repo_id, head="b" * 40, facts=hot, monkeypatch=monkeypatch)
    assert [json.loads(line)["id"] for line in sink.read_text(encoding="utf-8").splitlines()] == [
        event.id
    ]


@pytest.mark.asyncio
async def test_contract_refresh_skips_a_run_superseded_during_extraction(repo_db, monkeypatch):
    from sqlalchemy import update

    from repowise.core.events.store import refresh_contract_facts
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import Repository

    factory, repo_id, _root = repo_db
    unavailable = {"basis": "unavailable"}
    await _run(factory, repo_id, head="a" * 40, facts={"c": _facts()}, monkeypatch=monkeypatch)
    await _run(factory, repo_id, head="b" * 40, facts={"c": {**_facts(), "contracts": unavailable}},
               monkeypatch=monkeypatch)

    async def _moved(session, *_a, **_k):
        await session.execute(update(Repository).values(head_commit="c" * 40))
        return {"c": ("d1", {"basis": "measured", "provided": {}})}

    monkeypatch.setattr(snapshot_mod, "contract_facts", _moved)
    async with get_session(factory) as session:
        assert await refresh_contract_facts(session, repo_id) == []
    assert (await _stored_contracts(factory, repo_id))[1] == unavailable


@pytest.mark.asyncio
async def test_a_config_error_is_logged_once_per_process(repo_db, monkeypatch, caplog):
    factory, repo_id, root = repo_db
    (root / ".repowise" / "config.yaml").write_text(
        "events:\n  kinds: {once_only_retired_kind: true}\n", encoding="utf-8"
    )
    for head in "ab":
        await _run(factory, repo_id, head=head * 40, facts={"c": _facts()}, monkeypatch=monkeypatch)
    named = [r for r in caplog.records if "once_only_retired_kind" in r.getMessage()]
    assert len(named) == 1
