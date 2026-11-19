"""Python cProfile/pstats profiles: sniffing, frames and calls, ordering without a share, corrupt files."""

from __future__ import annotations

import json
import marshal
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.hotness import RunFacts
from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import (
    ProfileRun,
    Stack,
    detect_profile_format,
    profile_formats,
)
from repowise.core.evidence.profile_formats.pstats import MAX_BYTES, sniff
from repowise.core.frame_resolver import ProfileFrame
from repowise.core.workspace.repo_index import IndexedSymbol
from tests.unit.evidence.test_hotness import _cost, _derive

FIXTURE = Path(__file__).parents[2] / "fixtures" / "evidence" / "app.prof"
ROOT = "C:\\work\\repo"


def _sym(qualified: str, span: tuple[int, int], kind: str = "method", path: str = "pkg/app.py") -> IndexedSymbol:
    name = qualified.rsplit(".", 1)[-1]
    return IndexedSymbol(
        symbol_id=f"{path}::{qualified}",
        name=name,
        qualified_name=qualified,
        kind=kind,
        signature="",
        file_path=path,
        start_line=span[0],
        end_line=span[1],
        visibility="public",
    )


APP = _sym("pkg.app.App", (1, 40), kind="class")
RUN = _sym("pkg.app.App.run", (2, 6))
LOAD = _sym("pkg.app.App.load", (8, 10))
SYMBOLS = {"pkg/app.py": [APP, RUN, LOAD]}


def test_pstats_file_is_detected_without_input_format() -> None:
    data = FIXTURE.read_bytes()
    assert detect_profile_format(data) == "pstats"
    assert detect_profile_format(data[:4096]) == "pstats"
    assert sniff(data) is True
    assert sniff(data[:4096]) is True


def test_speedscope_pprof_collapsed_and_pytest_benchmark_detection_is_unchanged() -> None:
    # A marshal dict starts with { or 0xFB, so sniff must not claim JSON formats
    speedscope_data = (
        b'{"$schema": "https://speedscope.app/file-format-schema.json", "profiles": []}'
    )
    pytest_bench_data = b'{"machine_info": {}, "benchmarks": []}'
    v1_data = b'{"format": "repowise-evidence-v1", "kind": "profile"}'
    collapsed_data = b"main (/r/a.py:3);f (/r/a.py:9) 12\n"

    assert sniff(speedscope_data) is False
    assert sniff(pytest_bench_data) is False
    assert sniff(v1_data) is False
    assert sniff(collapsed_data) is False

    assert detect_profile_format(speedscope_data) == "speedscope"
    assert detect_profile_format(pytest_bench_data) == "pytest-benchmark"
    assert detect_profile_format(v1_data) is None
    assert detect_profile_format(collapsed_data) == "collapsed"


def test_empty_marshal_dict_and_non_pstats_marshal_data() -> None:
    assert sniff(marshal.dumps({}, 0)) is True
    assert sniff(marshal.dumps({}, 4)) is True

    # Marshal dict with string keys is not pstats
    assert sniff(marshal.dumps({"key": "val"})) is False
    # Marshal list or int is not pstats
    assert sniff(marshal.dumps([1, 2, 3])) is False
    assert sniff(marshal.dumps(42)) is False
    assert sniff(b"") is False


