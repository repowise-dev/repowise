"""The PostToolUse Read hook serving a skeleton instead of the whole file.

Two things are under test and they are not the same thing:

1. **The gates.** Every one of them must be able to say no on its own, and
   the default must be no. A hook that replaces what a tool returned is the
   most invasive thing in this codebase; the tests below are ordered as an
   argument that it fires only when it should.
2. **The contract.** A replacement that does not carry its elision ranges is
   a silent truncation, which is the one outcome that would make this worse
   than the nudge it replaced. That is asserted directly, not implied.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import _handle_post_tool_use
from repowise.cli.commands.augment_cmd.read_skeleton import (
    _MAX_OUTPUT_CHARS,
    _recorded_client_version,
    enabled,
    is_unbounded_read,
    supports_updated_output,
)

_SESSION = "sess-1"


def _source(bodies: int = 30, body_lines: int = 40) -> str:
    """A Python file big enough to clear every size floor."""
    out = ['"""Module docstring."""', "", "import os", "import sys", ""]
    for i in range(bodies):
        out.append(f"def func_{i}(argument_one, argument_two):")
        out.append(f'    """Summary line for func_{i}."""')
        out.extend(f"    value_{j} = argument_one + argument_two + {j}" for j in range(body_lines))
        out.append(f"    return value_{body_lines - 1}")
        out.append("")
    return "\n".join(out)


def _write_index(repo_path: Path, rel: str, source: str) -> None:
    """Persist wiki_symbols rows the way the indexer would."""
    db = repo_path / ".repowise" / "wiki.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS wiki_symbols ("
        "file_path TEXT, name TEXT, kind TEXT, signature TEXT, "
        "start_line INTEGER, end_line INTEGER)"
    )
    lines = source.splitlines()
    for idx, line in enumerate(lines, start=1):
        if not line.startswith("def "):
            continue
        end = idx
        while end < len(lines) and (lines[end].startswith("    ") or not lines[end].strip()):
            end += 1
        name = line[4 : line.index("(")]
        con.execute(
            "INSERT INTO wiki_symbols VALUES (?, ?, ?, ?, ?, ?)",
            (rel, name, "function", line.rstrip(":"), idx, end),
        )
    con.commit()
    con.close()


@pytest.fixture
def fake_home(_isolated_home: Path) -> Path:
    """The client-version probe reads ``~/.claude``, never the developer's.

    ``tests/unit/cli/conftest.py`` already redirects HOME *and* patches
    ``Path.home`` itself for every test in this package — so this aliases that
    rather than setting the env vars again, which the patch would ignore. It
    lives outside ``tmp_path``, which also keeps it clear of
    ``_find_repo_root``, whose walk refuses to treat ``$HOME`` as a repo.
    """
    return _isolated_home


@pytest.fixture
def repo(tmp_path: Path, fake_home: Path) -> Path:
    """An opted-in indexed repo with one large indexed file."""
    from repowise.cli.commands.augment_cmd._shared import _find_repo_root

    _find_repo_root.cache_clear()
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    (root / "pkg").mkdir()
    source = _source()
    (root / "pkg" / "big.py").write_text(source, encoding="utf-8")
    _write_index(root, "pkg/big.py", source)
    (root / ".repowise" / "config.yaml").write_text(
        "hooks:\n  read_skeleton: true\n", encoding="utf-8"
    )
    return root


def _read_output(repo_path: Path, rel: str) -> dict:
    """Read's real ``tool_response``, field for field.

    Faithful on purpose. The first version of this helper sent
    ``{"file": {"filePath", "numLines"}}`` — enough for the gates, missing
    ``content``, and missing the ``type`` envelope — and every test passed
    against a payload Claude Code never sends. The replacement is built *from*
    this object, so a fixture that is not the real shape cannot catch a
    response that is not the real shape, which is exactly what happened.
    """
    source = (repo_path / rel).read_text(encoding="utf-8")
    return {
        "type": "text",
        "file": {
            "filePath": str(repo_path / rel),
            "content": source,
            "numLines": len(source.splitlines()),
            "startLine": 1,
            "totalLines": len(source.splitlines()),
        },
    }


def _read(repo_path: Path, rel: str = "pkg/big.py", **tool_input):
    """Fire the PostToolUse Read hook as Claude Code would."""
    return _handle_post_tool_use(
        "Read",
        {"file_path": str(repo_path / rel), **tool_input},
        _read_output(repo_path, rel),
        str(repo_path),
        session_id=_SESSION,
    )


def _read_and_settle(repo_path: Path, rel: str = "pkg/big.py", **tool_input):
    """Fire the Read hook and run the bookkeeping the dispatcher defers.

    Both ledger writes hang off ``on_emitted``, which the real dispatcher runs
    only once the response is on its way to the agent, so a test that stops at
    the return value sees no rows at all.
    """
    result = _read(repo_path, rel, **tool_input)
    if result.on_emitted is not None:
        result.on_emitted()
    return result


def _served(result) -> str | None:
    """The skeleton text out of a Read replacement, or None if nothing served."""
    replacement = result.replacement
    if replacement is None:
        return None
    return replacement["file"]["content"]


def _edit(repo_path: Path, rel: str = "pkg/big.py"):
    return _handle_post_tool_use(
        "Edit",
        {"file_path": str(repo_path / rel)},
        {},
        str(repo_path),
        session_id=_SESSION,
    )


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_an_unbounded_read_of_a_large_indexed_file_is_served_as_a_skeleton(repo: Path) -> None:
    served = _served(_read(repo))

    assert served is not None
    assert "Serving the indexed skeleton of pkg/big.py" in served
    # Signatures survive; bodies do not.
    assert "def func_0(argument_one, argument_two):" in served
    assert "value_20 = argument_one" not in served


def test_the_replacement_is_shaped_like_a_read_result_not_a_string(repo: Path) -> None:
    """Claude Code type-checks ``updatedToolOutput`` against the replaced tool.

    Read's output is an object. Item 5 shipped emitting a bare string, which
    Claude Code rejected with "does not match Read's output shape" — falling
    back to the original file *silently*, exit 0 and no stderr, while the hook
    went on recording a served row and a saving. Every firing was a no-op for
    a release. This pins the shape so it cannot regress into a string again.
    """
    replacement = _read(repo).replacement

    assert isinstance(replacement, dict), "a string here is rejected by the client"
    assert replacement["type"] == "text"
    file_block = replacement["file"]
    # Exactly Read's own keys — carried through from the payload, not invented.
    assert set(file_block) == {"filePath", "content", "numLines", "startLine", "totalLines"}
    assert file_block["numLines"] == file_block["content"].count("\n")
    assert file_block["totalLines"] == 1324, "the file's real length, not the skeleton's"


def test_a_read_response_of_an_unexpected_shape_serves_nothing(repo: Path) -> None:
    """No payload, no replacement — and critically, no saving row either.

    The failure mode being ruled out is billing a saving for a replacement the
    client then refuses, which is what made the string bug invisible.

    The payload matters here. ``{"type": "text"}`` alone would be rejected by
    the size gate (no ``numLines`` means a line count of 0) and never reach
    the shape check at all — an assertion about the right thing on the wrong
    code path, which is the same vacuum that let the original bug ship. This
    one clears every gate above the shape check and fails only there. It is
    also, deliberately, the exact fixture the old tests used.
    """
    from repowise.cli.commands.augment_cmd.command import _handle_post_tool_use

    result = _handle_post_tool_use(
        "Read",
        {"file_path": str(repo / "pkg/big.py")},
        {"file": {"numLines": 400}},  # passes the size gate, has no `content`
        str(repo),
        session_id="shape-probe",
    )

    assert result.replacement is None
    assert result.on_emitted is None, "a rejected replacement must not bill a saving"


def test_every_elided_span_carries_its_line_range(repo: Path) -> None:
    """The whole justification for replacing a Read rather than nudging.

    Without ranges this is a silent truncation and the agent has no way back
    to what was removed. Same contract distill makes for shell output.
    """
    replacement = _served(_read(repo))
    assert replacement is not None

    markers = [ln.strip() for ln in replacement.splitlines() if ln.strip().startswith("...")]
    assert markers, "a skeleton with no elision markers elided nothing"
    for marker in markers:
        # "... N lines (a-b)" — 1-indexed, inclusive, both bounds present.
        assert "lines (" in marker and "-" in marker.split("lines (")[1]

    assert "Read it again with no range to get the whole file" in replacement


def test_kept_lines_carry_their_real_line_numbers(repo: Path) -> None:
    """Read returns ``cat -n``; dropping the gutter would leave the agent
    knowing line numbers only for the spans it cannot see."""
    replacement = _served(_read(repo))
    assert replacement is not None
    source = (repo / "pkg" / "big.py").read_text(encoding="utf-8").splitlines()

    numbered = 0
    for line in replacement.splitlines():
        if "\t" not in line or not line[:6].strip().isdigit():
            continue
        gutter, _, text = line.partition("\t")
        assert source[int(gutter) - 1] == text, f"line {gutter} does not match the file"
        numbered += 1
    assert numbered > 10, "almost nothing was numbered"


def test_numbering_is_dropped_rather_than_guessed_when_it_cannot_reconcile() -> None:
    """A wrong line number is worse than none."""
    from repowise.cli.commands.augment_cmd.read_skeleton import _number

    text = "def a():\n    ... 5 lines (2-6)\ndef b():\n"
    assert _number(text, total_lines=7).splitlines()[0].startswith("     1\t")
    # Same text, but the file is not that long — reconstruction is unsound.
    assert _number(text, total_lines=99) == text


def test_the_omission_store_path_matches_distills(repo: Path) -> None:
    """The path is spelled out to dodge a 250ms structlog import; keep it true.

    Lives in ``_shared`` now that both replacing surfaces bill savings through
    the same two writers: one copy of the literal, one guard over it.
    """
    from repowise.cli.commands.augment_cmd import _shared
    from repowise.core.distill.store import OMISSIONS_DB_FILENAME, OMISSIONS_DIRNAME

    source = Path(_shared.__file__).read_text(encoding="utf-8")
    assert f'".repowise" / "{OMISSIONS_DIRNAME}" / "{OMISSIONS_DB_FILENAME}"' in source


def test_the_replacement_is_smaller_than_what_it_replaced(repo: Path) -> None:
    replacement = _read(repo).replacement
    assert replacement is not None
    full = (repo / "pkg" / "big.py").read_text(encoding="utf-8")
    assert len(replacement) < len(full) / 2


def test_it_never_exceeds_the_output_cap(repo: Path) -> None:
    replacement = _read(repo).replacement
    assert replacement is not None
    assert len(replacement) <= _MAX_OUTPUT_CHARS


def test_a_skeleton_too_large_for_the_cap_is_skipped_not_truncated(repo: Path) -> None:
    """Cutting a skeleton would drop its trailing ranges — so we do not cut."""
    source = _source(bodies=400, body_lines=8)
    (repo / "pkg" / "big.py").write_text(source, encoding="utf-8")
    (repo / ".repowise" / "wiki.db").unlink()
    _write_index(repo, "pkg/big.py", source)

    assert _read(repo).replacement is None


# ---------------------------------------------------------------------------
# The gates — each one alone must be able to say no
# ---------------------------------------------------------------------------


def test_it_is_off_unless_the_repo_opted_in(repo: Path) -> None:
    (repo / ".repowise" / "config.yaml").write_text("hooks:\n  read_skeleton: false\n", "utf-8")
    assert _read(repo).replacement is None

    (repo / ".repowise" / "config.yaml").unlink()
    assert _read(repo).replacement is None
    assert enabled(repo) is False


def test_a_ranged_read_is_left_alone(repo: Path) -> None:
    """A bounded Read is already a targeted question; answer it as asked."""
    assert _read(repo, offset=10, limit=40).replacement is None
    assert _read(repo, limit=40).replacement is None


def test_a_verification_read_after_an_edit_is_left_alone(repo: Path) -> None:
    """Re-reading what you just edited needs fidelity, not structure.

    Tested on the gate directly. Going through ``_handle_post_tool_use`` would
    prove nothing: the first read has to serve a skeleton to set up the edit,
    and that puts the file in ``skeletonized``, so the once-per-file gate
    would return None whether or not the edit gate existed at all.
    """
    from repowise.cli.commands.augment_cmd.read_state import _skeleton_replacement

    state = {"skeletonized": [], "forgone": []}
    args = (
        repo,
        "pkg/big.py",
        {"file_path": str(repo / "pkg/big.py")},
        _read_output(repo, "pkg/big.py"),
    )

    served, _ = _skeleton_replacement(*args, state, edited_since_read=False)
    assert served is not None
    state["skeletonized"].clear()
    served, _ = _skeleton_replacement(*args, state, edited_since_read=True)
    assert served is None


def test_an_edit_after_a_skeleton_is_warned_about_once(repo: Path) -> None:
    """The replacement satisfies read-before-edit with content never shown."""
    assert _read(repo).replacement is not None

    first = _edit(repo)
    assert "only seen pkg/big.py as a skeleton" in (first.context or "")
    # Once per file — a warning on every edit would be a drumbeat.
    assert "as a skeleton" not in (_edit(repo).context or "")


def test_the_edit_warning_stops_once_the_file_is_read_in_full(repo: Path) -> None:
    """It is a guard for a true statement, not a permanent label."""
    assert _read(repo).replacement is not None
    assert _read(repo).replacement is None  # the escape hatch: file returned whole
    assert "as a skeleton" not in (_edit(repo).context or "")


def test_a_config_that_is_not_utf8_costs_only_this_enrichment(repo: Path) -> None:
    """A gate that raises must not take the rest of the Read handler with it."""
    (repo / ".repowise" / "config.yaml").write_bytes(b"hooks:\n  read_skeleton: true  # caf\xe9\n")

    _read(repo)
    _edit(repo)
    result = _read(repo)
    assert result.replacement is None
    # The stale-read notice and the session state still work.
    assert "changed (Edit/Write) after your previous read" in (result.context or "")


def test_no_durable_state_means_no_replacement(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch is the state file; without it the promise has no ceiling."""
    from repowise.cli.commands.augment_cmd import read_state

    monkeypatch.setattr(read_state, "_save_session_state", lambda *a, **k: False)
    assert _read(repo).replacement is None


