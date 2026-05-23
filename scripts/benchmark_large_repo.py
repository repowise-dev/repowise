#!/usr/bin/env python3
"""Benchmark the repowise pipeline against a synthetic large repository.

Generates a throwaway git repo with a configurable number of source files
(default 5,000) wired together with cross-module imports, then runs the
indexing pipeline and records per-phase wall-clock to
``bench/results/<git-sha>.json`` (gitignored).

The 5k size exercises the Phase-2 scale work (tiered git indexing, ESSENTIAL
``--mode fast`` path, batched embedding, checkpointing). A 30k dry-run is a
later milestone.

Usage::

    python scripts/benchmark_large_repo.py                 # 5k files, fast mode
    python scripts/benchmark_large_repo.py --files 2000
    python scripts/benchmark_large_repo.py --mode standard --keep
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def generate_synthetic_repo(root: Path, n_files: int, n_commits: int) -> int:
    """Create *n_files* Python modules under *root* with cross-imports and a
    git history of *n_commits* commits. Returns the number of files written.

    Modules are grouped into packages of 100 and each imports a symbol from a
    lower-numbered module so the dependency graph has real edges (and PageRank
    has something to rank).
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "bench@repowise.dev")
    _git(root, "config", "user.name", "bench")

    files_per_pkg = 100
    written = 0
    for i in range(n_files):
        pkg = root / f"pkg_{i // files_per_pkg:03d}"
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").touch()
        mod = pkg / f"mod_{i:05d}.py"
        # Import from an earlier module to create a graph edge.
        import_line = ""
        if i > 0:
            dep = i - 1
            import_line = (
                f"from pkg_{dep // files_per_pkg:03d}.mod_{dep:05d} import value_{dep}\n"
            )
        mod.write_text(
            f'"""Synthetic module {i}."""\n'
            f"{import_line}\n"
            f"value_{i} = {i}\n\n\n"
            f"def compute_{i}(x: int) -> int:\n"
            f'    """Return x scaled by module ordinal {i}."""\n'
            f"    return x * value_{i}\n"
        )
        written += 1

    # Spread the files across n_commits commits so git indexing has history.
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat: initial synthetic tree")
    commits = max(1, n_commits - 1)
    step = max(1, n_files // commits)
    for c in range(commits):
        # Touch a slice of files to create churn + co-change-able commits.
        touched = []
        for i in range(c * step, min((c + 1) * step, n_files)):
            mod = root / f"pkg_{i // files_per_pkg:03d}" / f"mod_{i:05d}.py"
            with mod.open("a") as fh:
                fh.write(f"\n# touch {c}\n")
            touched.append(str(mod.relative_to(root)))
        if touched:
            _git(root, "add", *touched)
            _git(root, "commit", "-q", "-m", f"refactor: tweak batch {c}")
    return written


async def _run(repo: Path, mode_name: str) -> tuple[dict[str, float], float, object]:
    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    mode = OrchestratorMode(mode_name)
    recorder = PhaseTimingRecorder(None)
    start = time.monotonic()
    result = await run_pipeline(
        repo,
        generate_docs=False,
        mode=mode,
        progress=recorder,
    )
    total = time.monotonic() - start
    return recorder.timings, total, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=5000, help="Number of source files.")
    parser.add_argument("--commits", type=int, default=50, help="Number of git commits.")
    parser.add_argument(
        "--mode", choices=["fast", "standard"], default="fast", help="Pipeline mode."
    )
    parser.add_argument(
        "--keep", action="store_true", help="Keep the generated repo (print its path)."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "bench" / "results",
        help="Where to write the result JSON (gitignored).",
    )
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="repowise-bench-"))
    repo = tmp / "synthetic_repo"
    print(f"Generating {args.files} files across {args.commits} commits in {repo} ...")
    gen_start = time.monotonic()
    n = generate_synthetic_repo(repo, args.files, args.commits)
    gen_secs = time.monotonic() - gen_start
    print(f"Generated {n} files in {gen_secs:.1f}s. Running pipeline (mode={args.mode}) ...")

    timings, total, result = asyncio.run(_run(repo, args.mode))

    try:
        sha = _git(REPO_ROOT, "rev-parse", "--short", "HEAD")
    except Exception:
        sha = "unknown"

    record = {
        "git_sha": sha,
        "timestamp": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "files_requested": args.files,
        "files_generated": n,
        "commits": args.commits,
        "generation_seconds": round(gen_secs, 2),
        "pipeline_total_seconds": round(total, 2),
        "phase_timings": timings,
        "files_indexed": getattr(result, "file_count", None),
        "symbol_count": getattr(result, "symbol_count", None),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / f"{sha}.json"
    out.write_text(json.dumps(record, indent=2))
    print(f"\nWrote {out}")
    print(json.dumps(record, indent=2))

    if args.keep:
        print(f"\nKept synthetic repo at {repo}")
    else:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
