"""The CI job ``evidence setup --write`` adds: each unit's tests with coverage and JUnit XML, kept as artifacts.

One job per unit with a test command (:mod:`repowise.core.test_runners.stacks`),
for GitHub Actions or GitLab CI. Each job runs the unit's coverage command
(with its JUnit XML flag when the runner takes one) and keeps the coverage
report and the test results as the run's artifacts, so every push to the
default branch leaves evidence ``coverage add`` and ``evidence add`` read,
and the ``coverage`` gate of the Repowise action or GitLab template can take
the report from the job. Nothing here reads or writes the checkout.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..test_runners.packages import quote_arg
from ..test_runners.stacks import Stack

#: The CI systems a snippet is written for, the first the default.
CI_KINDS = ("github", "gitlab")
#: Where each snippet lands, repo-relative.
CI_PATHS = {
    "github": ".github/workflows/repowise-evidence.yml",
    "gitlab": ".gitlab/repowise-evidence.yml",
}

# A setup input naming a file relative to the unit (``go-version-file: go.mod``).
_FILE_INPUT = re.compile(r"^(\s+[\w-]+-file:\s+)(\S+)(.*)$")
# setup-node's package-manager cache looks for the lockfile at the repository root.
_NODE_CACHE = re.compile(r"^\s+cache:\s+(npm|pnpm|yarn)\s*$")

# ceiling: one image per toolchain, the current major; a unit pinned to another
# version edits the image line. Package managers first, then the language.
_GITLAB_IMAGES = {
    "uv": "ghcr.io/astral-sh/uv:python3.12-bookworm",
    "bun": "oven/bun:1",
    "maven": "maven:3-eclipse-temurin-21",
    "gradle": "gradle:jdk21",
    "python": "python:3.12",
    "node": "node:lts",
    "go": "golang:1",
    "rust": "rust:1",
    "dotnet": "mcr.microsoft.com/dotnet/sdk:8.0",
    "ruby": "ruby:3.3",
    "php": "composer:2",
}
#: The line that installs a package manager the image lacks.
_GITLAB_TOOLS = {
    "poetry": "pip install poetry",
    "pdm": "pip install pdm",
    "hatch": "pip install hatch",
    # setup-ruby installs the bundle on GitHub; the image does not.
    "bundler": "bundle install",
    "pnpm": "corepack enable",
    "yarn": "corepack enable",
}


def detect_ci(repo_root: str | Path) -> str:
    """``gitlab`` for a checkout with a ``.gitlab-ci.yml`` and no GitHub workflows, else ``github``."""
    root = Path(repo_root)
    gitlab = (root / ".gitlab-ci.yml").is_file()
    return "gitlab" if gitlab and not (root / ".github" / "workflows").is_dir() else "github"


def _joined(root: str, rel: str) -> str:
    return posixpath.join(root, rel) if root else rel


def _with_junit(command: str, stack: Stack) -> tuple[str, str | None]:
    """*command* writing JUnit XML too, and the comment to show when the flag cannot be added.

    The flag goes at the end, which a command chaining a second step with
    ``&&`` (a c8 report) would hand to that step instead. The runner's
    environment for the report goes first; CI runs the line in sh or bash.
    """
    if not stack.junit_flag:
        return command, None
    env = stack.junit_vars()
    if "&&" in command:
        with_env = f" with {env} in its environment" if env else ""
        return command, f"# add {stack.junit_flag} to the test command{with_env} for test timings"
    return f"{env + ' ' if env else ''}{command} {stack.junit_flag}", None


def _junit_path(stack: Stack) -> str | None:
    """The JUnit XML the unit's run writes, repo-relative; a directory as its XML files."""
    if not stack.junit_report:
        return None
    path = _joined(stack.root, stack.junit_report)
    return path if path.endswith(".xml") else f"{path}/*.xml"


def _kept(stack: Stack) -> list[str]:
    """Every path the job keeps: the coverage reports, then the test results."""
    junit = _junit_path(stack)
    return [*stack.report_paths(), *(_joined(stack.root, r) for r in stack.push_reports.split()),
            *([junit] if junit else [])]


def _slug(stack: Stack) -> str:
    where = re.sub(r"[^a-z0-9]+", "-", stack.root.lower()).strip("-") or "root"
    return f"{where}-{stack.language}"


def _job_names(stacks: Sequence[Stack], prefix: str) -> list[str]:
    """*prefix* alone for one unit, else one name per unit."""
    if len(stacks) == 1:
        return [prefix]
    names: list[str] = []
    for stack in stacks:
        name = f"{prefix}-{_slug(stack)}"
        names.append(name if name not in names else f"{name}-{len(names) + 1}")
    return names


def _rebased(setup: Sequence[str], root: str) -> list[str]:
    """GitHub setup steps for a unit below the root: file inputs point into the unit.

    setup-node's cache is dropped, since it reads the lockfile at the root.
    ceiling: other root-relative inputs (pnpm's ``packageManager``, setup-ruby's
    bundler cache) are not rebased; the job's comment asks for a review.
    """
    if not root:
        return list(setup)
    out: list[str] = []
    for line in setup:
        if _NODE_CACHE.match(line):
            continue
        m = _FILE_INPUT.match(line)
        out.append(f"{m.group(1)}{_joined(root, m.group(2))}{m.group(3)}" if m else line)
    return out


def _indent(lines: Sequence[str], n: int) -> list[str]:
    return [(" " * n + line) if line.strip() else "" for line in lines]


