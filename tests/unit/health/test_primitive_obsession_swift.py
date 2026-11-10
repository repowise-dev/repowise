"""primitive_obsession on Swift: parameter types, and signatures Cocoa owns.

A Swift parameter sits on the declaration with no list node around it, so its
type is read there: closures, ``inout`` variables and rich types are not raw
values. ``@objc`` methods and delegate callbacks named after the object that
calls them (``func urlSession(_ session: URLSession, ...)``) implement a
protocol that owns their parameters.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.primitive_obsession import (
    PrimitiveObsessionDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity, walk_file_complexity

_FILLER = FunctionComplexity("filler", 900, 990, ccn=1, max_nesting=0, cognitive=0, nloc=70)
_PATH = "Sources/App/Copy.swift"

_SOURCE = """
final class Copier {
    func copy(
        from source: FilePath,
        to destination: FilePath,
        maxConcurrent: Int,
        onError: @escaping @Sendable (_ entry: DirectoryEntry, _ error: Error) async throws -> Void,
        shouldCopy: @escaping (FilePath) async -> Bool = { _ in true }
    ) {}
    func pad(_ text: String, width: Int, fill: Character, left: Bool, count: inout Int) {}
    func box(x: Int, y: Int, width: Int, height: Int?, label: String = "") {}
    init(host: String, port: Int, user: String, password: String, timeout: Double, tls: Bool) {}
    subscript(row: Int, column: Int, plane: Int, layer: Int, frame: Int) -> Int { return 0 }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSend sent: Int64, total: Int64, expected: Int64) {}
    func tableView(_ tableView: UITableView, row: Int, column: Int, width: Int, height: Int) {}
    @objc func onTimer(a: Int, b: Int, c: Int, d: Int, e: Int) {}
    func string(_ string: String, a: Int, b: Int, c: Int, d: Int) {}
    func request(_ session: URLSession, a: Int, b: Int, c: Int, d: Int) {}
}
"""


def _fns() -> dict[str, FunctionComplexity]:
    return {f.name: f for f in walk_file_complexity(_PATH, "swift", _SOURCE.encode())}


@pytest.mark.parametrize(
    ("name", "counts"),
    [
        ("copy", (5, 5, 1)),
        # ``inout`` is the caller's variable, not a raw value.
        ("pad", (5, 5, 4)),
        ("box", (5, 5, 5)),
        ("init", (6, 6, 6)),
        ("subscript", (5, 5, 5)),
    ],
)
def test_swift_parameter_types_are_read(name: str, counts: tuple[int, int, int]) -> None:
    fn = next(f for f in _fns().values() if f.name == name)
    assert (fn.param_count, fn.typed_param_count, fn.primitive_param_count) == counts


def test_only_raw_swift_signatures_are_flagged() -> None:
    fns = _fns()
    ctx = FileContext(
        file_path=_PATH, language="swift", nloc=80, has_test_file=False, module=None,
        is_test=False, all_functions=(*fns.values(), _FILLER),
    )
    flagged = {f.function_name for f in PrimitiveObsessionDetector().detect(ctx)}
    # ``copy`` passes paths and closures; ``urlSession``, ``tableView`` and
    # ``onTimer`` belong to a protocol; ``init`` has the constructor grace.
    # A callback name repeating a scalar (``string``) or another type
    # (``request``) is no delegate callback.
    assert flagged == {"pad", "box", "subscript", "string", "request"}
