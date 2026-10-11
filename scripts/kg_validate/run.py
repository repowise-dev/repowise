#!/usr/bin/env python3
"""KG validation harness — index the pinned matrix, check smells, diff baselines.

Usage (from the repo root):

    python scripts/kg_validate/run.py                  # check all matrix repos
    python scripts/kg_validate/run.py chi express      # subset
    python scripts/kg_validate/run.py --skip-index     # reuse existing exports
    python scripts/kg_validate/run.py --update-baselines
    python scripts/kg_validate/run.py --json           # machine-readable
    python scripts/kg_validate/run.py --modules-report /tmp/modules.md

Environment:
    KG_VALIDATE_DIR   clone/work dir (default /tmp/kg-validate)
    REPOWISE_PY       python used to run the indexer (default: this python)

Each repo is cloned at its pinned SHA from matrix.toml, indexed with
REPOWISE_KG_CURATION=1, and its exported knowledge-graph.json is checked by
kg_checks. The previous run's ``.repowise/`` output is wiped before indexing
— indexing output must never contaminate the next run's input.

Baselines live next to this script in ``baselines/<repo>.json`` and are
committed; the density_regression smell diffs against them.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
BASELINE_DIR = HERE / "baselines"
WORK_DIR = Path(os.environ.get("KG_VALIDATE_DIR", "/tmp/kg-validate"))
PY = os.environ.get("REPOWISE_PY", sys.executable)
PYTHONPATH = os.pathsep.join(
    str(REPO_ROOT / p) for p in ("packages/core/src", "packages/cli/src", "packages/server/src")
)

sys.path.insert(0, str(HERE))
from kg_checks import RepoReport, compute_stats, run_smells  # noqa: E402


def load_matrix() -> dict[str, dict]:
    with open(HERE / "matrix.toml", "rb") as fh:
        return tomllib.load(fh)


def import_support_map() -> dict[str, str]:
    sys.path.insert(0, str(REPO_ROOT / "packages/core/src"))
    from repowise.core.ingestion.languages.registry import REGISTRY

    return REGISTRY.import_support_map()


def ensure_clone(name: str, spec: dict) -> Path:
    dest = WORK_DIR / name
    source = spec["source"]
    if not (dest / ".git").exists():
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        if source.startswith(("http://", "https://", "git@")):
            src = source
        else:
            # Relative sources resolve against this script's dir first when
            # they name a committed bundle file (fixtures/*.bundle), else
            # against the repo root ("." = repowise itself). git clones
            # bundles like any other repo.
            local = HERE / source
            src = str(local if local.is_file() else REPO_ROOT / source)
        print(f"  cloning {name} from {src} …")
        subprocess.run(["git", "clone", "--quiet", src, str(dest)], check=True)
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != spec["sha"]:
        fetched = subprocess.run(
            ["git", "-C", str(dest), "checkout", "--quiet", spec["sha"]], capture_output=True
        )
        if fetched.returncode != 0:
            subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", "origin", spec["sha"]],
                           check=False)
            subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", spec["sha"]], check=True)
        print(f"  {name}: checked out pinned {spec['sha'][:10]}")
    return dest


def index_repo(dest: Path) -> None:
    # Wipe previous output: indexing must never read its own prior artifacts.
    shutil.rmtree(dest / ".repowise", ignore_errors=True)
    env = {**os.environ, "REPOWISE_KG_CURATION": "1", "PYTHONPATH": PYTHONPATH}
    code = (
        "from repowise.cli.main import cli; "
        f"cli(['init', {str(dest)!r}, '--index-only', '--yes'])"
    )
    res = subprocess.run([PY, "-c", code], env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"index failed for {dest.name}:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")


def check_repo(name: str, support: dict[str, str], *, skip_index: bool) -> RepoReport:
    matrix = load_matrix()
    spec = matrix[name]
    dest = ensure_clone(name, spec)
    if not skip_index:
        index_repo(dest)
    kg_path = dest / ".repowise" / "knowledge-graph.json"
    kg = json.loads(kg_path.read_text(encoding="utf-8"))

    stats = compute_stats(kg, support)
    baseline_path = BASELINE_DIR / f"{name}.json"
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
    )
    smells = run_smells(kg, stats, baseline)
    report = RepoReport(repo=name, stats=stats, smells=smells)
    # Keep tour paths in the report so baseline diffs show walk changes.
    report.stats["tour_paths"] = [s.get("target_path") for s in kg.get("tour", [])]
    report.stats["entry_points"] = (kg.get("project") or {}).get("entry_points", [])
    layer_names = {lyr.get("id"): lyr.get("name", "") for lyr in kg.get("layers", [])}
    report.modules_detail = [
        {
            "name": m.get("name", ""),
            "path": m.get("path", ""),
            "layer": layer_names.get(m.get("layerId"), m.get("layerId", "")),
            "size": len(m.get("nodeIds", [])),
        }
        for m in kg.get("modules", [])
    ]
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="*", help="subset of matrix repos")
    ap.add_argument("--skip-index", action="store_true")
    ap.add_argument("--update-baselines", action="store_true")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument(
        "--modules-report",
        metavar="PATH",
        help="write a per-repo curated-module inventory (markdown) for human review",
    )
    args = ap.parse_args()

    matrix = load_matrix()
    names = args.repos or list(matrix)
    unknown = [n for n in names if n not in matrix]
    if unknown:
        ap.error(f"not in matrix.toml: {unknown}")

    support = import_support_map()
    reports: list[RepoReport] = []
    for name in names:
        print(f"== {name} ==")
        try:
            report = check_repo(name, support, skip_index=args.skip_index)
        except Exception as exc:
            report = RepoReport(repo=name, stats={}, smells=[])
            report.smells.append(type("S", (), {})())  # placeholder replaced below
            from kg_checks import Smell

            report.smells = [Smell("FAIL", "harness_error", str(exc)[:500])]
        reports.append(report)
        dom = report.stats.get("dominant_language")
        for lang, b in (report.stats.get("by_language") or {}).items():
            star = "*" if lang == dom else " "
            print(
                f"  {star}{lang:<12} files={b['files']:<5} imports/file={b['edges_per_file']:<6}"
                f" resolution={b['resolution_rate']} orphans={b['orphan_ratio']:.0%}"
                f" [{b['import_support']}]"
            )
        for s in report.smells:
            print(f"  !! {s.severity} {s.code}: {s.message}")
        if not report.smells:
            print("  OK — no smells")
        if args.update_baselines and not any(
            s.code == "harness_error" for s in report.smells
        ):
            BASELINE_DIR.mkdir(exist_ok=True)
            (BASELINE_DIR / f"{name}.json").write_text(
                json.dumps(report.as_dict(), indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            print("  baseline updated")

    if args.as_json:
        print(json.dumps([r.as_dict() for r in reports], indent=1, sort_keys=True))

    if args.modules_report:
        lines = ["# Curated module inventory", ""]
        for r in reports:
            lines.append(f"## {r.repo}")
            if not r.modules_detail:
                lines.extend(["", "_no modules in artifact_", ""])
                continue
            lines.extend(["", "| Module | Path | Layer | Files |", "|---|---|---|---|"])
            for m in sorted(r.modules_detail, key=lambda m: (-m["size"], m["name"])):
                lines.append(
                    f"| {m['name']} | `{m['path'] or '(layer root)'}` | {m['layer']} | {m['size']} |"
                )
            lines.append("")
        Path(args.modules_report).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"modules report written to {args.modules_report}")

    failed = [r.repo for r in reports if r.failed]
    print(f"\n{len(reports) - len(failed)}/{len(reports)} clean" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
