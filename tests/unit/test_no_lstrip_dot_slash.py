"""No ``.lstrip("./")`` calls that strip leading dots from dot-directory names (#4031).

``str.lstrip("./")`` strips characters rather than the ``"./"`` prefix, turning
``"./.generated/index.js"`` or ``".github/workflows/ci.yml"`` into
``"generated/index.js"`` or ``"github/workflows/ci.yml"``. Use
``removeprefix("./")`` instead.
"""

from __future__ import annotations

import ast
import pathlib

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"

# Remaining sites tracked in #3990 (PR #4027) and #4014 (PR #4023).
# The set only ever shrinks as those land.
_KNOWN: frozenset[str] = frozenset(
    {
        "packages/cli/src/repowise/cli/commands/augment_cmd/decision_inject.py",
        "packages/cli/src/repowise/cli/commands/augment_cmd/glob_rescue.py",
        "packages/core/src/repowise/core/analysis/doc_drift/resolver.py",
    }
)


def _single_string_arg(call: ast.Call) -> str | None:
    if len(call.args) != 1:
        return None
    arg = call.args[0]
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None


def _is_lstrip_dot_slash(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if getattr(node.func, "attr", None) != "lstrip":
        return None
    arg = _single_string_arg(node)
    if arg is not None and {".", "/"}.issubset(arg):
        return f"line {node.lineno}: lstrip({arg!r})"
    return None


def _analyze_source(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        hit
        for node in ast.walk(tree)
        if (hit := _is_lstrip_dot_slash(node)) is not None
    ]


def _analyze_file(path: pathlib.Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _analyze_source(source)


def _offenders() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(_PACKAGES.rglob("*.py")):
        hits = _analyze_file(path)
        if hits:
            rel = path.relative_to(_PACKAGES.parent).as_posix()
            found[rel] = hits
    return found


def test_no_new_lstrip_dot_slash_calls() -> None:
    offenders = {p: hits for p, hits in _offenders().items() if p not in _KNOWN}
    assert not offenders, (
        "Forbidden .lstrip('./') call(s) found under packages/:\n"
        + "\n".join(f"  {p}: {', '.join(hits)}" for p, hits in sorted(offenders.items()))
        + '\n\nUse .removeprefix("./") so dot-directory paths keep their leading dot.'
    )


def test_known_sites_still_exist() -> None:
    """Every allowlist entry must still be a real match, so the list cannot rot."""
    found = _offenders()
    stale = sorted(p for p in _KNOWN if p not in found)
    assert not stale, (
        "These no longer call .lstrip('./') — remove them from _KNOWN:\n"
        + "\n".join(f"  {p}" for p in stale)
    )


def test_detector_catches_lstrip_dot_slash_ast_only() -> None:
    assert _analyze_source('x = target.lstrip("./")\n') == ["line 1: lstrip('./')"]
    assert _analyze_source("x = target.lstrip('/.')\n") == ["line 1: lstrip('/.')"]
    assert _analyze_source('"""Docstring mentioning target.lstrip("./")."""\nx = target.removeprefix("./")\n') == []
    assert _analyze_source('x = target.lstrip("/")\n') == []
