"""Fail CI when the default install grows or a startup path loads a heavy module.

Run it with the interpreter of a fresh virtualenv holding a default install
(no extras), so what it measures is what ``pip install repowise`` gives a user:

    uv export --locked --no-dev --no-emit-workspace --no-hashes --package repowise -o pins.txt
    uv venv --python 3.12 /tmp/fp
    uv pip install --python /tmp/fp/bin/python -c pins.txt .
    /tmp/fp/bin/python scripts/check_footprint.py

Two checks, both against ``ci/footprint_budget.json``:

- Install size and distribution count, summed from every installed
  distribution's RECORD. Each may exceed its budget by the tolerance the file
  sets, no more.
- Startup paths. Each path runs in its own fresh interpreter inside a small
  indexed repository (the hooks run their ``main`` on a sample payload, as
  Claude Code would start them) and must not leave any of its forbidden
  modules in ``sys.modules``.

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

#: Runs in the measured interpreter: start one path, then record what it loaded
#: and what it printed. A hook exits 0 by design; any other exit is a failure.
_DRIVER = """
import importlib, io, json, sys
spec, out = json.loads(sys.argv[1]), sys.argv[2]
sys.argv = [spec["module"], *spec.get("argv", [])]
if "stdin" in spec:
    sys.stdin = io.StringIO(json.dumps(spec["stdin"]))
stdout, sys.stdout = sys.stdout, io.StringIO()
module = importlib.import_module(spec["module"])
if spec.get("call"):
    try:
        getattr(module, spec["call"])()
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
printed, sys.stdout = sys.stdout.getvalue(), stdout
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"modules": sorted(sys.modules), "stdout": printed}, fh)
"""

#: The repository the paths run in, indexed so the hooks reach their index-backed code.
_FIXTURE = {
    "app.py": "def add(a, b):\n    return a + b\n",
    "test_app.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}


def measure_install() -> tuple[float, int, list[tuple[float, str]]]:
    """(MB on disk, distributions, largest ten) for this interpreter's environment."""
    sizes: dict[str, float] = {}
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in sizes:
            continue
        if dist.files is None:
            raise RuntimeError(f"{name} has no RECORD, so its size cannot be measured")
        total = 0
        for file in dist.files:
            # A RECORD may list a file the installer did not write (a .pyc).
            with contextlib.suppress(OSError):
                total += os.path.getsize(dist.locate_file(file))
        sizes[name.lower()] = total / 1e6
    largest = sorted(((mb, name) for name, mb in sizes.items()), reverse=True)[:10]
    return sum(sizes.values()), len(sizes), largest


def _env(home: Path) -> dict[str, str]:
    """A clean user: empty home, no telemetry, this install's ``repowise`` on PATH."""
    return {
        **os.environ,
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")]),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "REPOWISE_TELEMETRY_DISABLED": "1",
        "DO_NOT_TRACK": "1",
    }


def indexed_repo(tmp: Path) -> Path:
    """A two-file git repository with a keyless index, as a user's would have."""
    repo = tmp / "repo"
    repo.mkdir()
    for name, text in _FIXTURE.items():
        (repo / name).write_text(text, encoding="utf-8")
    git = ["git", "-c", "user.name=ci", "-c", "user.email=ci@example.com"]
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run([*git, *cmd], cwd=repo, check=True)
    init = [sys.executable, "-m", "repowise.cli.main", "init", ".", "--yes", "--no-prose"]
    init += ["--no-editor-setup", "--no-hook", "--no-workspace"]
    proc = subprocess.run(
        init, cwd=repo, env=_env(tmp / "home"), capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        raise RuntimeError(f"repowise init on the sample repository failed:\n{proc.stderr[-2000:]}")
    return repo


def run_path(path: dict, repo: Path, home: Path) -> tuple[set[str], str, float]:
    """Modules one startup path leaves loaded, what it printed, and its wall seconds."""
    spec = dict(path)
    if "stdin" in spec:
        payload = json.dumps(spec["stdin"]).replace("{repo}", json.dumps(str(repo))[1:-1])
        spec["stdin"] = {"cwd": str(repo), **json.loads(payload)}
    out = home.parent / "result.json"
    started = time.perf_counter()
    # -I: the installed package only, never a checkout on the path.
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _DRIVER, json.dumps(spec), str(out)],
        cwd=repo,
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=300,
    )
    seconds = time.perf_counter() - started
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"{path['name']} failed:\n{proc.stderr[-2000:]}")
    result = json.loads(out.read_text(encoding="utf-8"))
    out.unlink()
    return set(result["modules"]), result["stdout"], seconds


def heavy_loaded(modules: set[str], forbidden: list[str]) -> list[str]:
    """Forbidden names present as a module or the parent of one."""
    return [
        name
        for name in forbidden
        if name in modules or any(m.startswith(name + ".") for m in modules)
    ]


def check_install(
    install: dict, budget_file: str, rows: list[str], failures: list[str], notices: list[str]
) -> list[tuple[float, str]]:
    """Compare install size and distribution count with their budgets; the largest ten."""
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
    return largest


def check_startup_paths(paths: list[dict], rows: list[str], failures: list[str]) -> None:
    """Start each path in a fresh interpreter and flag the forbidden modules it loads."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            repo = indexed_repo(Path(tmp))
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            rows.append("| Startup paths | not measured | the sample repository did not index |")
            failures.append(f"The sample repository could not be set up: {exc}")
            return
        home = Path(tmp) / "home"
        for path in paths:
            try:
                modules, printed, seconds = run_path(path, repo, home)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                rows.append(f"| {path['name']} | did not finish | see the error below |")
                failures.append(str(exc))
                continue
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
            expected = path.get("expect_stdout")
            if expected and expected not in printed:
                failures.append(
                    f"{path['name']} no longer prints {expected!r} for its sample payload, "
                    f"so this check stops short of the code it covers. Update the payload "
                    f"in the budget file."
                )


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
    largest = check_install(budget["install"], budget_file, rows, failures, notices)
    check_startup_paths(budget["startup_paths"], rows, failures)

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
