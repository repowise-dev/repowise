"""Filter selection — pure functions mapping (command, output) to a filter.

Command matching wins over content sniffing: when the caller knows what ran,
trust it. Content sniffing is the fallback for surfaces that only see bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.registry import filter_registry

# Wrapper prefixes that mask the real command. Stripped repeatedly so
# "uv run python -m pytest" normalizes down to "pytest ...".
_WRAPPER_RE = re.compile(
    r"^(?:"
    r"uv run|uvx|npx|pnpm exec|pnpm dlx|yarn dlx|poetry run|pipenv run|hatch run|"
    r"python3? -m|py -m"
    r")\s+",
    re.IGNORECASE,
)

# Leading POSIX-style env assignments: FOO=bar BAZ=1 cmd ...
_ENV_ASSIGN_RE = re.compile(r"^\w+=\S+\s+")

# Path prefix on the executable: .venv\Scripts\pytest.exe → pytest
_EXE_PATH_RE = re.compile(r'^(?:"[^"]*[\\/]|\S*[\\/])(?P<exe>[\w.-]+?)(?:\.exe)?(?:")?(?=\s|$)')


def normalize_command(command: str) -> str:
    """Lowercased command with wrappers, env assignments, and exe paths stripped."""
    cmd = command.strip()
    for _ in range(4):
        previous = cmd
        cmd = _ENV_ASSIGN_RE.sub("", cmd)
        cmd = _WRAPPER_RE.sub("", cmd)
        if cmd == previous:
            break
    cmd = _EXE_PATH_RE.sub(lambda m: m.group("exe"), cmd)
    return cmd.lower()


def select_filter(
    command: str = "",
    output: str = "",
    *,
    disabled: Iterable[str] = (),
) -> OutputFilter | None:
    """Pick the filter for *command*/*output*, or None when nothing applies."""
    disabled_set = set(disabled)
    candidates = [f for f in filter_registry.filters() if f.name not in disabled_set]
    normalized = normalize_command(command) if command else ""
    if normalized:
        for f in candidates:
            if f.matches_command(normalized):
                return f
    if output:
        for f in candidates:
            if f.matches_content(output):
                return f
    return None
