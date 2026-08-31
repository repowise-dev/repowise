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

from collections.abc import Iterable, Mapping
from typing import Any

_STR = str


def _parse_folder_coverage(
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
        if isinstance(entry, (tuple, list)) and len(entry) == 2:
            glob_pattern, pct_raw = entry
        elif isinstance(entry, _STR) and "=" in entry:
            glob_pattern, _, pct_raw = entry.partition("=")
        else:
            raise ValueError(
                f"folder_coverage entry {entry!r} must be 'glob=pct' "
                "(e.g. 'src/core=1.0') or a (glob, pct) pair"
            )
        glob_pattern = glob_pattern.strip().strip('"').strip("'")
        if not glob_pattern:
            raise ValueError("folder_coverage entries need a non-empty glob")
        pct_raw = pct_raw.strip().strip('"').strip("'")
        try:
            pct = float(pct_raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"folder_coverage entry {entry!r}: {pct_raw!r} is not a number"
            ) from None
        if pct < 0 or pct > 1:
            raise ValueError(
                f"folder_coverage entry {entry!r}: pct must be in [0, 1]"
            )
        rules.append((glob_pattern, pct))
    return tuple(rules)
