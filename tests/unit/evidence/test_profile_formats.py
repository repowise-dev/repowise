"""Profile formats: sniffing, reading, and the one conversion path to v1."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import (
    ProfileFormat,
    ProfileRun,
    Stack,
    detect_profile_format,
    profile_formats,
)
from repowise.core.frame_resolver import ProfileFrame
from repowise.core.workspace.repo_index import IndexedSymbol

ROOT = "C:\\work\\repo"
OTHER = "C:\\work\\other-checkout"


def _sym(path: str, qualified: str, start: int, end: int, kind: str = "method") -> IndexedSymbol:
    name = qualified.rsplit(".", 1)[-1]
    return IndexedSymbol(
        symbol_id=f"{path}::{qualified}", name=name, qualified_name=qualified, kind=kind,
        signature="", file_path=path, start_line=start, end_line=end, visibility="public",
    )


APP = _sym("pkg/app.py", "pkg.app.App", 1, 40, "class")
RUN = _sym("pkg/app.py", "pkg.app.App.run", 3, 20)
LOAD = _sym("pkg/app.py", "pkg.app.App.load", 22, 30)
TEST = _sym("tests/test_app.py", "tests.test_app.test_run", 1, 5, "function")
SYMBOLS = {"pkg/app.py": [APP, RUN, LOAD], "tests/test_app.py": [TEST]}


def _f(path: str) -> str:
    return f"{ROOT}\\{path}" if path else ""


# -- speedscope ----------------------------------------------------------------

# py-spy shape: sampled, root-first stacks, executing lines, qualified names.
PYSPY = {
    "$schema": "https://www.speedscope.app/file-format-schema.json",
    "exporter": "py-spy@0.4.3",
    "shared": {"frames": [
        {"name": "<module>", "file": "C:\\Python\\runpy.py", "line": 88},           # 0 outside
        {"name": "App.run", "file": _f("pkg\\app.py"), "line": 10},                # 1
        {"name": "App.load", "file": _f("pkg\\app.py"), "line": 25},               # 2
        {"name": "read", "file": "C:\\Python\\json.py", "line": 7},                # 3 outside
        {"name": "App.load", "file": f"{OTHER}\\pkg\\app.py", "line": 25},         # 4 other checkout
        {"name": "helper", "file": _f("pkg\\app.py"), "line": 12},                 # 5 mismatch
        {"name": "test_run", "file": _f("tests\\test_app.py"), "line": 3},         # 6 test code
    ]},
    "profiles": [{
        "type": "sampled", "unit": "seconds", "startValue": 0, "endValue": 1,
        "samples": [[0, 1, 2, 3], [0, 1, 2], [0, 4], [0, 1, 5], [], [6, 1]],
        "weights": [0.4, 0.1, 0.2, 0.1, 0.1, 0.1],
    }],
}

# pyinstrument shape: evented, function first lines, bare names.
PYINSTRUMENT = {
    "$schema": "https://www.speedscope.app/file-format-schema.json",
    "exporter": "pyinstrument",
    "shared": {"frames": [
        {"name": "run", "file": _f("pkg\\app.py"), "line": 3},
        {"name": "load", "file": _f("pkg\\app.py"), "line": 22},
        {"name": "[self]", "file": None, "line": None},
    ]},
    "profiles": [{
        "type": "evented", "unit": "seconds", "startValue": 0, "endValue": 3,
        "events": [
            {"type": "O", "at": 0, "frame": 0},
            {"type": "O", "at": 1, "frame": 1},
            {"type": "C", "at": 2, "frame": 1},
            {"type": "O", "at": 2, "frame": 2},
            {"type": "C", "at": 3, "frame": 2},
            {"type": "C", "at": 3, "frame": 0},
        ],
    }],
}


def _bytes(doc: dict) -> bytes:
    return json.dumps(doc).encode()


def _convert(run: ProfileRun, **kw):
    kw.setdefault("value_kind", "wall")
    return convert_profile(run, root=ROOT, symbols_by_file=SYMBOLS, **kw)


def _entries(conversion) -> dict[str, dict]:
    return {e["function"]: e for e in conversion.document["symbols"]}


def test_speedscope_and_collapsed_are_sniffed_by_content() -> None:
    assert detect_profile_format(_bytes(PYSPY)) == "speedscope"
    assert detect_profile_format(b"main (/r/a.py:3);f (/r/a.py:9) 12\n") == "collapsed"
    assert detect_profile_format(b'{"format": "repowise-evidence-v1"}') is None
    assert detect_profile_format(b"<coverage line-rate='1'/>") is None


def test_sampled_speedscope_keeps_inclusive_and_self_and_explains_by_production() -> None:
    run = profile_formats.get("speedscope").read(_bytes(PYSPY))
    assert (run.unit, run.tool_name, run.tool_version, run.value_kind) == (
        "s", "py-spy", "0.4.3", None,
    )
    conv = _convert(run)
    got = _entries(conv)
    # json.read below App.load is outside the checkout: its time is load's own.
    assert got["pkg.app.App.load"]["self"] == pytest.approx(0.5)
    assert got["pkg.app.App.load"]["total"] == pytest.approx(0.5)
    assert got["pkg.app.App.run"]["total"] == pytest.approx(0.7)
    assert got["pkg.app.App.run"]["self"] == pytest.approx(0.1)  # under test_run
    assert got["pkg.app.App.run"]["total_samples"] == 4
    assert got["tests.test_app.test_run"]["total"] == pytest.approx(0.1)
    # The stack ending in an unresolved repo frame charges no self time.
    assert sum(e["self"] for e in got.values()) == pytest.approx(0.6)
    assert conv.total == pytest.approx(1.0)  # the empty stack counts
    # Explained: stacks with a production frame, so not the other checkout's stack nor
    # the empty one. The test-only frame does not count alone; its stack also runs App.run.
    assert conv.explained == pytest.approx(0.7)
    assert conv.summary()["explained_share"] == pytest.approx(0.7)
    assert conv.document["skipped"] == {"outside_repo": 3, "second_signal_mismatch": 1}


def test_a_frame_in_another_checkout_is_never_joined_by_suffix() -> None:
    run = profile_formats.get("speedscope").read(_bytes(PYSPY))
    conv = _convert(run)
    # App.load at the other checkout's path carried 0.2 s: outside, with runpy and json.
    assert conv.frame_time["outside_root"] == pytest.approx(1.4)
    assert _entries(conv)["pkg.app.App.load"]["total"] == pytest.approx(0.5)


def test_skip_rate_is_the_share_of_repo_frame_time_not_resolved() -> None:
    conv = _convert(profile_formats.get("speedscope").read(_bytes(PYSPY)))
    # Repo frame time: run 0.7 + load 0.5 + helper 0.1 + test_run 0.1.
    assert conv.skip_rate == pytest.approx(0.1 / 1.4)
    assert conv.summary()["skipped_share_by_reason"] == {"second_signal_mismatch": 0.0714}


def test_evented_speedscope_charges_each_interval_to_the_open_stack() -> None:
    run = profile_formats.get("speedscope").read(_bytes(PYINSTRUMENT))
    assert run.value_kind == "wall"  # pyinstrument times the wall clock
    assert all(s.samples is None for s in run.stacks)
    conv = _convert(run, value_kind=None)
    got = _entries(conv)
    assert (got["pkg.app.App.run"]["total"], got["pkg.app.App.run"]["self"]) == (3.0, 2.0)
    assert (got["pkg.app.App.load"]["total"], got["pkg.app.App.load"]["self"]) == (1.0, 1.0)
    assert "self_samples" not in got["pkg.app.App.run"]


def test_the_document_goes_through_the_v1_parser() -> None:
    conv = _convert(profile_formats.get("speedscope").read(_bytes(PYSPY)))
    doc = parse_evidence(json.dumps(conv.document))
    assert (doc.kind, doc.value_kind, doc.unit, doc.tool_name) == ("profile", "wall", "s", "py-spy")
    assert {(e["path"], e["function"], e["line"]) for e in doc.entries} == {
        ("pkg/app.py", "pkg.app.App.run", 3),
        ("pkg/app.py", "pkg.app.App.load", 22),
        ("tests/test_app.py", "tests.test_app.test_run", 1),
    }


def test_dirty_files_are_skipped_through_the_resolver() -> None:
    conv = _convert(
        profile_formats.get("speedscope").read(_bytes(PYSPY)), dirty_paths=["pkg/app.py"]
    )
    assert set(_entries(conv)) == {"tests.test_app.test_run"}
    assert conv.document["skipped"]["dirty"] == 3


def test_a_memory_profile_or_a_broken_file_is_refused() -> None:
    bytes_profile = {**PYSPY, "profiles": [{**PYSPY["profiles"][0], "unit": "bytes"}]}
    with pytest.raises(EvidenceFormatError):
        profile_formats.get("speedscope").read(_bytes(bytes_profile))
    with pytest.raises(EvidenceFormatError):
        profile_formats.get("speedscope").read(b'{"speedscope": 1}')


def test_a_profile_without_its_clock_is_refused() -> None:
    run = profile_formats.get("speedscope").read(_bytes(PYSPY))
    with pytest.raises(EvidenceFormatError, match="CPU or wall"):
        _convert(run, value_kind=None)


def test_typescript_frames_of_the_checkout_are_refused() -> None:
    doc = {**PYSPY, "shared": {"frames": [{"name": "send", "file": _f("src\\app.ts"), "line": 4,
                                           "col": 3}]},
           "profiles": [{"type": "sampled", "unit": "none", "samples": [[0]]}]}
    run = profile_formats.get("speedscope").read(_bytes(doc))
    with pytest.raises(EvidenceFormatError, match="TypeScript"):
        _convert(run)


# -- collapsed -----------------------------------------------------------------

COLLAPSED = "\n".join([
    f"<module> ({ROOT}\\run.py:1);App.run ({ROOT}\\pkg\\app.py:10);App.load ({ROOT}\\pkg\\app.py:25) 30",
    f"<module> ({ROOT}\\run.py:1);App.run ({ROOT}\\pkg\\app.py:10);dumps (C:\\Python\\json.py:5) 10",
    f"py::run:{ROOT}\\pkg\\app.py;[unknown] 5",  # perf: a path, no line
    f"App.load ({OTHER}\\pkg\\app.py:25) 4",
    " 3",  # an idle sample with no frames
    "not a stack line",
])


def test_collapsed_lines_carry_name_path_and_line() -> None:
    run = profile_formats.get("collapsed").read(COLLAPSED.encode())
    assert run.unit == "samples" and run.value_kind is None
    assert run.skipped == {"malformed_line": 1}
    frame = run.frames[1]
    assert (frame.function, frame.path, frame.line, frame.language) == (
        "App.run", f"{ROOT}\\pkg\\app.py", 10, "python",
    )
    assert sum(s.weight for s in run.stacks) == 52


def test_collapsed_conversion_skips_lineless_frames_and_other_checkouts() -> None:
    conv = _convert(profile_formats.get("collapsed").read(COLLAPSED.encode()))
    got = _entries(conv)
    assert (got["pkg.app.App.run"]["total"], got["pkg.app.App.run"]["self"]) == (40, 10)
    assert got["pkg.app.App.load"]["total_samples"] == 30
    # run.py is not indexed; the perf frame has no line to check its name on.
    assert conv.document["skipped"] == {
        "bad_line": 1, "malformed_line": 1, "not_indexed": 1, "outside_repo": 3,
    }
    assert conv.total == 52
    assert conv.explained == 40
    # Every stack counted its samples, so the run's count is known.
    assert conv.document["total_samples"] == 52


def test_a_file_with_no_stack_line_is_refused() -> None:
    with pytest.raises(EvidenceFormatError):
        profile_formats.get("collapsed").read(b"hello\n")


# -- pprof ---------------------------------------------------------------------

# A real `go test -run TestPrimes -count=1 -cpuprofile cpu.pprof ./calc` of the
# fixture module, run from D:\src\pprofgo. isPrime and Primes were inlined into
# TestPrimes, so one location carries three lines.
PPROF_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "pprof_go"
GO_ROOT = "D:\\src\\pprofgo"


def _go_symbols() -> dict[str, list[IndexedSymbol]]:
    """The fixture's functions, with spans checked against its source."""
    spans = {
        "calc/calc.go": [
            ("Primes", 5, 13), ("isPrime", 16, 23), ("SumSquares", 26, 32), ("square", 34, 34),
        ],
        "calc/calc_test.go": [("TestPrimes", 5, 17), ("BenchmarkPrimes", 19, 23)],
    }
    out: dict[str, list[IndexedSymbol]] = {}
    for path, funcs in spans.items():
        lines = (PPROF_DIR / path).read_text(encoding="utf-8").splitlines()
        for name, start, end in funcs:
            assert lines[start - 1].startswith(f"func {name}("), (path, name)
            out.setdefault(path, []).append(_sym(path, f"calc.{name}", start, end, "function"))
    return out


