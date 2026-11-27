"""Fail CI when the default install grows or a startup path loads a heavy module.

Run it with the interpreter of a fresh virtualenv holding a default install
(no extras), so what it measures is what ``pip install repowise`` gives a user:

    uv export --frozen --no-dev --no-emit-workspace --no-hashes --package repowise -o pins.txt
    uv venv --python 3.12 /tmp/fp
    uv pip install --python /tmp/fp/bin/python -c pins.txt .
    /tmp/fp/bin/python scripts/check_footprint.py

Two checks, both against ``ci/footprint_budget.json``:

- Install size and distribution count, summed from every installed
  distribution's RECORD. Each may exceed its budget by the tolerance the file
  sets, no more.
- Startup paths. Each path runs in its own fresh interpreter (the hooks run
  their ``main`` on a sample payload, as Claude Code would start them) and must
  not leave any of its forbidden modules in ``sys.modules``.

A budget only rises by editing the budget file, in the pull request that
justifies the growth.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata as md
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from repowise.core.ci.github import append_step_summary, error, notice

ROOT = Path(__file__).resolve().parents[1]
BUDGET = ROOT / "ci" / "footprint_budget.json"
#: Below the budget by more than this share, a notice asks for a lower budget.
SLACK_NOTICE = 0.10

#: Runs in the measured interpreter: start one path, then list what it loaded.
_DRIVER = """
import importlib, io, json, sys
spec, out = json.loads(sys.argv[1]), sys.argv[2]
sys.argv = [spec["module"], *spec.get("argv", [])]
if "stdin" in spec:
    sys.stdin = io.StringIO(json.dumps(spec["stdin"]))
module = importlib.import_module(spec["module"])
if spec.get("call"):
    try:
        getattr(module, spec["call"])()
    except SystemExit:
        pass
with open(out, "w", encoding="utf-8") as fh:
    json.dump(sorted(sys.modules), fh)
"""


def measure_install() -> tuple[float, int, list[tuple[float, str]]]:
    """(MB on disk, distributions, largest ten) for this interpreter's environment."""
    sizes: dict[str, float] = {}
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in sizes:
            continue
        total = 0
        for file in dist.files or []:
            with contextlib.suppress(OSError):
                total += os.path.getsize(dist.locate_file(file))
        sizes[name.lower()] = total / 1e6
    largest = sorted(((mb, name) for name, mb in sizes.items()), reverse=True)[:10]
    return sum(sizes.values()), len(sizes), largest


def loaded_modules(path: dict, workdir: Path) -> tuple[set[str], float]:
    """Modules one startup path leaves loaded, and its wall seconds."""
    spec = dict(path)
    if isinstance(spec.get("stdin"), dict):
        spec["stdin"] = {"cwd": str(workdir), **spec["stdin"]}
    home = workdir / "home"
    home.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "REPOWISE_TELEMETRY_DISABLED": "1",
        "DO_NOT_TRACK": "1",
    }
    out = workdir / "modules.json"
    started = time.perf_counter()
    # -I: the installed package only, never a checkout on the path.
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _DRIVER, json.dumps(spec), str(out)],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    seconds = time.perf_counter() - started
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"{path['name']} did not start:\n{proc.stderr[-2000:]}")
    return set(json.loads(out.read_text(encoding="utf-8"))), seconds


def heavy_loaded(modules: set[str], forbidden: list[str]) -> list[str]:
    """Forbidden names present as a module or the parent of one."""
    return [
        name
        for name in forbidden
        if name in modules or any(m.startswith(name + ".") for m in modules)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--budget", type=Path, default=BUDGET)
    args = parser.parse_args()
    budget = json.loads(args.budget.read_text(encoding="utf-8"))
    try:
        budget_file = args.budget.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        budget_file = str(args.budget)
    failures: list[str] = []
    notices: list[str] = []
    rows: list[str] = []

    install = budget["install"]
    size_mb, dists, largest = measure_install()
    size_cap = install["size_mb"] * (1 + install["size_tolerance_pct"] / 100)
    dist_cap = install["distributions"] + install["distributions_tolerance"]
    for label, value, base, cap, unit, fmt in (
        ("Install size", size_mb, install["size_mb"], size_cap, " MB", "{:.1f}"),
        ("Distributions", dists, install["distributions"], dist_cap, "", "{}"),
    ):
        shown = fmt.format(value) + unit
        rows.append(f"| {label} | {shown} | {base}{unit} (up to {fmt.format(cap)}{unit}) |")
        if value > cap:
            failures.append(
                f"{label} grew to {shown}, {fmt.format(value - base)}{unit} over the budget "
                f"of {base}{unit} (the limit with tolerance is {fmt.format(cap)}{unit}). "
                f"Drop the new dependency, move it to an extra, or raise the budget in "
                f"{budget_file} in this pull request and say why in its description."
            )
        elif value < base * (1 - SLACK_NOTICE):
            notices.append(
                f"{label} is {shown}, well under the budget of {base}{unit}: lower it in "
                f"{budget_file} so later growth is caught."
            )

    with tempfile.TemporaryDirectory() as tmp:
        for index, path in enumerate(budget["startup_paths"]):
            workdir = Path(tmp) / str(index)
            workdir.mkdir()
            modules, seconds = loaded_modules(path, workdir)
            heavy = heavy_loaded(modules, path["forbidden"])
            status = ", ".join(heavy) if heavy else "none"
            rows.append(
                f"| {path['name']} | {len(modules)} modules, {seconds:.2f} s | "
                f"forbidden loaded: {status} |"
            )
            if heavy:
                failures.append(
                    f"{path['name']} ({path['module']}) now loads {', '.join(heavy)} at "
                    f"startup. Import it inside the function that needs it. "
                    f'`python -X importtime -c "import {path["module"]}"` shows which '
                    f"import pulls it in."
                )

    report = "\n".join(
        [
            "### Footprint",
            "",
            "| Check | Measured | Budget |",
            "|---|---|---|",
            *rows,
            "",
            "Largest distributions: " + ", ".join(f"{name} {mb:.1f} MB" for mb, name in largest),
        ]
    )
    print(report)
    append_step_summary(report)
    in_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    for message in notices:
        print(notice(message) if in_actions else f"note: {message}")
    for message in failures:
        print(error(message) if in_actions else f"FAIL: {message}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
