"""The outline itself, and the numbers that say whether it is any good.

An outline is a two-level thing — sections that group pages — but the tree it
becomes is not, because sections are not pages. A section exists to be a
heading; giving it a row would mean inventing pages nobody asked for and would
break the property that placement adds no pages. So a section is carried here
as a label plus a number, and the pages under it inherit the number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .grouping import ConceptGroup


@dataclass
class ConceptPage:
    """One leaf of the outline: a named group of files."""

    title: str
    #: One sentence saying what the page covers and what it deliberately does
    #: not. The "and does not" half is the part that stops adjacent pages
    #: describing each other.
    scope: str
    group: ConceptGroup
    #: Dotted number within the outline, e.g. ``"3.2"``. Assigned by the
    #: planner after ordering, never by the model.
    number: str = ""
    #: False when the title came from the deterministic fallback because the
    #: model skipped this group. Long inputs get skipped near the tail, so
    #: these are what the repair pass goes back for.
    named_by_model: bool = False

    @property
    def target_path(self) -> str:
        return self.group.target_path

    @property
    def structural_key(self) -> str:
        return self.group.structural_key

    @property
    def members(self) -> list[str]:
        return self.group.members


@dataclass
class ConceptSection:
    """A heading over one or more pages."""

    title: str
    pages: list[ConceptPage] = field(default_factory=list)
    number: str = ""


@dataclass
class ConceptOutline:
    """A complete, ordered outline over a repository's production files."""

    sections: list[ConceptSection] = field(default_factory=list)
    #: Terms lifted from the repository's own docs and bound to a real group.
    #: Present for provenance: a title that came from the project's vocabulary
    #: should be traceable to the sentence that supplied it.
    vocabulary: dict[str, str] = field(default_factory=dict)
    #: ``"llm"`` when a model named the groups, ``"deterministic"`` when the
    #: keyless fallback did. One axis, per D5: the map is free, the prose
    #: needs a key.
    naming_mode: str = "deterministic"

    @property
    def pages(self) -> list[ConceptPage]:
        return [p for s in self.sections for p in s.pages]

    def number_sections(self) -> None:
        """Assign dotted numbers by position. Sections are 1-based."""
        for si, section in enumerate(self.sections, start=1):
            section.number = str(si)
            for pi, page in enumerate(section.pages, start=1):
                page.number = f"{si}.{pi}"


@dataclass
class OutlineReport:
    """What the validator measured.

    Deliberately numbers rather than a boolean. "The outline validated" says
    nothing a reader can act on; "two directories unclaimed, nine paths
    invented" says exactly where the planner went wrong, and the probes showed
    those two failures move independently.
    """

    total_files: int = 0
    covered_files: int = 0
    unclaimed_files: list[str] = field(default_factory=list)
    double_claimed_files: list[str] = field(default_factory=list)
    #: Paths the model emitted that are not in the index. The probes measured
    #: one on a good run and nine on a constrained one, so this fires.
    invented_paths: list[str] = field(default_factory=list)
    page_count: int = 0
    section_count: int = 0
    empty_pages: list[str] = field(default_factory=list)
    oversized_pages: list[str] = field(default_factory=list)
    single_child_sections: list[str] = field(default_factory=list)
    duplicate_titles: list[str] = field(default_factory=list)
    bad_length_titles: list[str] = field(default_factory=list)
    bare_directory_titles: list[str] = field(default_factory=list)
    duplicate_structural_keys: list[str] = field(default_factory=list)
    #: Two pages resolving to one ``target_path``. That is two pages sharing a
    #: page id, so one silently overwrites the other on persist.
    duplicate_target_paths: list[str] = field(default_factory=list)
    test_paths_included: list[str] = field(default_factory=list)
    title_word_avg: float = 0.0

    @property
    def coverage(self) -> float:
        return self.covered_files / self.total_files if self.total_files else 0.0

    @property
    def hard_failures(self) -> list[str]:
        """The failures that make an outline wrong rather than untidy.

        Coverage and fabrication are correctness; a title one word too long is
        taste. Only the first kind blocks.
        """
        out: list[str] = []
        if self.unclaimed_files:
            out.append(f"{len(self.unclaimed_files)} unclaimed files")
        if self.double_claimed_files:
            out.append(f"{len(self.double_claimed_files)} double-claimed files")
        if self.invented_paths:
            out.append(f"{len(self.invented_paths)} invented paths")
        if self.empty_pages:
            out.append(f"{len(self.empty_pages)} pages claiming no files")
        if self.duplicate_structural_keys:
            out.append(f"{len(self.duplicate_structural_keys)} duplicate structural keys")
        if self.duplicate_target_paths:
            out.append(f"{len(self.duplicate_target_paths)} duplicate target paths")
        if self.test_paths_included:
            out.append(f"{len(self.test_paths_included)} test files in the tree")
        return out

    @property
    def ok(self) -> bool:
        return not self.hard_failures

    def as_lines(self) -> list[str]:
        """The report as the generation log and the progress doc want it."""
        return [
            f"coverage        {self.covered_files}/{self.total_files} = "
            f"{100 * self.coverage:.1f}%",
            f"pages           {self.page_count} in {self.section_count} sections",
            f"unclaimed       {len(self.unclaimed_files)}",
            f"double-claimed  {len(self.double_claimed_files)}",
            f"invented paths  {len(self.invented_paths)}",
            f"empty pages     {len(self.empty_pages)}",
            f"oversized       {len(self.oversized_pages)}",
            f"one-child sects {len(self.single_child_sections)}",
            f"dup titles      {len(self.duplicate_titles)}",
            f"title words avg {self.title_word_avg:.1f}",
            f"dup struct keys {len(self.duplicate_structural_keys)}",
            f"dup target paths {len(self.duplicate_target_paths)}",
            f"test files in   {len(self.test_paths_included)}",
        ]
