"""Measure MCP server startup and query latency over stdio.

Spawns ``repowise mcp <repo>`` as a subprocess, speaks newline-delimited
JSON-RPC to it (FastMCP's stdio transport is NDJSON, not LSP framing), and
times four stages of a session:

    spawn -> initialize      process start and server lifespan
    initialize -> tools/list tool surface advertisement
    tools/list -> search     first search_codebase, cold
    search -> search         second search_codebase, warm, different query

Each run is a fresh process, so the first three stages are one-time startup
cost and the fourth is what a resident session pays per query.

Not wired into CI: it spawns a real interpreter against a real index, so
numbers are only comparable within one machine.

``cli/commands/doctor_cmd/mcp_smoke.py`` speaks the same protocol to decide
whether the server is alive; it records why the SDK's stdio client is not used
here. This one times a live session instead, so it stays separate.

Usage:
    python bench/mcp_latency.py <repo>            # table
    python bench/mcp_latency.py <repo> --json     # machine-readable
    python bench/mcp_latency.py <repo> -n 10
"""

from __future__ import annotations

import argparse
import contextlib
import json
import queue
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

#: Stage key -> table label, in report order.
STAGES: dict[str, str] = {
    "spawn_to_init": "spawn -> initialize",
    "init_to_tools": "initialize -> tools/list",
    "tools_to_cold_search": "tools/list -> cold search",
    "cold_to_warm_search": "cold search -> warm search",
    "spawn_to_cold_search": "spawn -> cold search (total)",
}

COLD_QUERY = "vector store"
WARM_QUERY = "workspace configuration loading"


class ServerError(RuntimeError):
    """The server died, or did not answer within the timeout."""


class _Session:
    """One spawned server, with its pipes drained off the main thread.

    Both pipes need a reader: a full stderr buffer blocks the child mid-write,
    which would show up as latency rather than as the deadlock it is.
    """

    def __init__(self, command: list[str], cwd: Path, timeout: float) -> None:
        self._timeout = timeout
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []
        self.started = time.perf_counter()
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
        )
        self._pump(self.proc.stdout, self._lines.put, done=lambda: self._lines.put(None))
        self._pump(self.proc.stderr, self._stderr.append)

    def _pump(self, stream: Any, sink: Any, done: Any = None) -> None:
        def run() -> None:
            for raw in iter(stream.readline, b""):
                sink(raw.decode("utf-8", errors="replace"))
            if done is not None:
                done()

        threading.Thread(target=run, daemon=True).start()

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request and return the response, ignoring unrelated traffic."""
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.perf_counter() + self._timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise ServerError(f"{method} timed out after {self._timeout:.0f}s{self._trailer()}")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise ServerError(f"server exited during {method}{self._trailer()}")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # a stray print on stdout is not our response
            # Notifications and server-initiated requests carry no matching id.
            if message.get("id") == request_id:
                return message

    def _trailer(self) -> str:
        tail = "".join(self._stderr[-20:]).strip()
        return f"\nserver stderr:\n{tail}" if tail else ""

    def close(self) -> None:
        # Runs from a ``finally``, often because the server already died, so
        # nothing here may raise over the error being reported.
        self._shut(self.proc.stdin)
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        # Only once the child is gone: the pump threads read these until EOF.
        self._shut(self.proc.stdout)
        self._shut(self.proc.stderr)

    @staticmethod
    def _shut(pipe: Any) -> None:
        if pipe is None:
            return
        with contextlib.suppress(OSError):
            pipe.close()


def _search(session: _Session, request_id: int, query: str) -> dict[str, Any]:
    return session.request(
        request_id, "tools/call", {"name": "search_codebase", "arguments": {"query": query}}
    )


def run_once(command: list[str], repo: Path, timeout: float) -> dict[str, float]:
    session = _Session(command, cwd=repo, timeout=timeout)
    try:
        session.request(
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "repowise-bench", "version": "1"},
            },
        )
        after_init = time.perf_counter()
        session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        session.request(2, "tools/list", {})
        after_tools = time.perf_counter()

        cold = _search(session, 3, COLD_QUERY)
        after_cold = time.perf_counter()

        warm = _search(session, 4, WARM_QUERY)
        after_warm = time.perf_counter()

        for label, response in (("cold", cold), ("warm", warm)):
            # A tool failure comes back as a *successful* response carrying
            # isError, and returns fast enough to look like a good timing.
            if "error" in response or response.get("result", {}).get("isError"):
                raise ServerError(f"{label} search failed: {json.dumps(response)[:400]}")

        return {
            "spawn_to_init": after_init - session.started,
            "init_to_tools": after_tools - after_init,
            "tools_to_cold_search": after_cold - after_tools,
            "cold_to_warm_search": after_warm - after_cold,
            "spawn_to_cold_search": after_cold - session.started,
        }
    finally:
        session.close()


def summarize(runs: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for stage in STAGES:
        values = [run[stage] * 1000 for run in runs]
        summary[stage] = {
            "median_ms": statistics.median(values),
            "min_ms": min(values),
            "max_ms": max(values),
        }
    return summary


def print_table(summary: dict[str, dict[str, float]], runs: int) -> None:
    width = max(len(label) for label in STAGES.values())
    print(f"\nMCP latency, median of {runs} run(s)\n")
    print(f"| {'Stage':<{width}} | Median | Min | Max |")
    print(f"|{'-' * (width + 2)}|---|---|---|")
    for stage, label in STAGES.items():
        row = summary[stage]
        print(
            f"| {label:<{width}} | {row['median_ms']:.0f} ms "
            f"| {row['min_ms']:.0f} ms | {row['max_ms']:.0f} ms |"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", nargs="?", default=".", help="repo to serve (default: cwd)")
    parser.add_argument("-n", "--runs", type=int, default=5, help="runs to time (default: 5)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="per-response timeout in seconds"
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        parser.error(f"not a directory: {repo}")
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    command = [sys.executable, "-m", "repowise.cli.main", "mcp", str(repo)]

    runs: list[dict[str, float]] = []
    for index in range(args.runs):
        result = run_once(command, repo, args.timeout)
        runs.append(result)
        if not args.json:
            stages = " ".join(f"{key}={result[key] * 1000:.0f}ms" for key in STAGES)
            print(f"run {index + 1}/{args.runs}: {stages}")

    summary = summarize(runs)
    if args.json:
        json.dump(
            {
                "repo": str(repo),
                "command": command,
                "runs": args.runs,
                "stages": summary,
                "raw_ms": [{k: v * 1000 for k, v in run.items()} for run in runs],
            },
            sys.stdout,
            indent=2,
        )
        print()
    else:
        print_table(summary, args.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
