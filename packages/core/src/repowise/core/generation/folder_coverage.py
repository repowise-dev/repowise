"""Folder-coverage rule parsing for GenerationConfig (issue #633).

``folder_coverage`` in .repowise/config.yaml is a list of ``"<glob>=<pct>"``
strings, e.g.::

    folder_coverage:
      - "src/core=1.0"
      - "src/legacy=0.5"

The flag sugar ``--folder-coverage "src/core=1.0"`` (repeatable) maps onto
the same list. Each rule promises at least ``pct`` of the code files under
*glob* get a file_page.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_STR = str


def parse_folder_coverage(
    raw: Any,
) -> tuple[tuple[str, float], ...]:
    """Validate and normalize ``folder_coverage`` into ``((glob, pct), ...)``.

    Accepts a list of ``"glob=pct"`` strings (the config.yaml and CLI shape),
    or a sequence of ``(glob, pct)`` pairs for direct construction. Bad rules
    raise with the offending entry named, matching the strictness of
    ``source_evidence_files`` — a typo should fail loudly, not silently drop
    the folder the user explicitly asked to document.
    """
    if raw is None:
        return ()
    if isinstance(raw, _STR) or not isinstance(raw, Iterable):
        raise ValueError(
            "folder_coverage must be a list of 'glob=pct' strings, "
            "e.g. 'src/core=1.0'"
        )

    rules: list[tuple[str, float]] = []
    for entry in raw:
        glob_pattern, pct_raw = _split_entry(entry)
        # Repo paths are relative, so the issue's "./src/core" means "src/core".
        glob_pattern = glob_pattern.strip().strip('"').strip("'").removeprefix("./")
        if not glob_pattern:
            raise ValueError("folder_coverage entries need a non-empty glob")
        if isinstance(pct_raw, _STR):
            pct_raw = pct_raw.strip().strip('"').strip("'")
        try:
            pct = float(pct_raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"folder_coverage entry {entry!r}: {pct_raw!r} is not a number"
            ) from None
        if not 0 <= pct <= 1:  # also rejects nan
            raise ValueError(
                f"folder_coverage entry {entry!r}: pct must be in [0, 1]"
            )
        rules.append((glob_pattern, pct))
    return tuple(rules)


def _split_entry(entry: Any) -> tuple[str, Any]:
    """``(glob, raw pct)`` from a ``"glob=pct"`` string or a ``(glob, pct)`` pair."""
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        glob_pattern, pct_raw = entry
        if not isinstance(glob_pattern, _STR):
            raise ValueError(f"folder_coverage entry {entry!r}: the glob must be a string")
        return glob_pattern, pct_raw
    if isinstance(entry, _STR) and "=" in entry:
        glob_pattern, _, pct_raw = entry.partition("=")
        return glob_pattern, pct_raw
    raise ValueError(
        f"folder_coverage entry {entry!r} must be 'glob=pct' "
        "(e.g. 'src/core=1.0') or a (glob, pct) pair"
    )
