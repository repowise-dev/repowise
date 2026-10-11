"""File names that re-export other modules' symbols (barrels).

A leaf with no imports, so ingestion and analysis can both read it.
"""

from __future__ import annotations

#: Re-export barrel filenames. A barrel aggregates other modules' symbols and
#: is reached by importing those names (or, for a package's public entry, via
#: package.json ``exports``/``main``), so a barrel with no inbound graph edge
#: is not unreachable.
#:
#: Scope note for the analyzer: this rescues a file in the *unreachable-file*
#: pass only. It is NOT applied in the unused-export pass — a genuine symbol
#: defined in a barrel that nobody imports should still be flagged.
BARREL_FILENAMES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "index.ts",
        "index.tsx",
        "index.js",
        "index.jsx",
        "index.mts",
        "index.cts",
        "index.mjs",
        "index.cjs",
        "mod.rs",
    }
)
