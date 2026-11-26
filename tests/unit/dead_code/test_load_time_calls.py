"""An unused export initialized by a call is never deletion-ready."""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code.deletion_gate import apply_deletion_gate
from repowise.core.analysis.dead_code.load_time_calls import mark_load_time_calls
from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def _finding(path: str, name: str, line: int, kind: str = "constant") -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=DeadCodeKind.UNUSED_EXPORT,
        file_path=path,
        symbol_name=name,
        symbol_kind=kind,
        confidence=1.0,
        reason="no importers",
        last_commit_at=None,
        commit_count_90d=0,
        lines=3,
        evidence=[],
        safe_to_delete=True,
        primary_owner=None,
        age_days=None,
        start_line=line,
        end_line=line + 2,
    )


def _marked(source: str, name: str, path: str = "src/actions.ts", kind: str = "constant") -> bool:
    lines = source.splitlines()
    line = next(i for i, text in enumerate(lines, 1) if name in text)
    (finding,) = mark_load_time_calls([_finding(path, name, line, kind)], {path: source.encode()})
    return finding.runs_at_load


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("export const actionZoom = register({\n  name: 'zoom',\n});\n", "actionZoom"),
        ("export const store = new Store<State>(initial);\n", "store"),
        ("export const t: Typed<(a: number) => void> = make<T>(x);\n", "t"),
        ("export const x = await load();\n", "x"),
        ("handler = router.add_route('/x', view)\n", "handler"),
        ("export const boot = (() => {\n  install();\n})();\n", "boot"),
        ("pub static CACHE: Lazy<Cache> = Lazy::new(Cache::default);\n", "CACHE"),
        ("export const m = registry?.add({ id: 1 });\n", "m"),
        ("export const box: Box<T = string> = make();\n", "box"),
    ],
)
def test_a_value_initialized_by_a_call_runs_at_load(source: str, name: str) -> None:
    assert _marked(source, name)


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("export const FORMATS = [1, 2, 3];\n", "FORMATS"),
        ("export const isEven = (n: number) => n % 2 === 0;\n", "isEven"),
        ("export const run = async (n) => n;\n", "run"),
        ("export const f = function (n) { return n; };\n", "f"),
        ("export const LIMIT = 10;\n", "LIMIT"),
        ("export const same = a == b(1);\n", "same"),
        ("export const typed = (a: number): number => a + 1;\n", "typed"),
        ("export const box: Box<T = string> = [];\n", "box"),
    ],
)
def test_a_literal_or_function_value_does_not(source: str, name: str) -> None:
    assert not _marked(source, name)


def test_a_function_declaration_is_not_a_candidate() -> None:
    assert not _marked("export function register(x) { return x; }\n", "register", kind="function")


def test_the_gate_withholds_it_and_keeps_confidence() -> None:
    path = "src/actions.ts"
    source = b"export const actionZoom = register({\n  name: 'zoom',\n});\n"
    finding = _finding(path, "actionZoom", 1)
    mark_load_time_calls([finding], {path: source})
    graph = _build_graph({path: {"node_type": "file", "language": "typescript", "symbols": []}})
    apply_deletion_gate([finding], graph)
    assert not finding.safe_to_delete
    assert finding.confidence == 1.0
    assert any("calls something when the module loads" in line for line in finding.evidence)


def test_a_file_missing_from_the_source_map_is_read_from_disk(tmp_path) -> None:
    path = "src/actions.ts"
    (tmp_path / "src").mkdir()
    (tmp_path / path).write_text("export const actionZoom = register({ name: 'zoom' });\n")
    (finding,) = mark_load_time_calls([_finding(path, "actionZoom", 1)], {}, tmp_path)
    assert finding.runs_at_load


def test_a_source_that_cannot_be_read_is_not_proven_inert(tmp_path) -> None:
    (finding,) = mark_load_time_calls([_finding("src/gone.ts", "x", 1)], {}, tmp_path)
    assert finding.runs_at_load
