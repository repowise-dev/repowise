"""Whether a repository declares ruff as its formatter.

Two readers ask the same question of the same checkout: the formatter-drift
episode in ``precedent/structural.py`` and the ``Format:`` line in generated
agent files (``generation/editor_files/tech_stack.py``). The rules live here
so the two cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

#: Declaring one of these means the repo formats with something else.
_COMPETING_FORMATTERS: tuple[str, ...] = ("[tool.black]", "[tool.blue]", "[tool.yapf]")


def declares_ruff_format(root: Path) -> bool:
    """True when the repo names ruff, and only ruff, as its formatter.

    A ``[tool.ruff.format]`` section is a declaration, as is an explicit
    ``ruff format`` or ``ruff-format`` invocation in a file where a project
    records its commands. Ruff beside a competing formatter is lint
    configuration, not a formatter choice.
    """
    pyproject = _read_text(root / "pyproject.toml")
    if any(marker in pyproject for marker in _COMPETING_FORMATTERS):
        return False
    if "[tool.ruff.format]" in pyproject:
        return True
    for candidate in ("Makefile", "package.json", ".pre-commit-config.yaml", "justfile"):
        text = _read_text(root / candidate)
        if "ruff format" in text or "ruff-format" in text:
            return True
    return False


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