def _github_job(name: str, stack: Stack) -> list[str]:
    command, missing = _with_junit(stack.test, stack)
    where = stack.root or "the repository root"
    lines = [f"  {name}:"]
    if stack.root:
        lines.append(f"    # Runs in {stack.root}; check that the toolchain steps read that unit's files.")
    lines += [f"    name: Evidence ({stack.language} at {where})", "    runs-on: ubuntu-latest"]
    if stack.root:
        lines += ["    defaults:", "      run:", f"        working-directory: {stack.root}"]
    steps = [
        "- uses: actions/checkout@v4",
        *_rebased(stack.setup, stack.root),
        *stack.prepare,
        *([missing] if missing else []),
        "- name: Run tests with coverage",
        f"  run: {json.dumps(command, ensure_ascii=False)}",
        "- name: Keep the coverage report and test results",
        "  if: always()",
        "  uses: actions/upload-artifact@v4",
        "  with:",
        # The job name, unique in the workflow: upload-artifact refuses a repeated name.
        f"    name: repowise-{name}",
        "    if-no-files-found: warn",
        "    path: |",
        *(f"      {p}" for p in _kept(stack)),
    ]
    return [*lines, "    steps:", *_indent(steps, 6)]


def _github(stacks: Sequence[Stack], branch: str) -> str:
    lines = [
        "# Written by `repowise evidence setup --write`. Each job runs one unit's tests with",
        "# coverage and keeps the report and test results; download them and run the",
        "# `repowise coverage add` and `repowise evidence add` lines `repowise evidence setup` prints.",
        "name: Repowise evidence",
        "on:",
        "  pull_request:",
        "  push:",
        f"    branches: [{json.dumps(branch, ensure_ascii=False)}]",
        "permissions:",
        "  contents: read",
        "jobs:",
    ]
    for name, stack in zip(_job_names(stacks, "evidence"), stacks, strict=True):
        lines += _github_job(name, stack)
    return "\n".join(lines) + "\n"


def _setup_runs(stack: Stack) -> list[str]:
    """The install commands of the unit's GitHub setup (one-line ``run`` steps), minus pipx."""
    runs = [line[len("- run: "):] for line in stack.setup if line.startswith("- run: ")]
    return [r for r in runs if not r.startswith("pipx ")]


def _gitlab_job(name: str, stack: Stack) -> list[str]:
    command, missing = _with_junit(stack.test, stack)
    image = _GITLAB_IMAGES.get(stack.package_manager or "") or _GITLAB_IMAGES.get(stack.language, "alpine")
    tool = _GITLAB_TOOLS.get(stack.package_manager or "")
    script = list(dict.fromkeys([
        *([f"cd {quote_arg(stack.root)}"] if stack.root else []),
        *([tool] if tool else []),
        # A workstation's installs when the unit names them, else its CI setup's.
        *(stack.requires or _setup_runs(stack)),
    ]))
    lines = [
        f"{name}:",
        f"  image: {image}   # adjust to your toolchain version",
        "  rules:",
        "    - if: $CI_PIPELINE_SOURCE == \"merge_request_event\"",
        "    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH",
        "  script:",
        *(f"    - {json.dumps(s, ensure_ascii=False)}" for s in script),
        *([f"    {missing}"] if missing else []),
        f"    - {json.dumps(command, ensure_ascii=False)}",
        "  artifacts:",
        "    when: always",
        "    paths:",
        *(f"      - {json.dumps(p, ensure_ascii=False)}" for p in _kept(stack)),
    ]
    junit = _junit_path(stack)
    if junit:
        lines += ["    reports:", f"      junit: {json.dumps(junit, ensure_ascii=False)}"]
    return lines


def _gitlab(stacks: Sequence[Stack]) -> str:
    lines = [
        "# Written by `repowise evidence setup --write`. Include it from .gitlab-ci.yml:",
        "#   include:",
        f"#     - local: {CI_PATHS['gitlab']}",
        "# Each job runs one unit's tests with coverage and keeps the report and test results",
        "# as artifacts; `repowise evidence setup` prints the lines that ingest them.",
    ]
    for name, stack in zip(_job_names(stacks, "repowise-evidence"), stacks, strict=True):
        lines += ["", *_gitlab_job(name, stack)]
    return "\n".join(lines) + "\n"


def ci_snippet(stacks: Sequence[Stack], ci: str, branch: str | None) -> dict[str, Any]:
    """The *ci* file for *stacks*, JSON-ready: ``path``, ``content`` (None when no unit has a
    command), the unit roots it runs, and ``skipped`` units with why.

    *branch* is the default branch pushes run on (``main`` when git cannot name one).
    GitLab has no Actions step, so a unit whose command needs one
    (:attr:`Stack.shell`) is skipped there.
    """
    if ci not in CI_KINDS:
        raise ValueError(f"unknown CI {ci!r}; one of {', '.join(CI_KINDS)}")
    kept: list[Stack] = []
    skipped: list[dict[str, str | None]] = []
    for stack in stacks:
        if not stack.test:
            reason = stack.to_dict()["reason"] or "Repowise could not tell how this unit runs its tests."
        elif ci == "gitlab" and stack.shell:
            reason = f"Its test command runs only inside the {stack.shell} step it ships with."
        else:
            kept.append(stack)
            continue
        skipped.append({"root": stack.root, "stack": stack.language, "reason": reason})
    if not kept:
        content = None
    elif ci == "github":
        content = _github(kept, branch or "main")
    else:
        content = _gitlab(kept)
    return {
        "ci": ci,
        "path": CI_PATHS[ci],
        "content": content,
        "units": [s.root for s in kept],
        "skipped": skipped,
    }


__all__ = ["CI_KINDS", "CI_PATHS", "ci_snippet", "detect_ci"]