def _reindex(repo_path: Path, rel: str, source: str) -> None:
    (repo_path / rel).write_text(source, encoding="utf-8")
    (repo_path / ".repowise" / "wiki.db").unlink()
    _write_index(repo_path, rel, source)


def test_a_skeleton_that_is_not_enough_smaller_is_left_alone(repo: Path) -> None:
    """Long, indexed, and above every size floor — but nothing to elide.

    Bodies of two lines fall under the render rule that keeps short runs
    verbatim, so the "skeleton" is the file. Replacing a Read with itself is
    strictly worse than leaving it alone: same tokens, plus a header.
    """
    _reindex(repo, "pkg/big.py", _source(bodies=300, body_lines=1))
    assert _read(repo).replacement is None


def test_a_skeleton_that_saves_too_few_tokens_is_left_alone(repo: Path) -> None:
    """A real proportional win on a file too small for it to be worth anything."""
    _reindex(repo, "pkg/big.py", _source(bodies=6, body_lines=15))
    result = _read(repo)
    assert result.replacement is None
    # And it is the token floor doing it, not the line floor above.
    assert len((repo / "pkg" / "big.py").read_text("utf-8").splitlines()) > 100


def test_a_stale_index_pointing_past_the_end_of_the_file_is_left_alone(repo: Path) -> None:
    """Bounds from before the file shrank: build_skeleton degrades to raw."""
    (repo / "pkg" / "big.py").write_text("\n".join(f"line_{i} = {i}" for i in range(150)), "utf-8")
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("UPDATE wiki_symbols SET start_line = start_line + 100000")
    con.commit()
    con.close()
    assert _read(repo).replacement is None