def test_frames_and_calls_join_to_indexed_symbols_with_unresolved_outside_frames() -> None:
    run = profile_formats.get("pstats").read(FIXTURE.read_bytes())
    assert (run.unit, run.value_kind, run.tool_name, run.ordering_only) == (
        "s",
        "wall",
        "cProfile",
        True,
    )

    func_names = {f.function: f for f in run.frames}
    assert "run" in func_names
    assert "load" in func_names
    assert "<built-in method builtins.sum>" in func_names

    assert func_names["run"].calls == 1
    assert func_names["load"].calls == 5
    assert func_names["<built-in method builtins.sum>"].calls == 5

    # Check caller chain stacks
    assert len(run.stacks) > 0
    assert all(s.samples is None for s in run.stacks)

    conv = convert_profile(run, root=ROOT, symbols_by_file=SYMBOLS)
    entries = {e["function"]: e for e in conv.document["symbols"]}

    # Default value_kind is wall; value_kind flag can override to cpu
    assert conv.document["value_kind"] == "wall"
    conv_cpu = convert_profile(run, root=ROOT, symbols_by_file=SYMBOLS, value_kind="cpu")
    assert conv_cpu.document["value_kind"] == "cpu"

    # Functions joined to indexed symbols
    assert "pkg.app.App.run" in entries
    assert "pkg.app.App.load" in entries

    # Calls is recorded on entries
    assert entries["pkg.app.App.run"]["calls"] == 1
    assert entries["pkg.app.App.load"]["calls"] == 5

    # Frames outside checkout (<built-in>, ~) stay unresolved and count as outside_repo
    assert conv.document["skipped"] == {"outside_repo": 2}


def test_ordering_without_a_share() -> None:
    run = profile_formats.get("pstats").read(FIXTURE.read_bytes())
    conv = convert_profile(run, root=ROOT, symbols_by_file=SYMBOLS)

    # Conversion total and explained are None: no share is computed
    assert conv.total is None
    assert conv.explained is None
    assert conv.explained_share is None

    # v1 document sets no total and no explained
    assert "total" not in conv.document
    assert "explained" not in conv.document

    # summary sets no explained_share, skip_rate, or skipped_share_by_reason
    assert conv.skip_rate is None
    assert conv.summary()["total"] is None
    assert conv.summary()["explained_share"] is None
    assert conv.summary()["skip_rate"] is None
    assert conv.summary()["skipped_share_by_reason"] == {}
    assert conv.summary()["repo_self_share"] is None
    assert conv.summary()["wait_share"] is None
    assert conv.summary()["library_share"] is None
    assert conv.summary()["self_unattributed"] == {}

    # CLI report outputs no percentage shares for ordering-only run
    messages: list[str] = []

    class Notices:
        def print(self, msg: str) -> None:
            messages.append(msg)

    from repowise.cli.commands.evidence_cmd import _report_conversion

    _report_conversion(Notices(), "app.prof", conv.summary(), 1)
    assert len(messages) == 1
    assert "ordering of functions only" in messages[0]
    assert "%" not in messages[0]

    # Parse through v1 parser
    doc = parse_evidence(json.dumps(conv.document))
    assert doc.total is None
    assert doc.explained is None
    assert doc.total_samples is None

    # Functions are ordered: App.run is costlier inclusive than App.load
    symbols = conv.document["symbols"]
    assert len(symbols) == 2
    assert symbols[0]["function"] == "pkg.app.App.run"
    assert symbols[1]["function"] == "pkg.app.App.load"
    assert symbols[0]["total"] > symbols[1]["total"]

    # When derived into symbol_hotness, ordering_only is True and all shares are None
    run_facts = RunFacts(
        id="r1",
        workload_name="w-r1",
        workload_class="entry_point",
        value_kind="wall",
        unit="s",
        commit_sha="a" * 40,
        measured_at=None,
        total_value=None,
        explained_value=None,
        total_samples=None,
    )
    costs = {
        "r1": [
            _cost(
                "pkg.app.App.run", symbols[0]["total"], path="pkg/app.py", calls=symbols[0]["calls"]
            ),
            _cost(
                "pkg.app.App.load",
                symbols[1]["total"],
                path="pkg/app.py",
                calls=symbols[1]["calls"],
            ),
        ]
    }
    hot = _derive([run_facts], costs, head="a" * 40)
    assert len(hot) == 2
    r_run = hot["pkg/app.py::pkg.app.App.run"]
    r_load = hot["pkg/app.py::pkg.app.App.load"]

    assert (r_run["rank"], r_run["tier"], r_run["ordering_only"]) == (1, "ranked", True)
    assert (r_load["rank"], r_load["tier"], r_load["ordering_only"]) == (2, "ranked", True)

    assert r_run["total_share"] is None
    assert r_run["self_share"] is None
    assert r_run["share_low"] is None
    assert r_run["share_high"] is None
    assert r_run["explained_share"] is None


