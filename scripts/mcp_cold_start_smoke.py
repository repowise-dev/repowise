"""Cold first-call latency of the MCP tools, one fresh server process per call.

Each case starts ``repowise mcp <repo> --no-workspace`` over stdio, then times
the server start (spawn to handshake) and the first tool call, which is the
call an agent waits on at the start of a session. Writes a markdown report and
exits 1 when a call errors or goes over budget. The weekly ``MCP cold start``
workflow runs it against this repository; it never gates a pull request.

    python scripts/mcp_cold_start_smoke.py --repo . --report smoke.md
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

#: First calls an agent makes. Targets name files in this repository.
CASES: list[tuple[str, dict[str, Any]]] = [
    ("get_overview", {}),
    ("get_answer", {"question": "How does the MCP server shape a tool response?"}),
    ("search_codebase", {"query": "tool_middleware"}),
    ("get_context", {"targets": ["packages/server/src/repowise/server/mcp_server/_meta.py"]}),
    ("get_risk", {"targets": ["packages/server/src/repowise/server/mcp_server/_meta.py"]}),
    ("get_change_risk", {"revspec": "HEAD~3..HEAD"}),
    ("get_why", {"query": "why are the tool modules imported lazily"}),
    ("get_health", {}),
    ("get_dead_code", {}),
]


async def _time_case(repo: str, tool: str, args: dict[str, Any]) -> tuple[float, float, str]:
    """(start seconds, call seconds, error text or "") for one fresh server."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "repowise.cli.main", "mcp", repo, "--no-workspace"],
        # The stdio client passes only a minimal environment by default.
        env={**os.environ, "DO_NOT_TRACK": "1"},
    )
    started = time.perf_counter()
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        ready = time.perf_counter()
        result = await session.call_tool(tool, args)
        done = time.perf_counter()
    error = ""
    if result.isError:
        error = " ".join(getattr(c, "text", "") for c in result.content)[:200]
    return ready - started, done - ready, error


def _row(tool: str, timing: tuple[float, float, str], args: argparse.Namespace) -> str:
    start, call, error = timing
    over = []
    if start > args.start_budget_s:
        over.append(f"start over {args.start_budget_s:g} s")
    if call > args.call_budget_s:
        over.append(f"call over {args.call_budget_s:g} s")
    if error:
        over.append(f"error: {error}")
    status = "; ".join(over) or "ok"
    return f"| `{tool}` | {start:.1f} | {call:.1f} | {status} |"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="An indexed repository")
    parser.add_argument("--report", type=Path, help="Write the markdown report here too")
    parser.add_argument("--start-budget-s", type=float, default=20.0)
    parser.add_argument("--call-budget-s", type=float, default=60.0)
    args = parser.parse_args()
    repo = str(Path(args.repo).resolve())

    lines = [
        "| Tool | Start (s) | First call (s) | Status |",
        "|------|-----------|----------------|--------|",
    ]
    for tool, call_args in CASES:
        try:
            timing = asyncio.run(_time_case(repo, tool, call_args))
        except Exception as exc:  # A server that cannot start is a breach too.
            timing = (0.0, 0.0, f"{type(exc).__name__}: {exc}"[:200])
        lines.append(_row(tool, timing, args))
    breached = any(not line.endswith("| ok |") for line in lines[2:])
    heading = (
        f"MCP cold first-call smoke: budgets {args.start_budget_s:g} s start, "
        f"{args.call_budget_s:g} s first call, one fresh server per tool."
    )
    report = "\n".join([heading, "", *lines]) + "\n"
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    return 1 if breached else 0


if __name__ == "__main__":
    raise SystemExit(main())