def _go_convert(run: ProfileRun, symbols: dict | None = None):
    return convert_profile(run, root=GO_ROOT, symbols_by_file=symbols or _go_symbols())


def _pb_varint(value: int) -> bytes:
    value &= (1 << 64) - 1
    out = bytearray()
    while True:
        out.append((value & 0x7F) | (0x80 if value > 0x7F else 0))
        value >>= 7
        if not value:
            return bytes(out)


def _pb(field: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return _pb_varint(field << 3) + _pb_varint(value)
    return _pb_varint(field << 3 | 2) + _pb_varint(len(value)) + value


def _pprof(
    samples: list[tuple[list[int], list[int]]],
    locations: dict[int, list[tuple[int, int]]],
    functions: dict[int, tuple[str, str, int]],
    sample_types: tuple[tuple[str, str], ...] = (("samples", "count"), ("cpu", "nanoseconds")),
    packed: bool = True,
) -> bytes:
    """A Profile message; each sample is (location ids leaf first, values)."""
    strings = [""]

    def s(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    out = _pb(9, 1_700_000_000)  # time_nanos first, as Go writes it
    out += b"".join(_pb(1, _pb(1, s(t)) + _pb(2, s(u))) for t, u in sample_types)
    for ids, values in samples:
        if packed:
            body = _pb(1, b"".join(map(_pb_varint, ids))) + _pb(
                2, b"".join(map(_pb_varint, values))
            )
        else:
            body = b"".join(_pb(1, i) for i in ids) + b"".join(_pb(2, v) for v in values)
        out += _pb(2, body)
    for loc_id, lines in locations.items():
        line_msgs = b"".join(_pb(4, _pb(1, f) + _pb(2, ln)) for f, ln in lines)
        out += _pb(4, _pb(1, loc_id) + line_msgs)
    for fn_id, (name, path, start) in functions.items():
        out += _pb(5, _pb(1, fn_id) + _pb(2, s(name)) + _pb(4, s(path)) + _pb(5, start))
    return out + b"".join(_pb(6, text.encode()) for text in strings)


def test_a_go_cpu_profile_is_sniffed_gzipped_or_raw() -> None:
    gz = (PPROF_DIR / "cpu.pprof").read_bytes()
    assert detect_profile_format(gz) == "pprof"
    assert detect_profile_format(gzip.decompress(gz)) == "pprof"
    assert detect_profile_format(gzip.compress(b"hello world\n")) is None
    assert detect_profile_format(b"\x0a\x04hello") is None


def test_comments_packed_or_not_still_sniff_and_read() -> None:
    body = _pprof(
        samples=[([1], [2, 20])],
        locations={1: [(1, 8)]},
        functions={1: ("example.com/pprofgo/calc.Primes", "D:/src/pprofgo/calc/calc.go", 5)},
    )
    for comment in (_pb(13, _pb_varint(1) + _pb_varint(2)), _pb(13, 1) + _pb(13, 2)):
        # The comment leads, so the sniff walks over it before any sample type.
        data = comment + body
        assert detect_profile_format(data) == "pprof"
        run = profile_formats.get("pprof").read(gzip.compress(data))
        assert sum(s.samples or 0 for s in run.stacks) == 2


def test_a_real_go_profile_reads_cpu_time_with_inlined_frames_innermost_last() -> None:
    run = profile_formats.get("pprof").read((PPROF_DIR / "cpu.pprof").read_bytes())
    assert (run.unit, run.value_kind) == ("ns", "cpu")
    assert sum(s.samples or 0 for s in run.stacks) == 106
    assert sum(s.weight for s in run.stacks) == 1_060_000_000  # 10 ms per sample
    names: Counter[tuple[str, ...]] = Counter()
    for stack in run.stacks:
        names[tuple(run.frames[i].function.rsplit(".", 1)[-1] for i in stack.frames)] += (
            stack.samples or 0
        )
    # One location: isPrime inlined into Primes inlined into TestPrimes.
    assert names[("tRunner", "TestPrimes", "Primes", "isPrime")] == 59
    is_prime = next(f for f in run.frames if f.function.endswith(".isPrime"))
    assert (is_prime.path, is_prime.start_line, is_prime.language) == (
        "D:/src/pprofgo/calc/calc.go", 16, "go",
    )


def test_a_real_go_profile_joins_inlined_frames_to_their_own_functions() -> None:
    run = profile_formats.get("pprof").read((PPROF_DIR / "cpu.pprof").read_bytes())
    conv = _go_convert(run)
    got = _entries(conv)
    assert got["calc.isPrime"]["total_samples"] == 59
    assert got["calc.isPrime"]["self_samples"] == 59
    assert got["calc.Primes"]["total_samples"] == 60
    assert got["calc.SumSquares"]["self_samples"] == 28
    assert got["calc.TestPrimes"]["total_samples"] == 106
    assert conv.skip_rate == 0
    # testing.tRunner, in GOROOT, is the one frame outside the checkout.
    assert conv.document["skipped"] == {"outside_repo": 1}
    # Only the stack in TestPrimes alone has no production frame.
    assert conv.explained_share == pytest.approx(88 / 106)


def test_raw_and_gzipped_profiles_read_the_same() -> None:
    gz = (PPROF_DIR / "cpu.pprof").read_bytes()
    read = profile_formats.get("pprof").read
    assert read(gzip.decompress(gz)) == read(gz)


def test_a_line_labelled_with_the_wrong_inlined_function_is_skipped() -> None:
    # The compiler labelled calc.go:18, inside isPrime, as Primes (start line 5).
    data = _pprof(
        samples=[([1], [7, 70])],
        locations={1: [(1, 18)]},
        functions={1: ("example.com/pprofgo/calc.Primes", "D:/src/pprofgo/calc/calc.go", 5)},
    )
    conv = _go_convert(profile_formats.get("pprof").read(data))
    assert conv.document["symbols"] == []
    assert conv.document["skipped"] == {"second_signal_mismatch": 1}
    assert conv.skip_rate == 1


def test_an_index_whose_start_line_moved_skips_the_frame() -> None:
    symbols = _go_symbols()
    symbols["calc/calc.go"] = [
        _sym("calc/calc.go", "calc.isPrime", 15, 23, "function") if s.name == "isPrime" else s
        for s in symbols["calc/calc.go"]
    ]
    run = profile_formats.get("pprof").read((PPROF_DIR / "cpu.pprof").read_bytes())
    conv = _go_convert(run, symbols)
    assert "calc.isPrime" not in _entries(conv)
    assert conv.document["skipped"]["second_signal_mismatch"] == 2  # lines 17 and 18


def test_frames_outside_the_root_and_unsymbolized_addresses_are_outside() -> None:
    data = _pprof(
        samples=[([1, 2, 3], [3, 30])],
        locations={1: [(1, 18)], 2: [(2, 8)], 3: []},
        functions={
            1: ("example.com/pprofgo/calc.isPrime", "D:/src/other/calc/calc.go", 16),
            2: ("example.com/pprofgo/calc.Primes", "D:/src/pprofgo/calc/calc.go", 5),
        },
        packed=False,
    )
    run = profile_formats.get("pprof").read(gzip.compress(data))
    conv = _go_convert(run)
    # isPrime from another checkout never joins; Primes, its caller, takes the time.
    assert _entries(conv)["calc.Primes"]["self"] == 30
    assert conv.document["skipped"] == {"outside_repo": 2}


def test_a_trimpath_build_or_a_memory_profile_is_refused() -> None:
    read = profile_formats.get("pprof").read
    with pytest.raises(EvidenceFormatError, match="-trimpath"):
        read((PPROF_DIR / "trimpath.pprof").read_bytes())
    heap = _pprof(
        samples=[([1], [1, 512])],
        locations={1: [(1, 8)]},
        functions={1: ("calc.Primes", "D:/src/pprofgo/calc/calc.go", 5)},
        sample_types=(("alloc_objects", "count"), ("alloc_space", "bytes")),
    )
    with pytest.raises(EvidenceFormatError, match="alloc_space/bytes"):
        read(heap)
    with pytest.raises(EvidenceFormatError):
        read((PPROF_DIR / "cpu.pprof").read_bytes()[:200])
    missing_location = _pprof(
        samples=[([9], [1, 10])], locations={1: [(1, 8)]},
        functions={1: ("calc.Primes", "D:/src/pprofgo/calc/calc.go", 5)},
    )
    with pytest.raises(EvidenceFormatError, match="location 9"):
        read(missing_location)


# -- registry ------------------------------------------------------------------


def test_a_plugin_format_is_one_registry_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def read(data: bytes) -> ProfileRun:
        frame = ProfileFrame(f"{ROOT}\\pkg\\app.py", 10, "python", function="App.run")
        return ProfileRun(frames=(frame,), stacks=(Stack((0,), 7.0, 7),), unit="samples")

    monkeypatch.setitem(profile_formats._impls, "toy", ProfileFormat(lambda h: h == b"TOY", read))
    assert detect_profile_format(b"TOY") == "toy"
    conv = _convert(profile_formats.get("toy").read(b"TOY"))
    assert _entries(conv)["pkg.app.App.run"]["total"] == 7


def test_a_timed_sampled_profile_needs_a_weight_per_sample() -> None:
    profile = {**PYSPY["profiles"][0], "weights": [0.4]}
    with pytest.raises(EvidenceFormatError, match="weight"):
        profile_formats.get("speedscope").read(_bytes({**PYSPY, "profiles": [profile]}))


def test_one_stack_longer_than_the_sniff_window_is_still_collapsed() -> None:
    deep = ";".join(f"f{i} (/r/a.py:{i + 1})" for i in range(400)) + " 3\n"
    assert len(deep) > 4096
    assert detect_profile_format(deep.encode()) == "collapsed"


def test_module_body_self_time_is_reported_unattributed_not_dropped() -> None:
    doc = {**PYSPY, "shared": {"frames": [
        {"name": "<module>", "file": _f("pkg\\app.py"), "line": 35},
        {"name": "loads", "file": "C:\\Python\\json.py", "line": 3},
    ]}, "profiles": [{"type": "sampled", "unit": "none", "samples": [[0, 1], [0]],
                      "weights": [2, 1]}]}
    conv = _convert(profile_formats.get("speedscope").read(_bytes(doc)))
    assert conv.document["symbols"] == []
    assert conv.explained == 3.0
    assert conv.self_unattributed == {"module_body": 3.0}
    assert conv.summary()["self_unattributed"] == {"module_body": 1.0}


def test_unit_none_weights_are_sample_counts() -> None:
    doc = {**PYSPY, "profiles": [{"type": "sampled", "unit": "none",
                                  "samples": [[0, 1, 2], [0, 1]], "weights": [5, 2]}]}
    got = _entries(_convert(profile_formats.get("speedscope").read(_bytes(doc))))
    assert got["pkg.app.App.run"]["total"] == got["pkg.app.App.run"]["total_samples"] == 7
    assert got["pkg.app.App.load"]["self_samples"] == 5


def test_an_evented_profile_keeps_sample_counts_of_sampled_ones_in_the_file() -> None:
    sampled = {"type": "sampled", "unit": "seconds", "samples": [[2]], "weights": [0.5]}
    evented = {"type": "evented", "unit": "seconds", "events": [
        {"type": "O", "at": 0, "frame": 1}, {"type": "C", "at": 1, "frame": 1},
    ]}
    run = profile_formats.get("speedscope").read(
        _bytes({**PYSPY, "profiles": [sampled, evented]})
    )
    samples = {s.frames: s.samples for s in run.stacks}
    assert samples == {(2,): 1, (1,): None}
    # Part of the run counted no samples, so the run's count is unknown.
    assert "total_samples" not in _convert(run).document


def test_speedscope_is_sniffed_by_its_schema_key_not_the_word() -> None:
    assert detect_profile_format(b'{"name": "speedscope run", "profiles": []}') is None
    assert detect_profile_format(
        b'{"exporter": "x", "$schema": "https://www.speedscope.app/file-format-schema.json"}'
    ) == "speedscope"


# Waits, libraries and the repo code's own cost.
SPLIT = {**PYSPY, "shared": {"frames": [
    {"name": "<module>", "file": "C:\\Python\\runpy.py", "line": 88},                      # 0
    {"name": "App.run", "file": _f("pkg\\app.py"), "line": 10},                           # 1
    {"name": "App.load", "file": _f("pkg\\app.py"), "line": 25},                          # 2
    {"name": "EpollSelector.select", "file": "C:\\Python\\Lib\\selectors.py", "line": 9},  # 3
    {"name": "epoll.poll", "file": "<built-in>", "line": 0},                              # 4
    {"name": "read", "file": "C:\\Python\\json.py", "line": 7},                           # 5
    {"name": "execute", "file": "C:\\venv\\Lib\\site-packages\\sa\\engine.py", "line": 3},  # 6
    {"name": "get", "file": _f(".venv\\Lib\\site-packages\\lib\\x.py"), "line": 4},       # 7
    {"name": "Condition.wait", "file": "C:\\Python\\Lib\\threading.py", "line": 5},       # 8
]}, "profiles": [{
    "type": "sampled", "unit": "seconds",
    "samples": [[0, 1, 3, 4], [0, 1, 2, 5], [0, 1, 2, 6], [0, 1, 7], [0, 1, 7, 8], [0, 8]],
    "weights": [4, 2, 1, 1, 1, 1],
}]}


def test_waits_charge_no_self_time_and_libraries_stay_with_their_caller() -> None:
    conv = _convert(profile_formats.get("speedscope").read(_bytes(SPLIT)))
    got = _entries(conv)
    # The event loop's select under run is a wait; a library inside the checkout
    # (a venv) is charged to run like one outside it.
    assert (got["pkg.app.App.run"]["self"], got["pkg.app.App.run"]["total"]) == (1, 9)
    # json (stdlib) and sqlalchemy (a dependency) below load are load's cost.
    assert (got["pkg.app.App.load"]["self"], got["pkg.app.App.load"]["total"]) == (3, 3)
    # Costliest first by self, not by inclusive.
    assert [e["function"] for e in conv.document["symbols"]] == [
        "pkg.app.App.load", "pkg.app.App.run",
    ]
    assert (conv.repo_self, conv.waits, conv.libraries) == (2, 6, 2)
    summary = conv.summary()
    assert (summary["repo_self_share"], summary["wait_share"], summary["library_share"]) == (
        0.2, 0.6, 0.2,
    )
    assert (conv.document["repo_self"], conv.document["waits"]) == (2, 6)
    assert parse_evidence(json.dumps(conv.document)).waits == 6


def test_a_wait_is_a_runtime_or_standard_library_frame_only() -> None:
    from repowise.core.evidence.profile_convert import is_wait_frame

    def frame(path: str, name: str) -> ProfileFrame:
        return ProfileFrame(path=path, line=1, language="python", function=name)

    assert is_wait_frame(frame("/usr/lib/python3.12/threading.py", "Event.wait"))
    assert is_wait_frame(frame("/usr/lib/python3.12/concurrent/futures/_base.py", "result"))
    assert is_wait_frame(frame("<built-in>", "time.sleep"))
    assert not is_wait_frame(frame("/usr/lib/python3.12/threading.py", "Thread.start"))
    assert not is_wait_frame(frame("/venv/site-packages/pkg/queue.py", "get"))
    assert not is_wait_frame(frame("/usr/lib/python3.12/json/decoder.py", "decode"))


@pytest.mark.parametrize(("given", "read"), [(3, 3), (0, 0), (-1, None), ("3", None), (True, None)])
def test_a_speedscope_file_carries_the_failed_test_count_of_its_run(given, read) -> None:
    run = profile_formats.get("speedscope").read(_bytes({**PYINSTRUMENT, "testsFailed": given}))
    assert run.tests_failed == read
    assert profile_formats.get("speedscope").read(_bytes(PYINSTRUMENT)).tests_failed is None