def test_corrupt_and_non_pstats_marshal_files_are_refused_with_remedy() -> None:
    read = profile_formats.get("pstats").read

    # Corrupt or truncated file raises EvidenceFormatError with remedy
    with pytest.raises(EvidenceFormatError) as exc_info:
        read(FIXTURE.read_bytes()[:20])
    assert "readable pstats file" in str(exc_info.value)
    assert exc_info.value.hint

    # Non-marshal file raises EvidenceFormatError
    with pytest.raises(EvidenceFormatError) as exc_info:
        read(b"not a marshal file at all")
    assert "readable pstats file" in str(exc_info.value)
    assert exc_info.value.hint

    # A marshal file that is not a dictionary raises EvidenceFormatError with remedy
    with pytest.raises(EvidenceFormatError) as exc_info:
        read(marshal.dumps([1, 2, 3]))
    assert "not pstats data" in str(exc_info.value)
    assert "python -m cProfile" in exc_info.value.hint

    # A marshal dict with string keys (not pstats tuple keys) raises EvidenceFormatError with remedy
    with pytest.raises(EvidenceFormatError) as exc_info:
        read(marshal.dumps({"some_key": "some_value"}))
    assert "not pstats data" in str(exc_info.value)
    assert "python -m cProfile" in exc_info.value.hint

    # A marshal dict with tuple keys but invalid stats values raises EvidenceFormatError with remedy
    with pytest.raises(EvidenceFormatError) as exc_info:
        read(marshal.dumps({("a.py", 1, "foo"): (1, 2)}))
    assert "not pstats data" in str(exc_info.value)
    assert "python -m cProfile" in exc_info.value.hint

    # A marshal dict with valid top-level entry but malformed caller key raises EvidenceFormatError
    with pytest.raises(EvidenceFormatError) as exc_info:
        read(
            marshal.dumps(
                {("a.py", 1, "foo"): (1, 1, 0.1, 0.1, {(1, 2, 3): (1, 1, 0.1, 0.1)})}
            )
        )
    assert "not pstats data" in str(exc_info.value)
    assert "python -m cProfile" in exc_info.value.hint

    with pytest.raises(EvidenceFormatError) as exc_info:
        read(
            marshal.dumps(
                {("a.py", 1, "foo"): (1, 1, 0.1, 0.1, {("a.py", "bad_line", "bar"): 1})}
            )
        )
    assert "not pstats data" in str(exc_info.value)
    assert "python -m cProfile" in exc_info.value.hint

    # Exceeding size cap raises EvidenceFormatError
    with pytest.raises(EvidenceFormatError) as exc_info:
        read(b"0" * (MAX_BYTES + 1))
    assert "over the cap" in str(exc_info.value)


def test_folded_closure_frames_do_not_inflate_symbol_call_counts() -> None:
    lines = [
        "class App:\n",
        "    def run(self):\n",
        "        xs = [1, 2]\n",
        "        return sum(x for x in xs)\n",
    ]
    frames = (
        ProfileFrame(
            path=f"{ROOT}\\pkg\\app.py",
            line=2,
            language="python",
            function="App.run",
            calls=1,
        ),
        ProfileFrame(
            path=f"{ROOT}\\pkg\\app.py",
            line=4,
            language="python",
            function="App.run.<locals>.<genexpr>",
            calls=100,
        ),
    )
    stacks = (
        Stack(frames=(0,), weight=0.1, samples=None),
        Stack(frames=(0, 1), weight=0.9, samples=None),
    )
    run = ProfileRun(
        frames=frames,
        stacks=stacks,
        unit="s",
        value_kind="cpu",
        tool_name="cProfile",
        ordering_only=True,
    )
    conv = convert_profile(
        run,
        root=ROOT,
        symbols_by_file=SYMBOLS,
        read_lines=lambda _p: lines,
    )
    entries = {e["function"]: e for e in conv.document["symbols"]}
    # Only the symbol's own frame call count contributes (1, not 101)
    assert entries["pkg.app.App.run"]["calls"] == 1
