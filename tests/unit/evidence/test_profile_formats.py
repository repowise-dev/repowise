"""Profile formats: sniffing, reading, and the one conversion path to v1."""

from __future__ import annotations

import json

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


def test_a_file_with_no_stack_line_is_refused() -> None:
    with pytest.raises(EvidenceFormatError):
        profile_formats.get("collapsed").read(b"hello\n")


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


def test_speedscope_is_sniffed_by_its_schema_key_not_the_word() -> None:
    assert detect_profile_format(b'{"name": "speedscope run", "profiles": []}') is None
    assert detect_profile_format(
        b'{"exporter": "x", "$schema": "https://www.speedscope.app/file-format-schema.json"}'
    ) == "speedscope"