def test_the_env_override_turns_it_on_without_a_config(repo: Path, monkeypatch) -> None:
    (repo / ".repowise" / "config.yaml").unlink()
    assert _read(repo).replacement is None

    # A different file on purpose. `pkg/big.py` was just measured by the
    # counterfactual, and a measured file is not replaced again this session —
    # see test_a_file_is_never_billed_to_both_ledgers.
    source = (repo / "pkg" / "big.py").read_text(encoding="utf-8")
    (repo / "pkg" / "second.py").write_text(source, encoding="utf-8")
    _write_index(repo, "pkg/second.py", source)

    monkeypatch.setenv("REPOWISE_HOOK_READ_SKELETON", "1")
    assert _read(repo, rel="pkg/second.py").replacement is not None


def test_the_second_read_of_a_file_returns_it_whole(repo: Path) -> None:
    """The escape hatch the header promises has to actually exist."""
    assert _read(repo).replacement is not None
    assert _read(repo).replacement is None


def test_a_small_file_is_left_alone(repo: Path) -> None:
    source = "def tiny():\n    return 1\n"
    (repo / "pkg" / "small.py").write_text(source, encoding="utf-8")
    _write_index(repo, "pkg/small.py", source)
    assert _read(repo, rel="pkg/small.py").replacement is None


