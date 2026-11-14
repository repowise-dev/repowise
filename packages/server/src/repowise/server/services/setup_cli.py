"""Run the CLI commands the Setup page reads and writes through.

The server never imports ``repowise.cli`` (see ``routers/platform.py``): agent
detection, hook state and doctor live there, so each answer comes from the
command a person would type, run in a child process with a timeout. Every
function here is blocking; callers run it in a thread.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Doctor starts the MCP server once to check it answers; leave room for that.
_READ_TIMEOUT_SECONDS = 90.0
#: Writes touch a few config files; coverage add parses a report.
_WRITE_TIMEOUT_SECONDS = 180.0

#: Kept off the child's output: a wide fixed width stops rich from wrapping
#: paths, and UTF-8 keeps its glyphs from failing on a Windows code page.
_CHILD_ENV = {"COLUMNS": "200", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}


@dataclass(frozen=True)
class CliRun:
    """What one command did. ``exit_code`` is ``None`` when it could not run."""

    exit_code: int | None
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def json(self) -> Any:
        """Parsed stdout, or ``None`` when it is not JSON."""
        try:
            return json.loads(self.stdout)
        except ValueError:
            return None

    def lines(self, limit: int = 40) -> list[str]:
        """The command's output, both streams, trimmed for a response body."""
        text = "\n".join(part for part in (self.stdout, self.stderr) if part.strip())
        return [line.rstrip() for line in text.splitlines() if line.strip()][-limit:]


def run_cli(args: list[str], *, cwd: Path, timeout: float) -> CliRun:
    """``repowise <args>`` in *cwd*. Never raises."""
    argv = [sys.executable, "-m", "repowise.cli.main", *args]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, **_CHILD_ENV},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("repowise %s could not run: %s", " ".join(args[:2]), exc)
        return CliRun(None, "", str(exc))
    return CliRun(proc.returncode, proc.stdout or "", proc.stderr or "")


# -- reads -------------------------------------------------------------------


def doctor_json(repo_path: Path, *, quick: bool = False) -> dict | None:
    """``repowise doctor --format json`` for this repo; it exits 1 on a failed check.

    *quick* adds ``--quick``, which skips starting the MCP server and
    reconciling the stores: seconds instead of tens of seconds.
    """
    args = ["doctor", str(repo_path), "--no-workspace", "--format", "json"]
    if quick:
        args.append("--quick")
    run = run_cli(args, cwd=repo_path, timeout=_READ_TIMEOUT_SECONDS)
    payload = run.json()
    return payload if isinstance(payload, dict) else None


def agents_json(repo_path: Path) -> dict | None:
    """``repowise agents --format json``, which lists against the current directory."""
    run = run_cli(["agents", "--format", "json"], cwd=repo_path, timeout=_READ_TIMEOUT_SECONDS)
    payload = run.json()
    return payload if isinstance(payload, dict) else None


# -- writes ------------------------------------------------------------------


def agents_write(repo_path: Path, target: str, *, remove: bool) -> CliRun:
    """``repowise agents add|remove PATH --target=<id> --format json``."""
    verb = "remove" if remove else "add"
    args = ["agents", verb, str(repo_path), f"--target={target}", "--format", "json"]
    if not remove:
        args.append("--yes")
    return run_cli(args, cwd=repo_path, timeout=_WRITE_TIMEOUT_SECONDS)


#: Hook name -> the command group it maps to.
HOOK_COMMANDS: dict[str, list[str]] = {
    "post-commit": ["hook"],
    "rewrite": ["hook", "rewrite"],
}


def hook_write(repo_path: Path, name: str, *, enabled: bool) -> CliRun:
    """``repowise hook [rewrite] install|uninstall PATH --no-workspace``."""
    verb = "install" if enabled else "uninstall"
    args = [*HOOK_COMMANDS[name], verb, str(repo_path), "--no-workspace"]
    return run_cli(args, cwd=repo_path, timeout=_WRITE_TIMEOUT_SECONDS)


def coverage_add(repo_path: Path, report: Path) -> CliRun:
    """``repowise coverage add REPORT --path PATH``; *report* is absolute and inside the repo."""
    args = ["coverage", "add", str(report), "--path", str(repo_path)]
    return run_cli(args, cwd=repo_path, timeout=_WRITE_TIMEOUT_SECONDS)
