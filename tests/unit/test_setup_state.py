"""``.repowise/setup.json``, the sync-hook read, and the readers that use them.

The hook and the MCP handshake read the last full answer instead of the
index, so the line they add is on exactly while the pass is offered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core.git_hooks import sync_hook_installed
from repowise.core.setup_state import (
    MCP_SETUP_LINE,
    offered_steps,
    read_setup_state,
    record_setup,
    write_setup_state,
)


def _offered(root: Path, *, open_: int = 4, offer: str | None = "finish_setup", dismissed=False):
    record_setup(root, {"open": open_, "offer": offer, "dismissed": dismissed})


def test_the_last_answer_round_trips_and_merges(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    _offered(tmp_path)
    write_setup_state(tmp_path, status_tail_shown=True)
    state = read_setup_state(tmp_path)
    assert state["open"] == 4 and state["offer"] == "finish_setup"
    assert state["status_tail_shown"] is True and state["written_at"]
    assert offered_steps(tmp_path) == 4


def test_a_read_never_creates_the_folder(tmp_path: Path) -> None:
    _offered(tmp_path)
    assert not (tmp_path / ".repowise").exists()
    assert read_setup_state(tmp_path) == {} and offered_steps(tmp_path) == 0


@pytest.mark.parametrize(
    "answer", [{"offer": None}, {"dismissed": True}, {"open_": 0}]
)
def test_no_steps_are_offered_once_done_declined_or_not_yet_offered(
    tmp_path: Path, answer: dict
) -> None:
    (tmp_path / ".repowise").mkdir()
    _offered(tmp_path, **answer)
    assert offered_steps(tmp_path) == 0


def test_the_sync_hook_read(tmp_path: Path) -> None:
    from repowise.cli.hooks import install

    assert sync_hook_installed(tmp_path) is None  # not a git repository
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert sync_hook_installed(tmp_path) is False
    install(tmp_path)
    assert sync_hook_installed(tmp_path) is True


def test_the_session_start_block_carries_the_line_only_while_offered(tmp_path: Path) -> None:
    from repowise.cli.commands.augment_cmd import session_start

    (tmp_path / ".repowise").mkdir()
    assert session_start._setup_line(tmp_path) is None
    _offered(tmp_path, open_=1)
    line = session_start._setup_line(tmp_path)
    assert line is not None
    assert "1 step." in line and "`repowise next --prompt setup`" in line
    _offered(tmp_path, dismissed=True)
    assert session_start._setup_line(tmp_path) is None


def test_the_mcp_instructions_carry_the_line_only_while_offered(tmp_path: Path) -> None:
    from repowise.core.registry.instructions import server_instructions
    from repowise.server.mcp_server._server import _setup_extra

    (tmp_path / ".repowise").mkdir()
    assert _setup_extra(str(tmp_path)) == []
    assert _setup_extra(None) == []
    _offered(tmp_path)
    assert _setup_extra(str(tmp_path)) == [MCP_SETUP_LINE]
    text = server_instructions(None, extra=_setup_extra(str(tmp_path)))
    assert text.endswith(MCP_SETUP_LINE)
    # About 25 tokens: the per-repo cap the instructions tests hold is 1500 chars.
    assert len(MCP_SETUP_LINE) < 200 and len(text) < 1500
    _offered(tmp_path, offer=None)
    assert _setup_extra(str(tmp_path)) == []
