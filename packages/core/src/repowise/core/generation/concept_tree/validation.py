"""Check the outline against the index, and count what is wrong.

Not a theoretical gap. Measured on this codebase, a planner run invented one
directory and a more heavily constrained one invented nine, among them
``core/generation/cost_tracker.py`` and ``web/src/lib/workspace-mode.ts`` —
paths that read as if they belong here and do not exist. An unchecked citation
like that reaches the reader as an authoritative reference, which is worse than
saying nothing.

Grouping is deterministic, so coverage and double-claiming ought to be true by
construction. They are still asserted here, because "ought to be" is how the
last two placement bugs got in, and because the repair pass can hand back a
tree the model touched.
"""

from __future__ import annotations

from collections import Counter

from ..models import compute_page_id  # noqa: F401  (documents the id contract)
from .models import ConceptOutline, OutlineReport

MIN_TITLE_WORDS = 2
MAX_TITLE_WORDS = 7


def _is_bare_directory(title: str, dirs: list[str]) -> bool:
    """Whether the title is just the directory it covers, respelled.

    ``core/ingestion`` and ``Ingestion`` are both failures of the same kind:
    they tell a reader where the code is, which they can already see, instead
    of what it does.
    """
    flat = title.strip().lower().replace("-", " ").replace("_", " ").replace("/", " ")
    for d in dirs:
        segs = [s for s in d.replace("-", " ").replace("_", " ").split("/") if s]
        if not segs:
            continue
        if flat == " ".join(segs).lower() or flat == segs[-1].lower():
            return True
    return False


def validate_outline(
    outline: ConceptOutline,
    *,
    all_files: set[str],
    test_files: set[str] | None = None,
    max_files_per_page: int,
) -> OutlineReport:
    """Measure *outline* against the file set it claims to cover.

    *all_files* is the production file set the grouper was given — the ground
    truth for both coverage and fabrication. A member the outline names that is
    not in it is an invented path, and the distinction between "invented" and
    "unclaimed" matters: they have different causes and the probes moved them
    in opposite directions.
    """
    report = OutlineReport()
    pages = outline.pages
    report.page_count = len(pages)
    report.section_count = len(outline.sections)
    report.total_files = len(all_files)

    claimed: Counter[str] = Counter()
    for page in pages:
        if not page.members:
            report.empty_pages.append(page.title or page.target_path)
        for member in page.members:
            claimed[member] += 1

    known = set(claimed)
    report.invented_paths = sorted(known - all_files)
    report.covered_files = len(known & all_files)
    report.unclaimed_files = sorted(all_files - known)
    report.double_claimed_files = sorted(p for p, n in claimed.items() if n > 1)

    if test_files:
        report.test_paths_included = sorted(known & test_files)

    for page in pages:
        if len(page.members) > max_files_per_page and not page.group.oversized:
            report.oversized_pages.append(f"{page.title} ({len(page.members)})")

    for section in outline.sections:
        if len(section.pages) == 1:
            report.single_child_sections.append(section.title)

    titles = [p.title.strip() for p in pages]
    report.duplicate_titles = sorted(
        t for t, n in Counter(t.lower() for t in titles).items() if n > 1
    )
    for page in pages:
        words = len(page.title.split())
        if words < MIN_TITLE_WORDS or words > MAX_TITLE_WORDS:
            report.bad_length_titles.append(f"{page.title} ({words}w)")
        if _is_bare_directory(page.title, page.group.dirs):
            report.bare_directory_titles.append(page.title)
    report.title_word_avg = (
        sum(len(t.split()) for t in titles) / len(titles) if titles else 0.0
    )

    keys = Counter(p.structural_key for p in pages)
    report.duplicate_structural_keys = sorted(k for k, n in keys.items() if n > 1)

    # The page id is derived from the target path alone, so a repeat here is a
    # row collision rather than a cosmetic one: the second page would overwrite
    # the first on persist and the wiki would quietly lose a page.
    targets = Counter(p.target_path for p in pages)
    report.duplicate_target_paths = sorted(t for t, n in targets.items() if n > 1)

    return report
