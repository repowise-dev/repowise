"""Measure the serialized size of every MCP tool response against a real index.

The response budget is declared in characters, so this reports characters, using
the budgeter's own serialization. Two numbers per case: the raw handler result,
and the same call through the production middleware stack, which is where
budgeting happens. Raw over the ceiling with wrapped under it means the budget
did its job; both over means the tool is delivering unbounded.

Synthetic payloads cannot answer this — a tool is only as big as the repository
it is reading — so this needs a real indexed repo and does not run in CI. The
CI-side guarantee is `tests/unit/server/mcp/test_response_budget_contracts.py`,
which pins that every tool has a contract and that the floor truncates and
flags. Use this when changing a shed order, adding a tool, or checking whether a
cap still holds on a large repository.

    python scripts/measure_mcp_response_sizes.py --repo /path/to/indexed/repo

Workspace-only tools (`get_architecture`, `get_conformance`, `get_blast_radius`)
need `--repo` inside a workspace; elsewhere they return an error payload, which
is reported as `not measured` rather than as a bound.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import os
import sqlite3
import sys
import traceback
from typing import Any

#: Representative worst cases. Paths are resolved against the target repo and a
#: case whose target is missing is reported, never silently skipped.
CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("get_overview", "default", {}),
    ("get_overview", "all-includes", {"include": ["modules", "entry_points", "hotspots"]}),
    ("get_context", "one-file", {"targets": ["{file}"]}),
    ("get_context", "many-targets", {"targets": ["{file}", "{file2}"], "include": ["skeleton", "callers"]}),
    ("get_symbol", "largest-symbol", {"symbol_id": "{symbol}"}),
    ("get_symbol", "largest-depth-3", {"symbol_id": "{symbol}", "depth": 3, "context_lines": 50}),
    ("search_codebase", "limit-5", {"query": "{query}"}),
    ("search_codebase", "limit-50", {"query": "{query}", "limit": 50}),
    ("get_health", "default", {}),
    ("get_health", "limit-100", {"limit": 100}),
    ("get_health", "all-includes", {"include": ["files", "refactoring", "performance"], "limit": 100}),
    ("get_risk", "one-target", {"targets": ["{file}"]}),
    ("get_change_risk", "head", {"revspec": "HEAD"}),
    ("get_why", "targets", {"targets": ["{file}"]}),
    ("get_dead_code", "default", {}),
    ("get_dead_code", "limit-200", {"limit": 200, "min_confidence": 0.0, "include_internals": True}),
    ("get_execution_flows", "default", {}),
    ("get_execution_flows", "top-100-depth-20", {"top_n": 100, "max_depth": 20}),
    ("get_dependency_path", "two-files", {"source": "{file}", "target": "{file2}"}),
    ("get_architecture", "default", {}),
    ("get_conformance", "default", {}),
    ("get_blast_radius", "one-target", {"targets": ["{file}"]}),
    ("list_repos", "default", {}),
    ("get_answer", "how-question", {"question": "How does {query} work?"}),
]


def _size(payload: object) -> int:
    """Exactly what ``_budget.budgeter.response_chars`` measures."""
    return len(json.dumps(payload, separators=(",", ":"), default=str))


def _pick_targets(repo: str) -> dict[str, str]:
    """Choose the largest indexed symbol and its file, so cases are worst case."""
    db = os.path.join(repo, ".repowise", "wiki.db")
    if not os.path.exists(db):
        raise SystemExit(f"no index at {db}; run 'repowise init' in that repo first")
    connection = sqlite3.connect(db)
    try:
        rows = list(
            connection.execute(
                "SELECT symbol_id, file_path FROM wiki_symbols "
                "ORDER BY end_line - start_line DESC LIMIT 2"
            )
        )
    finally:
        connection.close()
    if not rows:
        raise SystemExit(f"index at {db} has no symbols")
    return {
        "symbol": rows[0][0],
        "file": rows[0][1],
        "file2": rows[1][1] if len(rows) > 1 else rows[0][1],
        "query": "index the repository",
    }


def _skeleton(payload: Any) -> Any:
    """The nested key shape of a response, with every value discarded.

    A declared shed path that does not resolve against this is a silent no-op,
    which the size columns cannot reveal: the final guard brings the response
    under the ceiling either way.
    """
    if isinstance(payload, dict):
        # A map keyed by repository path is one shape under many names, and
        # those names differ per repo. Collapse it so the fixture describes the
        # shape rather than the checkout it was recorded against.
        if len(payload) > 1 and all("/" in key for key in payload):
            return {"<path>": _skeleton(next(iter(payload.values())))}
        return {key: _skeleton(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_skeleton(payload[0])] if payload else []
    return None


def _merge_skeleton(into: Any, new: Any) -> Any:
    """Union two shapes, so several cases together describe one tool."""
    if isinstance(into, dict) and isinstance(new, dict):
        for key, value in new.items():
            into[key] = _merge_skeleton(into.get(key), value) if key in into else value
        return into
    if isinstance(into, list) and isinstance(new, list):
        if new and not into:
            return new
        if into and new:
            into[0] = _merge_skeleton(into[0], new[0])
        return into
    return new if into is None else into


def _bind(kwargs: dict[str, Any], targets: dict[str, str]) -> dict[str, Any]:
    def sub(value: Any) -> Any:
        if isinstance(value, str):
            return value.format(**targets)
        if isinstance(value, list):
            return [sub(item) for item in value]
        return value

    return {key: sub(value) for key, value in kwargs.items()}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="path to an indexed repository")
    parser.add_argument("--json", help="also write the full rows here")
    parser.add_argument("--only", help="comma-separated tool names")
    parser.add_argument(
        "--skeletons",
        help="write each tool's nested key shape here, for the shed-path fixture",
    )
    options = parser.parse_args()

    os.environ.setdefault("REPOWISE_TELEMETRY_DISABLED", "1")

    from repowise.server.mcp_server import _TOOL_MODULES, _server, _state, tool_middleware
    from repowise.server.mcp_server._budget import (
        DEFAULT_RESPONSE_CHARS,
        EXPANDED_RESPONSE_CHARS,
    )

    _state._repo_path = options.repo
    only = set(options.only.split(",")) if options.only else None
    targets = _pick_targets(options.repo)

    handlers: dict[str, tuple[Any, Any]] = {}
    for name, module in _TOOL_MODULES.items():
        if only and name not in only:
            continue
        function = getattr(importlib.import_module(f"repowise.server.mcp_server.{module}"), name)
        handlers[name] = (function, tool_middleware(function))

    rows: list[dict[str, Any]] = []
    skeletons: dict[str, Any] = {}
    async with _server._lifespan(None):
        ready = getattr(_state, "_vector_store_ready", None)
        if ready is not None:
            # The vector arm loads in the background; measuring before it lands
            # would understate every search-backed response.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ready.wait(), timeout=60)

        for tool, case, raw_kwargs in CASES:
            if only and tool not in only:
                continue
            if tool not in handlers:
                continue
            kwargs = _bind(raw_kwargs, targets)
            row: dict[str, Any] = {"tool": tool, "case": case, "args": kwargs}
            for label, handler in (("raw", handlers[tool][0]), ("wrapped", handlers[tool][1])):
                try:
                    payload = await handler(**kwargs)
                except Exception as exc:
                    row[f"{label}_error"] = f"{type(exc).__name__}: {exc}"
                    row[f"{label}_trace"] = traceback.format_exc()[-1200:]
                    continue
                row[f"{label}_chars"] = _size(payload)
                if label == "raw" and isinstance(payload, dict):
                    if "error" in payload:
                        row["unexercised"] = payload["error"]
                    else:
                        skeletons[tool] = _merge_skeleton(
                            skeletons.get(tool), _skeleton(payload)
                        )
            rows.append(row)

    print(f"{'tool':<26} {'case':<20} {'raw':>9} {'wrapped':>9}  verdict")
    for row in rows:
        wrapped = row.get("wrapped_chars")
        if row.get("unexercised") or wrapped is None:
            verdict = "not measured"
        elif wrapped > EXPANDED_RESPONSE_CHARS:
            verdict = f"OVER expanded ({EXPANDED_RESPONSE_CHARS})"
        elif wrapped > DEFAULT_RESPONSE_CHARS:
            verdict = f"over default ({DEFAULT_RESPONSE_CHARS})"
        else:
            verdict = "within budget"
        print(
            f"{row['tool']:<26} {row['case']:<20} "
            f"{row.get('raw_chars', '-'):>9} {wrapped if wrapped is not None else '-':>9}  {verdict}"
        )

    if options.json:
        with open(options.json, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=2, default=str)
    if options.skeletons:
        with open(options.skeletons, "w", encoding="utf-8") as handle:
            json.dump(skeletons, handle, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
