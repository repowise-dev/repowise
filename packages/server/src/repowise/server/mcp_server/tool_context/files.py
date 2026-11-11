"""``include=["files"]`` on a directory card: every file under it, with its tests.

The test signal is :func:`~repowise.core.analysis.pr_blast.file_tests`, the
same rows ``get_risk``'s test gaps read, so the two tools never disagree on
whether a file is tested.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from repowise.core.analysis.pr_blast import FileTests, file_tests
from repowise.core.analysis.test_reachability import cached_test_files
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server._directory import _dir_prefix, _rows_under
from repowise.server.mcp_server._helpers import LIKE_ESCAPE, escape_like

if TYPE_CHECKING:
    from repowise.server.mcp_server.tool_context.sections import CardState

#: Rows one directory card lists. A larger directory says how many it left out
#: and is listed in full by naming its subdirectories as targets.
MAX_FILE_ROWS = 100

#: Serialized size the rows may take, so the block fits the include budget
#: (32,000 chars) beside the card instead of being dropped whole.
MAX_ROW_CHARS = 16_000

#: Tests shown per evidence kind on one row; the rest are counted.
_MAX_TESTS_PER_ROW = 2

_BASIS = (
    "has_test is true when a test file has its file name (named_tests, nearest "
    "directory first, possibly elsewhere in the repo), a test reaches it through "
    "calls or imports (reached_by), or a per-test coverage row exists "
    "(covered_by); false when none does. type_only (only types or re-exports) and "
    "test files need no test; test_owed false marks generated, vendored, build, tooling, "
    "docs example or migration code; new files are not indexed yet, so neither is claimed."
)


async def files_section(card: CardState) -> None:
    """Opt-in ``files`` block on a module or directory card."""
    if not card.wants("files"):
        return
    if card.dir_path is None:
        card.data["files_basis"] = "directory targets only"
        return
    test_files = await cached_test_files(card.session, card.repo_id)
    # Code files first: a cap then cuts test files, which need no test themselves.
    paths = sorted(await _paths_under(card), key=lambda p: (p in test_files, p))
    found = await file_tests(card.session, card.repo_id, paths[:MAX_FILE_ROWS])
    rows = _fit([_row(path, tests) for path, tests in found.items()])
    block: dict[str, Any] = {
        "total": len(paths),
        "complete": len(rows) == len(paths),
        "counts": _counts(found[row["path"]] for row in rows),
        "rows": rows,
        "basis": _BASIS,
    }
    if len(rows) < len(paths):
        block["omitted"] = len(paths) - len(rows)
        block["hint"] = (
            f"first {len(rows)}, code files before tests, each by path; "
            "a subdirectory target lists its own files"
        )
    if not test_files:
        block["note"] = "the index holds no test files, so has_test false is not evidence"
    card.data["files"] = block


async def _paths_under(card: CardState) -> set[str]:
    """Files under the directory: git rows, and graph file nodes for files without one."""
    res = await card.session.execute(
        select(GraphNode.node_id.label("file_path")).where(
            GraphNode.repository_id == card.repo_id,
            GraphNode.node_type == "file",
            GraphNode.node_id.like(
                f"{escape_like(_dir_prefix(card.dir_path))}%", escape=LIKE_ESCAPE
            ),
        )
    )
    nodes = _rows_under(card.dir_path, list(res.all()), card.query.exclude_spec)
    return {r.file_path for r in [*await card.dir_rows(), *nodes]}


def _fit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The head of *rows* whose serialized size stays within ``MAX_ROW_CHARS``."""
    used = 0
    for i, row in enumerate(rows):
        used += len(json.dumps(row)) + 1
        if used > MAX_ROW_CHARS:
            return rows[:i]
    return rows


def _counts(rows: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row.state] = out.get(row.state, 0) + 1
    return out


def _row(path: str, tests: FileTests) -> dict[str, Any]:
    if tests.state == "test":
        return {"path": path, "is_test": True}
    if tests.state == "not_code":
        return {"path": path, "code": False}
    if tests.state == "new":
        return {"path": path, "new": True}
    if tests.state == "not_applicable":
        return {"path": path, "test_owed": False}
    row: dict[str, Any] = {"path": path, "has_test": tests.state == "tested"}
    if tests.state == "type_only":
        row["type_only"] = True
    # A named test usually reaches its file too; it is listed once.
    reached = tuple(t for t in tests.reached_by if t not in tests.named_by)
    for key, found in (
        ("named_tests", tests.named_by),
        ("reached_by", reached),
        ("covered_by", tests.covered_by),
    ):
        if found:
            row[key] = list(found[:_MAX_TESTS_PER_ROW])
            if len(found) > _MAX_TESTS_PER_ROW:
                row[f"{key}_total"] = len(found)
    return row
