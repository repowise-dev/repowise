"""Deterministic tables embedded into the repository overview.

The overview is written by a model, and enumerable facts written by a model are
resampled on every render: two calls with the same prompt, the same model and
the same temperature produced pages that disagreed on their row count and on
which paths they cited. Facts the run already holds — which packages exist,
where they are, how big they are — are built here instead and embedded after
the page comes back, the same way the architecture map already is.

That makes them identical on the model-written page and on the structure-only
page, stable across updates that changed no code, and assertable in a test.
"""

from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)

PACKAGE_TABLE_HEADING = "## Packages"

# The heading plus everything up to the next heading of the same or higher
# level. Anchored at a line start so a mention inside prose is not a match.
_PACKAGE_SECTION_RE = re.compile(
    r"^##[ \t]+Packages[ \t]*\n(?:.*?)(?=^#{1,2}[ \t]|\Z)",
    re.MULTILINE | re.DOTALL,
)


def build_package_table(package_stats: list[dict]) -> str | None:
    """Render ``Package | Path | Files | Languages`` as a markdown table.

    Returns ``None`` when the repository has no packages to tabulate — a
    single-package repository is the common case and a header with no rows
    under it is worse than no section.
    """
    if not package_stats:
        # Not an error: most repositories are not monorepos. Logged because a
        # table that silently stops appearing is a failure this page has
        # already shipped once.
        log.info("overview_package_table_empty")
        return None

    lines = [
        "| Package | Path | Files | Languages |",
        "|---|---|---|---|",
    ]
    for pkg in package_stats:
        langs = ", ".join(pkg.get("languages") or []) or "—"
        lines.append(f"| {pkg['name']} | `{pkg['path']}` | {pkg.get('files', 0)} | {langs} |")
    log.debug("overview_package_table_built", packages=len(package_stats))
    return "\n".join(lines)


def embed_package_table(content: str, table: str | None) -> str:
    """Return *content* with *table* under ``## Packages``, idempotently.

    Replaces an existing ``## Packages`` section wholesale, whether it is one
    this function wrote on a previous update or one the model wrote itself —
    the model writes that heading unprompted, and leaving both would give the
    reader the package list twice, once counted and once sampled.
    """
    if not table:
        return content

    section = f"{PACKAGE_TABLE_HEADING}\n\n{table}\n\n"
    if _PACKAGE_SECTION_RE.search(content):
        # Function replacement: table cells carry backslashes and backticks
        # that re.sub would otherwise read as group references.
        return _PACKAGE_SECTION_RE.sub(lambda _m: section, content, count=1)
    sep = "" if content.endswith("\n") else "\n"
    return f"{content}{sep}\n{section}"