def test_an_unindexed_file_is_left_alone(repo: Path) -> None:
    (repo / "pkg" / "other.py").write_text(_source(), encoding="utf-8")
    assert _read(repo, rel="pkg/other.py").replacement is None


def test_no_index_at_all_is_left_alone(repo: Path) -> None:
    (repo / ".repowise" / "wiki.db").unlink()
    assert _read(repo).replacement is None


def test_an_unsupported_client_gets_its_read_untouched(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older clients ignore updatedToolOutput — emitting it would lose the win.

    This used to fall back to the skeleton nudge. The nudge was retired for
    having no measurable effect, so the fallback is silence: there is nothing
    to say to a client that cannot be handed the skeleton.
    """
    monkeypatch.setenv("REPOWISE_HOOK_UPDATED_OUTPUT", "0")
    result = _read(repo)
    assert result.replacement is None
    assert result.context is None


# ---------------------------------------------------------------------------
# Client-version probe
# ---------------------------------------------------------------------------


def test_the_version_probe_fails_open_when_it_cannot_tell(fake_home: Path) -> None:
    """No record on disk means "assume modern": the cost of being wrong is a
    skipped enrichment, never a broken Read."""
    assert _recorded_client_version() is None
    assert supports_updated_output() is True


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"version_to": "2.1.220", "version_from": "2.1.219"}, True),
        ({"version_to": None, "version_from": "2.1.220"}, True),
        ({"version_to": None, "version_from": "2.1.100"}, False),
        ({"version_to": "2.0.9", "version_from": "2.0.8"}, False),
        ({"version_to": "not-a-version"}, True),  # unparseable → fail open
        ({}, True),
    ],
)
def test_the_version_probe_reads_the_updater_record(
    fake_home: Path, record: dict, expected: bool
) -> None:
    claude = fake_home / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / ".last-update-result.json").write_text(json.dumps(record), encoding="utf-8")
    assert supports_updated_output() is expected


def test_a_corrupt_updater_record_fails_open(fake_home: Path) -> None:
    claude = fake_home / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / ".last-update-result.json").write_text("{not json", encoding="utf-8")
    assert supports_updated_output() is True


def test_is_unbounded_read() -> None:
    assert is_unbounded_read({"file_path": "a.py"}) is True
    assert is_unbounded_read({"file_path": "a.py", "offset": 1}) is False
    assert is_unbounded_read({"file_path": "a.py", "limit": 1}) is False
    assert is_unbounded_read("not a dict") is False


# ---------------------------------------------------------------------------
# Measurement — Gate A's numerator and denominator
# ---------------------------------------------------------------------------


def _ledger_categories(repo_path: Path) -> list[str]:
    db = repo_path / ".repowise" / "sessions" / "sessions.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return [
            row[0]
            for row in con.execute(
                "SELECT category FROM injections WHERE surface = 'read' ORDER BY rowid"
            )
        ]
    finally:
        con.close()


def test_a_served_skeleton_and_its_recoveries_are_logged(repo: Path) -> None:
    _read(repo)  # served
    _read(repo, offset=10, limit=20)  # ranged recovery — the contract working
    _read(repo)  # full recovery — the replacement was wrong

    categories = _ledger_categories(repo)
    assert "skeleton_served" in categories
    assert "skeleton_ranged" in categories
    assert "skeleton_recovered_full" in categories


def _savings_rows(repo_path: Path) -> list[tuple]:
    db = repo_path / ".repowise" / "omissions" / "omissions.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT filter, source, raw_tokens, distilled_tokens FROM savings"
        ).fetchall()
    finally:
        con.close()


def test_the_saving_is_billed_to_the_ledger_repowise_saved_reads(repo: Path) -> None:
    from repowise.core.distill.store import OmissionStore

    store = OmissionStore.open_default(repo)
    store.close()

    result = _read(repo)
    # Accounting is deferred to `on_emitted`, which the hook runs only after
    # the response is on its way to the agent. Nothing bills before then.
    assert not _savings_rows(repo)
    assert result.on_emitted is not None
    result.on_emitted()

    rows = _savings_rows(repo)
    assert len(rows) == 1
    filter_name, source, raw, distilled = rows[0]
    assert (filter_name, source) == ("read_skeleton", "hook-read")
    assert 0 < distilled < raw


# ---------------------------------------------------------------------------
# The counterfactual: what the feature would have saved, while it is off
# ---------------------------------------------------------------------------


def _forgone_rows(repo_path: Path) -> list[tuple]:
    db = repo_path / ".repowise" / "omissions" / "omissions.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT path, raw_tokens, distilled_tokens FROM forgone_savings"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


@pytest.fixture
def repo_off(repo: Path) -> Path:
    """The same repo with the feature explicitly declined."""
    (repo / ".repowise" / "config.yaml").write_text(
        "hooks:\n  read_skeleton: false\n", encoding="utf-8"
    )
    from repowise.core.distill.store import OmissionStore

    OmissionStore.open_default(repo).close()
    return repo


def test_a_repo_with_it_off_still_measures_what_it_would_have_saved(repo_off: Path) -> None:
    """Otherwise the gate can never run: off means no data, and no data means
    the flag stays off by inaction rather than by evidence."""
    result = _read_and_settle(repo_off)

    assert result.replacement is None, "measuring must not replace anything"
    rows = _forgone_rows(repo_off)
    assert len(rows) == 1
    path, raw, distilled = rows[0]
    assert path == "pkg/big.py"
    assert 0 < distilled < raw


def test_a_forgone_saving_is_kept_out_of_the_savings_ledger(repo_off: Path) -> None:
    """`repowise saved` sums that table into a headline figure. Nothing here
    happened, so adding it would inflate a real number with a hypothetical."""
    _read_and_settle(repo_off)

    assert _forgone_rows(repo_off)
    assert not _savings_rows(repo_off)


def test_the_counterfactual_measures_each_file_once(repo_off: Path) -> None:
    for _ in range(4):
        _read_and_settle(repo_off)

    assert len(_forgone_rows(repo_off)) == 1


def test_the_counterfactual_stops_at_the_per_session_cap(repo_off: Path) -> None:
    """It runs on Reads that are *not* being replaced — the common case — so
    an uncapped measurement would cost more than the feature it measures."""
    from repowise.cli.commands.augment_cmd import read_state

    source = (repo_off / "pkg" / "big.py").read_text(encoding="utf-8")
    for i in range(read_state._MAX_FORGONE_PER_SESSION + 5):
        rel = f"pkg/copy_{i}.py"
        (repo_off / rel).write_text(source, encoding="utf-8")
        _write_index(repo_off, rel, source)
        _read_and_settle(repo_off, rel)

    assert len(_forgone_rows(repo_off)) == read_state._MAX_FORGONE_PER_SESSION


def test_a_file_is_never_billed_to_both_ledgers(repo_off: Path) -> None:
    """Flipping the flag on mid-session must not count the same file twice.

    Without the `forgone` entry in the once-per-file gate the same read is
    recorded as saved *and* advertised as not-saved, with identical token
    counts, in one session.
    """
    _read_and_settle(repo_off)  # measured while off
    (repo_off / ".repowise" / "config.yaml").write_text(
        "hooks:\n  read_skeleton: true\n", encoding="utf-8"
    )
    _read_and_settle(repo_off)

    assert len(_forgone_rows(repo_off)) == 1
    assert not _savings_rows(repo_off), "the same file was billed to both ledgers"


def test_nothing_is_measured_when_the_session_state_cannot_persist(
    repo_off: Path, monkeypatch
) -> None:
    """The cap and the once-per-file gate both live in that state file.

    If it does not persist they reset on every event, so the same file is
    counted over and over — inflating the one number this feature exists to
    state honestly, and removing the ceiling on what measuring costs.
    """
    from repowise.cli.commands.augment_cmd import read_state

    monkeypatch.setattr(read_state, "_save_session_state", lambda *a, **k: False)

    for _ in range(6):
        _read_and_settle(repo_off)

    assert not _forgone_rows(repo_off)


def test_no_omission_store_means_no_forgone_row_either(repo: Path) -> None:
    """Same never-create-the-store rule as the real saving."""
    (repo / ".repowise" / "config.yaml").write_text(
        "hooks:\n  read_skeleton: false\n", encoding="utf-8"
    )
    _read_and_settle(repo)

    assert not (repo / ".repowise" / "omissions").exists()


def test_no_omission_store_means_no_store_is_created(repo: Path) -> None:
    """A hook is not the place to opt a repo into distill bookkeeping."""
    _read(repo)
    assert not (repo / ".repowise" / "omissions").exists()


# ---------------------------------------------------------------------------
# The retired re-read notice
# ---------------------------------------------------------------------------


def test_the_reread_notice_no_longer_fires(repo: Path) -> None:
    """Item 7: it scored 100% "respected" by measuring the base rate."""
    (repo / ".repowise" / "config.yaml").write_text("hooks:\n  read_skeleton: false\n", "utf-8")
    for _ in range(3):
        result = _read(repo)
        assert "You already read" not in (result.context or "")
