"""The missing-orientation-page notice on the ``repowise update`` path.

Three things it must get right, all of them measured on ``test-repos/microdot``
before they were written down: it names ``--full`` and never a re-index, it
says nothing when the last whole-repo run already offered every registered
slot (microdot's own state: three of five slots are gate-skipped there on every
run, fresh or full), and it is driven by ``iter_specs()`` rather than a list
repeated here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd import slot_notice
from repowise.cli.helpers import (
    ONBOARDING_SLOTS_OFFERED_KEY,
    load_state,
    save_state,
    stamp_offered_slots,
)
from repowise.core.generation.onboarding import iter_specs


class _FakeTerminal:
    """A console that reports as a terminal and records what was printed."""

    is_terminal = True

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **_kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))


@pytest.fixture
def console(monkeypatch) -> _FakeTerminal:
    fake = _FakeTerminal()
    monkeypatch.setattr(slot_notice, "console", fake)
    return fake


def _registered() -> list[str]:
    return [spec.slot for spec in iter_specs()]


# --- what counts as missing ------------------------------------------------


def test_no_slot_is_missing_when_the_last_full_run_offered_them_all(tmp_path: Path) -> None:
    state: dict = {}
    stamp_offered_slots(state)
    save_state(tmp_path, state)

    assert slot_notice.missing_slots(tmp_path) == []


def test_a_slot_registered_since_the_last_full_run_is_missing(tmp_path: Path) -> None:
    registered = _registered()
    assert len(registered) > 1, "this test needs at least two registered slots"
    newcomer = registered[-1]

    # The record a whole-repo run left before ``newcomer`` was registered.
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: sorted(registered[:-1])})

    assert slot_notice.missing_slots(tmp_path) == [newcomer]


def test_a_gate_skipped_slot_is_not_reported(tmp_path: Path) -> None:
    """The microdot case: offered, refused by its own gate, so no page.

    Nothing any command can do changes that, so reporting it would promise a
    page ``--full`` will not produce.
    """
    state: dict = {}
    stamp_offered_slots(state)  # every registered slot was evaluated...
    save_state(tmp_path, state)  # ...and this store holds no onboarding row.

    assert slot_notice.missing_slots(tmp_path) == []


def test_missing_slots_are_returned_in_reading_order(tmp_path: Path) -> None:
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: []})

    assert slot_notice.missing_slots(tmp_path) == _registered()


def test_nothing_is_missing_when_onboarding_is_disabled(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "enable_onboarding: false\n", encoding="utf-8"
    )
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: []})

    assert slot_notice.missing_slots(tmp_path) == []


def test_the_comparison_follows_the_registry(tmp_path: Path) -> None:
    """Driven by ``iter_specs()``, not by a second list.

    A slot registered without this module following is the exact state the
    notice exists to report, so the registry has to be what it reads.
    """
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: []})

    assert slot_notice.missing_slots(tmp_path) == [spec.slot for spec in iter_specs()]


def test_an_index_with_no_record_falls_back_to_its_rows(tmp_path: Path) -> None:
    """Every index built before the record existed takes this path.

    There is no evidence of what was offered, so the rows are all there is:
    the notice reports what has no page and one ``--full`` resolves it either
    way, by writing the record. An index-only store holds no onboarding page
    at all, so every registered slot is reported.
    """
    import asyncio
    import subprocess

    from repowise.core.pipeline.full_index import index_repo_full

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for args in (
        ("init",),
        ("config", "user.email", "t@t.com"),
        ("config", "user.name", "T"),
    ):
        subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(repo), capture_output=True, text=True)

    asyncio.run(index_repo_full(repo))  # real store, keyless, no onboarding pages

    state = load_state(repo)
    assert ONBOARDING_SLOTS_OFFERED_KEY not in state

    assert slot_notice.missing_slots(repo) == _registered()


# --- what the user sees ----------------------------------------------------


def test_notice_names_full_and_never_a_reindex(tmp_path: Path, console: _FakeTerminal) -> None:
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: []})

    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)

    printed = " ".join(console.lines)
    assert "repowise update --full" in printed
    assert "index" in printed  # it says the index is kept
    for forbidden in ("re-index", "reindex", "repowise init"):
        assert forbidden not in printed.lower().replace("--full", "")


def test_notice_names_the_missing_pages_by_title(tmp_path: Path, console: _FakeTerminal) -> None:
    from repowise.core.generation.onboarding import SLOT_GLOSSARY, SLOT_TITLES

    offered = [slot for slot in _registered() if slot != SLOT_GLOSSARY]
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: sorted(offered)})

    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)

    printed = " ".join(console.lines)
    assert SLOT_TITLES[SLOT_GLOSSARY] in printed


def test_notice_is_silent_when_nothing_is_missing(tmp_path: Path, console: _FakeTerminal) -> None:
    state: dict = {}
    stamp_offered_slots(state)
    save_state(tmp_path, state)

    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)

    assert console.lines == []


def test_notice_shows_once_per_missing_set(tmp_path: Path, console: _FakeTerminal) -> None:
    registered = _registered()
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: sorted(registered[:-1])})

    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)
    assert console.lines

    console.lines.clear()
    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)
    assert console.lines == []  # the ledger suppresses the second showing

    # A further slot registered later is news again: the ledger is keyed by the
    # missing set, not by "this store has been told something once".
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: sorted(registered[:-2])})
    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)
    assert console.lines


def test_notice_is_silent_when_not_a_terminal(tmp_path: Path, monkeypatch) -> None:
    class _NonTerminal(_FakeTerminal):
        is_terminal = False

    fake = _NonTerminal()
    monkeypatch.setattr(slot_notice, "console", fake)
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: []})

    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)

    assert fake.lines == []
    # And a suppressed showing must not burn the one-shot.
    monkeypatch.setattr(slot_notice, "console", _FakeTerminal())
    assert slot_notice.missing_slots(tmp_path) == _registered()


def test_dry_run_does_not_burn_the_one_shot(tmp_path: Path, console: _FakeTerminal) -> None:
    save_state(tmp_path, {ONBOARDING_SLOTS_OFFERED_KEY: []})

    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=True)
    assert console.lines

    console.lines.clear()
    slot_notice.surface_missing_slots(tmp_path, emitter=None, dry_run=False)
    assert console.lines


# --- the stamp -------------------------------------------------------------


def test_stamp_records_every_registered_slot(tmp_path: Path) -> None:
    state: dict = {}
    stamp_offered_slots(state)

    assert state[ONBOARDING_SLOTS_OFFERED_KEY] == sorted(_registered())


def test_stamp_records_nothing_when_onboarding_was_off(tmp_path: Path) -> None:
    """A run with the collection disabled offered nothing.

    Recording otherwise would silence the notice for a user who later turns it
    back on and whose index has never seen a single slot.
    """
    state: dict = {}
    stamp_offered_slots(state, enabled=False)

    assert state[ONBOARDING_SLOTS_OFFERED_KEY] == []
