"""Every ``REPOWISE_*`` variable the code reads is a registered setting.

Scans string literals (not comments or docstrings) in every package's Python
``src`` tree, and ``process.env`` reads and string literals in its TypeScript.
"""

from __future__ import annotations

import ast
import functools
import os
import re
from pathlib import Path

from repowise.core.settings import all_settings

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages"
SETTINGS_DIR = PACKAGES / "core" / "src" / "repowise" / "core" / "settings"

TOKEN = re.compile(r"(?<![A-Za-z0-9_])REPOWISE_[A-Z0-9_]*")
TS_READ = re.compile(
    r"""process\.env\.(REPOWISE_[A-Z0-9_]*)|["'`](REPOWISE_[A-Z0-9_]*)["'`]"""
)
_SKIP_DIRS = {"node_modules", "dist", "out", "build", "__tests__", "test", "tests", "__pycache__"}

#: Strings that look like variables but are file markers.
NOT_ENV_VARS: dict[str, str] = {
    "REPOWISE_AGENTS": "marker fencing the generated AGENTS.md block",
    "REPOWISE_COPILOT_CLI": "marker in generated Copilot instructions",
    "REPOWISE_CORRECTIONS": "marker fencing distill corrections",
    "REPOWISE_DISTILL": "marker fencing the distill instructions block",
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _source_files(suffixes: tuple[str, ...]) -> list[tuple[Path, str]]:
    """``(path, text)`` of each ``packages/*/src`` file naming a ``REPOWISE_`` string.

    Only ``src`` trees, never build or vendor dirs, and the substring check
    before any parse keeps the scan to a few seconds.
    """
    found = []
    for src in PACKAGES.glob("*/src"):
        for top, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if not name.endswith(suffixes):
                    continue
                path = Path(top, name)
                text = path.read_text(encoding="utf-8")
                if "REPOWISE_" in text:
                    found.append((path, text))
    return found


@functools.cache
def _python_reads() -> list[tuple[str, Path, int]]:
    hits = []
    for path, text in _source_files((".py",)):
        if SETTINGS_DIR in path.parents:
            continue
        tree = ast.parse(text)
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                hits += [(t, path, node.lineno) for t in TOKEN.findall(node.value)]
    return hits


@functools.cache
def _typescript_reads() -> list[tuple[str, Path, int]]:
    hits = []
    for path, text in _source_files((".ts", ".tsx", ".js", ".mjs")):
        if path.name.endswith((".test.ts", ".test.tsx", ".d.ts")):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in TS_READ.finditer(line):
                hits.append((match.group(1) or match.group(2), path, lineno))
    return hits


def _registered(token: str, names: set[str], prefixes: set[str]) -> bool:
    if token in names or any(token.startswith(p) for p in prefixes):
        return True
    # A prefix the code completes itself (``f"REPOWISE_HOOK_{flag}"``).
    return token.endswith("_") and any(n.startswith(token) for n in names | prefixes)


def test_every_repowise_env_var_read_is_registered():
    names = {s.env.name for s in all_settings() if s.env and not s.env.prefix}
    prefixes = {s.env.name for s in all_settings() if s.env and s.env.prefix}
    missing: dict[str, str] = {}
    for token, path, line in _python_reads() + _typescript_reads():
        if token in NOT_ENV_VARS or _registered(token, names, prefixes):
            continue
        missing.setdefault(token, f"{path.relative_to(ROOT).as_posix()}:{line}")
    assert not missing, "\n".join(
        f"{name} is read at {where} but no setting declares it. Add one line to the "
        "area that owns it in packages/core/src/repowise/core/settings/areas/, e.g. "
        f'_s("{name}", "str", None, "What it does, in plain words.") '
        "(add internal=True for a test or debug knob). If it is a marker, not a "
        "variable, add it to NOT_ENV_VARS in this test."
        for name, where in sorted(missing.items())
    )


def test_no_registered_env_var_is_dead():
    seen = {token for token, _, _ in _python_reads() + _typescript_reads()}
    for s in all_settings():
        if s.env is None:
            continue
        name = s.env.name
        assert name in seen or any(
            t.endswith("_") and name.startswith(t) for t in seen
        ), f"{name} is registered but nothing reads it; remove its setting"


def test_the_scan_sees_reads():
    """Guard the scanner itself: it must find a few well-known reads."""
    seen = {token for token, _, _ in _python_reads()}
    assert {"REPOWISE_DB_URL", "REPOWISE_EMBEDDER", "REPOWISE_HOOK_"} <= seen
    ts_seen = {token for token, _, _ in _typescript_reads()}
    assert "REPOWISE_API_KEY" in ts_seen
