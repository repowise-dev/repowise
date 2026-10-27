"""The harness seam: a second adapter needs no consumer to change.

The toy harness below is deliberately nothing like Claude Code — a pipe
delimited text format, its own file extension, its own gate tokens, and one
logical event split across two lines so it needs state that outlives a
single ``normalize`` call. If it can register, discover, gate and normalize
through the shared drive paths, the seam is real rather than relocated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from repowise.core.sessions import (
    INTENT_SHELL_CALLS,
    INTENT_TOOL_CALLS,
    INTENT_TURNS,
    ClaudeCodeAdapter,
    CursorStore,
    Event,
    HarnessAdapter,
    ToolUse,
    get_adapter,
    iter_new_events,
    register_adapter,
    registered_adapters,
)
from repowise.core.sessions.adapters import registry


class ToyAdapter(HarnessAdapter):
    """A second harness: ``kind|text`` lines, tool calls split across two."""

    name: ClassVar[str] = "toy"

    def __init__(self) -> None:
        self.files_opened = 0
        self.files_closed = 0
        #: Name of a tool announced on a previous line, awaiting its args.
        self._pending_tool: str | None = None

    def discover(self, repo_root: Path, *, projects_root: Path | None = None) -> list[Path]:
        if projects_root is None or not projects_root.is_dir():
            return []
        return sorted(projects_root.glob("*.toy"))

    def prefilter(self, intent: str):
        if intent == INTENT_TURNS:
            return lambda raw: raw.startswith(("say|", "call|", "args|"))
        if intent in (INTENT_SHELL_CALLS, INTENT_TOOL_CALLS):
            return lambda raw: raw.startswith(("call|", "args|"))
        return None

    def begin_file(self, path: Path | None = None) -> None:
        self.files_opened += 1
        self._pending_tool = None

    def end_file(self) -> None:
        self.files_closed += 1
        self._pending_tool = None

    def normalize(self, raw_line: str) -> Event | None:
        kind, _, rest = raw_line.strip().partition("|")
        if kind == "say":
            return Event(kind="user", text=rest)
        if kind == "call":
            # Half an event: hold it until the args line arrives.
            self._pending_tool = rest
            return None
        if kind == "args" and self._pending_tool is not None:
            event = Event(kind="assistant")
            event.tool_uses.append(ToolUse(id="t1", name=self._pending_tool, input={"raw": rest}))
            self._pending_tool = None
            return event
        return None


@pytest.fixture
def toy() -> ToyAdapter:
    """Register ToyAdapter for one test, then put the registry back."""
    before = dict(registry._ADAPTERS)
    register_adapter(ToyAdapter)
    try:
        yield ToyAdapter()
    finally:
        registry._ADAPTERS.clear()
        registry._ADAPTERS.update(before)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_default_adapter_is_claude_code_and_is_a_fresh_instance() -> None:
    first, second = get_adapter(), get_adapter()
    assert first.name == "claude_code"
    assert isinstance(first, ClaudeCodeAdapter)
    # Per call, not a shared singleton: adapters may hold per-file state.
    assert first is not second


def test_registration_adds_exactly_one_name(toy: ToyAdapter) -> None:
    assert registered_adapters() == ["claude_code", "codex", "toy"]
    assert get_adapter("toy").name == "toy"


def test_unknown_harness_is_a_lookup_error() -> None:
    with pytest.raises(LookupError):
        get_adapter("no_such_harness")


# ---------------------------------------------------------------------------
# The three drive paths, all reached by a second harness
# ---------------------------------------------------------------------------


def _write_toy(directory: Path, name: str = "s.toy") -> Path:
    path = directory / name
    path.write_text(
        "say|hello\nnoise|ignored\ncall|Bash\nargs|ls -la\n",
        encoding="utf-8",
        newline="",
    )
    return path


def test_discover_then_iter_events(tmp_path: Path, toy: ToyAdapter) -> None:
    _write_toy(tmp_path)
    found = get_adapter("toy").discover(tmp_path, projects_root=tmp_path)
    assert [p.name for p in found] == ["s.toy"]

    events = list(toy.iter_events(found[0], prefilter=toy.prefilter(INTENT_TURNS)))
    assert [e.kind for e in events] == ["user", "assistant"]
    assert events[0].text == "hello"
    # The tool call was assembled from two lines, so cross-line state survived.
    assert events[1].tool_uses[0].name == "Bash"
    assert events[1].tool_uses[0].input == {"raw": "ls -la"}


def test_cursored_path_drives_a_second_harness(tmp_path: Path, toy: ToyAdapter) -> None:
    path = _write_toy(tmp_path)
    store = CursorStore(tmp_path / "cursors.json")

    first = list(iter_new_events(toy, path, store, prefilter=toy.prefilter(INTENT_TOOL_CALLS)))
    assert [e.tool_uses[0].name for e in first] == ["Bash"]

    with path.open("a", encoding="utf-8", newline="") as fh:
        fh.write("call|PowerShell\nargs|Get-ChildItem\n")
    second = list(iter_new_events(toy, path, store, prefilter=toy.prefilter(INTENT_TOOL_CALLS)))
    assert [e.tool_uses[0].name for e in second] == ["PowerShell"]


def test_caller_held_handle_drives_a_second_harness(tmp_path: Path, toy: ToyAdapter) -> None:
    path = _write_toy(tmp_path)
    with path.open("rb") as fh:
        lines = (raw.decode("utf-8", errors="replace") for raw in fh)
        events = list(toy.events_from_lines(lines, prefilter=toy.prefilter(INTENT_TURNS)))
    assert [e.kind for e in events] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# Per-file lifecycle
# ---------------------------------------------------------------------------


def test_lifecycle_brackets_every_file(tmp_path: Path, toy: ToyAdapter) -> None:
    _write_toy(tmp_path, "a.toy")
    _write_toy(tmp_path, "b.toy")
    for path in toy.discover(tmp_path, projects_root=tmp_path):
        list(toy.iter_events(path))
    assert (toy.files_opened, toy.files_closed) == (2, 2)


def test_lifecycle_closes_when_a_consumer_stops_early(tmp_path: Path, toy: ToyAdapter) -> None:
    path = _write_toy(tmp_path)
    iterator = toy.iter_events(path, prefilter=toy.prefilter(INTENT_TURNS))
    assert next(iterator).text == "hello"
    iterator.close()
    assert (toy.files_opened, toy.files_closed) == (1, 1)


def test_state_does_not_leak_between_files(tmp_path: Path, toy: ToyAdapter) -> None:
    """A tool call left dangling at EOF must not pair with the next file."""
    dangling = tmp_path / "a.toy"
    dangling.write_text("call|Bash\n", encoding="utf-8", newline="")
    orphan_args = tmp_path / "b.toy"
    orphan_args.write_text("args|rm -rf /\n", encoding="utf-8", newline="")

    events = [e for p in (dangling, orphan_args) for e in toy.iter_events(p)]
    assert events == []


class TracingAdapter(ToyAdapter):
    """Records the order of every lifecycle and parse callback."""

    name: ClassVar[str] = "tracing"

    def __init__(self) -> None:
        super().__init__()
        self.trace: list[str] = []

    def begin_file(self, path: Path | None = None) -> None:
        super().begin_file(path)
        self.trace.append(f"begin:{path.name if path is not None else None}")

    def end_file(self) -> None:
        super().end_file()
        self.trace.append("end")

    def normalize(self, raw_line: str) -> Event | None:
        self.trace.append(f"normalize:{raw_line.strip()}")
        return super().normalize(raw_line)


def test_gate_decides_before_normalize_is_paid_for(tmp_path: Path) -> None:
    """The performance contract: a gated-out line is never parsed.

    Transcript lines run to hundreds of kilobytes, so the whole layer rests
    on deciding from the raw string. Normalize-then-discard would leave every
    other test in this file green while giving all of that back.
    """
    adapter = TracingAdapter()
    path = _write_toy(tmp_path)  # 4 lines, of which 1 is a "say|"
    list(adapter.iter_events(path, prefilter=lambda raw: raw.startswith("say|")))

    parsed = [step for step in adapter.trace if step.startswith("normalize:")]
    assert parsed == ["normalize:say|hello"]


def test_lifecycle_opens_before_the_first_parse_and_closes_after_the_last(
    tmp_path: Path,
) -> None:
    """begin_file must bracket normalize, not merely happen at some point."""
    adapter = TracingAdapter()
    path = _write_toy(tmp_path, "session.toy")
    list(adapter.iter_events(path, prefilter=adapter.prefilter(INTENT_TURNS)))

    assert adapter.trace[0] == "begin:session.toy"
    assert adapter.trace[-1] == "end"
    assert all(step.startswith("normalize:") for step in adapter.trace[1:-1])


def test_bare_lines_report_no_path(tmp_path: Path) -> None:
    """A caller that supplies lines rather than a file says so honestly."""
    adapter = TracingAdapter()
    list(adapter.events_from_lines(["say|hi\n"]))
    assert adapter.trace[0] == "begin:None"


# ---------------------------------------------------------------------------
# Intents: the harness's key names live on the adapter, not in a consumer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "line", "expected"),
    [
        # Shell calls: a command tool_use, or Claude Code's result record.
        (INTENT_SHELL_CALLS, '{"type":"assistant","x":"tool_use","command":"ls"}', True),
        (INTENT_SHELL_CALLS, '{"toolUseResult":"Error: Exit code 1"}', True),
        (INTENT_SHELL_CALLS, '{"x":"tool_use","name":"Read"}', False),
        (INTENT_SHELL_CALLS, '{"type":"user","message":"hi"}', False),
        # Any tool traffic, shell or not.
        (INTENT_TOOL_CALLS, '{"x":"tool_use","name":"Read"}', True),
        (INTENT_TOOL_CALLS, '{"x":"tool_result","id":"1"}', True),
        (INTENT_TOOL_CALLS, '{"type":"file-history-snapshot"}', False),
        # Dialog turns, in either JSON spelling.
        (INTENT_TURNS, '{"type":"user"}', True),
        (INTENT_TURNS, '{"type": "assistant"}', True),
        (INTENT_TURNS, '{"type":"file-history-snapshot"}', False),
        (INTENT_TURNS, '{"type":"queue-operation"}', False),
    ],
)
def test_claude_code_intent_gates(intent: str, line: str, expected: bool) -> None:
    gate = ClaudeCodeAdapter().prefilter(intent)
    assert gate is not None
    assert gate(line) is expected


def test_turn_gate_survives_pretty_printed_json() -> None:
    """The spelling hazard the old two-token-per-kind tuple could not see."""
    gate = ClaudeCodeAdapter().prefilter(INTENT_TURNS)
    assert gate(json.dumps({"type": "user"}, indent=2)) is True


def test_unknown_intent_gets_no_gate_rather_than_a_wrong_one() -> None:
    """None means "parse everything", which is slow and correct."""
    assert ClaudeCodeAdapter().prefilter("no_such_intent") is None
    assert HarnessAdapter.prefilter(ClaudeCodeAdapter(), INTENT_TURNS) is None
