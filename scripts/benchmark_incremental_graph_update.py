#!/usr/bin/env python3
"""Compare full and incremental updates on an indexed synthetic repository.

The benchmark creates and fast-indexes a synthetic Python repository, then
runs the same body-only edit through the full and opt-in incremental update
paths on isolated copies. It records end-to-end and per-phase timings, plus
hashes of persisted graph nodes and edges to verify convergence.

Usage::

    python scripts/benchmark_incremental_graph_update.py
    python scripts/benchmark_incremental_graph_update.py --files 2000 --commits 20
    python scripts/benchmark_incremental_graph_update.py --files 1000 --keep

The benchmark uses no model provider or LLM. Its temporary home keeps init
from changing the user's global editor or MCP configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmark_large_repo import generate_synthetic_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = (
    REPO_ROOT / "packages" / "core" / "src",
    REPO_ROOT / "packages" / "cli" / "src",
    REPO_ROOT / "packages" / "server" / "src",
)
sys.path[:0] = [str(path) for path in SOURCE_DIRS]

EDIT_PATH = Path("pkg_000/mod_00000.py")
EDIT_BEFORE = b"return x * value_0"
EDIT_AFTER = b"return x * value_0 + 0"


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path
) -> tuple[float, str]:
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    elapsed = time.monotonic() - started
    output = log_path.read_text(encoding="utf-8", errors="replace")
    if result.returncode:
        tail = "".join(output.splitlines(keepends=True)[-40:])
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"Log: {log_path}\n{tail}"
        )
    return elapsed, output


def _rebind_copy(source: Path, destination: Path, env: dict[str, str]) -> None:
    """Rebind the copied SQLite index and sealed graph snapshot to its new path."""
    os.environ["REPOWISE_CACHE_KEY_PATH"] = env["REPOWISE_CACHE_KEY_PATH"]
    from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

    db_path = destination / ".repowise" / "wiki.db"
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT id FROM repositories WHERE local_path = ?", (str(source.resolve()),)
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                f"Expected one repository row for {source.resolve()}, found {len(rows)}"
            )
        db.execute(
            "UPDATE repositories SET local_path = ? WHERE id = ?",
            (str(destination.resolve()), rows[0][0]),
        )

    seed_path = destination / ".repowise" / "graph_seed_edges.pkl"
    seed = load_sealed_pickle(seed_path, domain="graph_seed_edges.pkl")
    if seed.get("repo_path") != str(source.resolve()):
        raise RuntimeError(f"Unexpected graph seed path in {seed_path}")
    seed["repo_path"] = str(destination.resolve())
    dump_sealed_pickle(seed_path, seed, domain="graph_seed_edges.pkl")

    from repowise.core.pipeline.incremental.graph_seed import load_seed_edges

    if asyncio_run(load_seed_edges(destination)) is None:
        raise RuntimeError(f"Copied graph seed was not accepted for {destination}")


def asyncio_run(awaitable: Any) -> Any:
    """Run the small async cache validation without making the whole script async."""
    import asyncio

    return asyncio.run(awaitable)


def _edit_copy(destination: Path) -> None:
    path = destination / EDIT_PATH
    content = path.read_bytes()
    if content.count(EDIT_BEFORE) != 1:
        raise RuntimeError(f"Expected one body-only edit site in {path}")
    path.write_bytes(content.replace(EDIT_BEFORE, EDIT_AFTER, 1))


def _graph_snapshot(repo: Path) -> dict[str, dict[str, Any]]:
    db_path = repo / ".repowise" / "wiki.db"
    with sqlite3.connect(db_path) as db:
        repo_id = db.execute(
            "SELECT id FROM repositories WHERE local_path = ?", (str(repo.resolve()),)
        ).fetchone()
        if repo_id is None:
            raise RuntimeError(f"Repository row not found for {repo}")

        snapshots = {}
        for table in ("graph_nodes", "graph_edges"):
            columns = [
                row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            ignored = {"id", "created_at"}
            selected = [column for column in columns if column not in ignored]
            rows = db.execute(
                f"SELECT {', '.join(selected)} FROM {table} WHERE repository_id = ?",
                (repo_id[0],),
            ).fetchall()
            canonical = sorted(
                json.dumps(row, separators=(",", ":"), ensure_ascii=True) for row in rows
            )
            digest = hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()
            snapshots[table] = {"rows": len(rows), "sha256": digest}
        return snapshots


def _timings(repo: Path) -> dict[str, float]:
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    return state.get("phase_timings", {})


def _make_env(temp_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = [str(path) for path in SOURCE_DIRS]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    isolated_home = temp_root / "home"
    env["HOME"] = str(isolated_home)
    env["XDG_CONFIG_HOME"] = str(isolated_home / ".config")
    env["REPOWISE_CACHE_KEY_PATH"] = str(
        isolated_home / ".config" / "repowise" / "cache_hmac_key"
    )
    env["REPOWISE_NO_COST_TRACKING"] = "1"
    isolated_home.mkdir(parents=True)
    return env


def _benchmark(args: argparse.Namespace, temp_root: Path) -> dict[str, Any]:
    executable = shutil.which("repowise")
    if executable is None:
        raise RuntimeError("repowise CLI was not found on PATH")

    env = _make_env(temp_root)
    base = temp_root / "base"
    base.mkdir()
    started = time.monotonic()
    generate_synthetic_repo(base, args.files, args.commits)
    generation_seconds = time.monotonic() - started

    init_seconds, _ = _run(
        [
            executable,
            "init",
            "--mode",
            "fast",
            "--yes",
            "--no-agents",
            "--no-codex",
            "--no-claude-md",
            "--no-distill-hook",
        ],
        cwd=base,
        env=env,
        log_path=temp_root / "init.log",
    )

    copies: dict[str, Path] = {}
    for mode in ("full", "patch"):
        destination = temp_root / mode
        shutil.copytree(base, destination)
        _rebind_copy(base, destination, env)
        _edit_copy(destination)
        copies[mode] = destination

    updates: dict[str, dict[str, Any]] = {}
    for mode in ("full", "patch"):
        update_env = env.copy()
        update_env.pop("REPOWISE_INCREMENTAL_GRAPH", None)
        if mode == "patch":
            update_env["REPOWISE_INCREMENTAL_GRAPH"] = "1"
        elapsed, output = _run(
            [
                executable,
                "update",
                "--index-only",
                "--working-tree",
                "--no-workspace",
                "--progress",
                "json",
                "--verbose",
            ],
            cwd=copies[mode],
            env=update_env,
            log_path=temp_root / f"{mode}-update.log",
        )
        patched = "Patching the graph:" in output
        if mode == "patch" and not patched:
            raise RuntimeError(
                "The incremental run did not apply a graph patch. "
                f"Inspect {temp_root / 'patch-update.log'}"
            )
        updates[mode] = {
            "elapsed_seconds": round(elapsed, 2),
            "phase_timings": _timings(copies[mode]),
            "graph": _graph_snapshot(copies[mode]),
            "patch_applied": patched,
        }

    full = updates["full"]
    patch = updates["patch"]
    graphs_equal = full["graph"] == patch["graph"]
    full_seconds = full["elapsed_seconds"]
    patch_seconds = patch["elapsed_seconds"]
    report = {
        "git_sha": _git_sha(),
        "timestamp": datetime.now(UTC).isoformat(),
        "files_requested": args.files,
        "commits": args.commits,
        "generation_seconds": round(generation_seconds, 2),
        "initial_index_seconds": round(init_seconds, 2),
        "changed_file": EDIT_PATH.as_posix(),
        "edit_class": "function-body-only",
        "updates": updates,
        "graphs_equal": graphs_equal,
        "improvement": {
            "seconds": round(full_seconds - patch_seconds, 2),
            "percent": (
                round(100 * (full_seconds - patch_seconds) / full_seconds, 1)
                if full_seconds
                else 0.0
            ),
        },
    }
    return report


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=5000, help="Synthetic source files.")
    parser.add_argument("--commits", type=int, default=50, help="Synthetic git commits.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "bench" / "results",
        help="Where to write the result JSON (gitignored).",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the benchmark worktree.")
    args = parser.parse_args(argv)
    if args.files < 2 or args.commits < 1:
        parser.error("--files must be at least 2 and --commits at least 1")

    temp_root = Path(tempfile.mkdtemp(prefix="repowise-incremental-bench-"))
    try:
        report = _benchmark(args, temp_root)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = args.output_dir / f"incremental-{report['git_sha']}-{args.files}.json"
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        print(f"\nWrote {output_path}")
        if args.keep:
            print(f"Kept benchmark worktree at {temp_root}")
        return 0 if report["graphs_equal"] else 1
    finally:
        if not args.keep:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
