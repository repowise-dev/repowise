"""Whether a repository actually declares ruff as its formatter.

One question, two askers: the formatter-drift episode in
``precedent/structural.py`` uses the answer as the premise of a stored fact,
and the ``Format:`` line in generated agent files
(``generation/editor_files/tech_stack.py``) uses it as an instruction an agent
acts on. They read the declaration from here so the two cannot drift into
disagreeing about the same checkout.
"""

from __future__ import annotations

from pathlib import Path

#: Declaring one of these means the repo formats with something else. A ruff
#: mention beside one of them is lint configuration, not a formatter choice.
COMPETING_FORMATTERS: tuple[str, ...] = ("[tool.black]", "[tool.blue]", "[tool.yapf]")


def declares_ruff_format(root: Path) -> bool:
    """True when the repo names ruff, and only ruff, as its formatter.

    A ``pyproject.toml`` merely containing the words "ruff" and "format" is
    not a declaration: "formatter" appears in comments, tool descriptions and
    disabled-rule notes, and ruff-as-linter beside black-as-formatter is a
    common pairing. The declaration has to be structural — a
    ``[tool.ruff.format]`` section — or explicit: an invocation recorded in
    the Makefile, package.json, pre-commit config or justfile. A repo that
    declares any competing formatter is silent regardless.
    """
    pyproject = _read_text(root / "pyproject.toml")
    if any(marker in pyproject for marker in COMPETING_FORMATTERS):
        return False
    if "[tool.ruff.format]" in pyproject:
        return True
    # An explicit invocation anywhere a project records its own commands.
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
