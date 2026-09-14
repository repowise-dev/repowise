"""Synthetic Git repositories for change-health comparison tests.

Small, purpose-built repos rather than Repowise's own history: the comparison
must generalise across languages and change shapes, and a fixture that only
ever sees this codebase cannot show that.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout


class Repo:
    """A throwaway repository built one commit at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        git(path, "init", "-q", "-b", "main")
        git(path, "config", "user.email", "dev@example.com")
        git(path, "config", "user.name", "Dev")

    def write(self, relative: str, content: str) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def remove(self, relative: str) -> None:
        git(self.path, "rm", "-q", relative)

    def move(self, source: str, destination: str) -> None:
        (self.path / destination).parent.mkdir(parents=True, exist_ok=True)
        git(self.path, "mv", source, destination)

    def commit(self, message: str, files: dict[str, str] | None = None) -> str:
        for relative, content in (files or {}).items():
            self.write(relative, content)
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", message, "--allow-empty")
        return git(self.path, "rev-parse", "HEAD").strip()


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[str], Repo]:
    counter = {"n": 0}

    def factory(name: str = "repo") -> Repo:
        counter["n"] += 1
        return Repo(tmp_path / f"{name}{counter['n']}")

    return factory


# -- source builders --------------------------------------------------------
# Each returns a function body whose complexity is driven by *branches*, so a
# test can ask for "the same function, more complex" in any language without
# hand-writing both revisions.


def python_complex(name: str, branches: int, *, indent: str = "") -> str:
    lines = [f"{indent}def {name}(value):"]
    for i in range(branches):
        lines.append(f"{indent}    if value == {i} and value > {i - 1}:")
        lines.append(f"{indent}        value = value + {i}")
    lines.append(f"{indent}    return value")
    return "\n".join(lines) + "\n"


def typescript_complex(name: str, branches: int) -> str:
    lines = [f"export function {name}(value: number): number {{"]
    for i in range(branches):
        lines.append(f"  if (value === {i} && value > {i - 1}) {{")
        lines.append(f"    value = value + {i};")
        lines.append("  }")
    lines.append("  return value;")
    lines.append("}")
    return "\n".join(lines) + "\n"


def go_complex(name: str, branches: int) -> str:
    lines = ["package main", "", f"func {name}(value int) int {{"]
    for i in range(branches):
        lines.append(f"\tif value == {i} && value > {i - 1} {{")
        lines.append(f"\t\tvalue = value + {i}")
        lines.append("\t}")
    lines.append("\treturn value")
    lines.append("}")
    return "\n".join(lines) + "\n"


#: Language name to (path suffix, builder). Drives the cross-language tests so
#: no response-shaping code is language-specific.
LANGUAGES: dict[str, tuple[str, Callable[[str, int], str]]] = {
    "python": ("app.py", python_complex),
    "typescript": ("app.ts", typescript_complex),
    "go": ("app.go", go_complex),
}


def python_io_in_loop(*, in_loop: bool) -> str:
    """A module whose sink is called from a loop, or not."""
    body = "    for row in rows:\n        fetch(row)\n" if in_loop else "    fetch(rows[0])\n"
    return (
        "import sqlite3\n\n\n"
        "def fetch(row):\n"
        "    conn = sqlite3.connect('db')\n"
        "    return conn.execute('select 1', row).fetchall()\n\n\n"
        "def handler(rows):\n" + body + "    return rows\n"
    )
