"""Material for the repository overview, read off a generation run.

The overview is written for someone opening the repository for the first time,
so it is fed what that person needs: the README from the top, the commands and
names a user types (each with its own declaration span), the walkthrough pages
already written for the main operations, the top-level parts with the summary
of each part's own page, which part imports which, and the terms the
documentation defines. Counts of files, cycles and hotspots are not fed: they
say nothing to that reader.

Everything here is read from what the run already holds; nothing calls a model.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ...ingestion.barrels import BARREL_FILENAMES
from ...support_paths import is_support_path
from ..onboarding import SLOT_GETTING_STARTED, SLOT_HOW_IT_WORKS, SLOT_TITLES
from ..onboarding.docs import DocSection, doc_sections, is_user_doc, rank_sections, readme_intro
from ..onboarding.surface import Journey, Surface, Surfaces, journey

_MAX_PARTS = 8
_MAX_EDGES = 12
_MAX_JOURNEYS = 6
_SUMMARY_CHARS = 260
_QUICK_WORDS = ("quickstart", "quick start", "getting started", "usage", "example", "tutorial")


@dataclass(frozen=True)
class Part:
    title: str
    path: str
    files: int
    summary: str


@dataclass(frozen=True)
class PartEdge:
    src: str
    dst: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class ReadingStop:
    title: str
    why: str


@dataclass(frozen=True)
class OverviewMaterial:
    """Everything the overview prompt shows besides the system map."""

    readme: list[DocSection] = field(default_factory=list)
    user_docs: list[DocSection] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    commands: list[Surface] = field(default_factory=list)
    tools: list[Surface] = field(default_factory=list)
    api: list[Surface] = field(default_factory=list)
    journeys: list[Journey] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    edges: list[PartEdge] = field(default_factory=list)
    terms: list[Any] = field(default_factory=list)
    reading: list[ReadingStop] = field(default_factory=list)

    def digest_text(self) -> str:
        """What the page is written from, beyond the evidence files: its reuse subject."""
        # Documentation read from disk is not in the run's source map, so its
        # text is part of the subject: an edited README rewrites the page.
        return "\n".join(
            [*(s.text for s in self.readme + self.user_docs),
             *(f"{c.name}|{c.doc}" for c in self.commands + self.tools + self.api),
             *(f"{j.title}|{j.opening}|{j.stages}" for j in self.journeys),
             *(f"{p.title}|{p.summary}" for p in self.parts),
             *(f"{t.term}|{t.definition}" for t in self.terms)]
        )


def clean_summary(text: str, limit: int = _SUMMARY_CHARS) -> str:
    """A page summary as one plain line: headings, fences and links removed."""
    unfenced = re.sub(r"(?ms)^\s*```.*?(^\s*```|\Z)", "", text or "")
    lines = [
        ln for ln in unfenced.splitlines()
        if ln.strip() and not ln.lstrip().startswith(("#", "|", "<"))
    ]
    flat = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", " ".join(" ".join(lines).split()))
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    end = cut.rfind(". ")
    return cut[: end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "..."


#: The shapes a structure-only module page summary takes: a path followed by
#: what it defines or imports, or a coverage count. Not a description.
_CODE_BUILT = re.compile(
    r"^`[^`]+`(\s+(defines|imports|·)|\s*$)|\bis imported by\b|\bCovers the \d+|"
    r"belongs to the \w+ layer|\*\*Language:\*\*"
)
#: Files that stand for their package: the barrels, plus Go's package comment
#: file and a Rust crate root.
_PACKAGE_FILES = BARREL_FILENAMES | {"doc.go", "lib.rs"}


def _file_doc(text: str, path: str) -> str:
    """The file's own description: module docstring, Go package comment, leading JSDoc."""
    head = "\n".join(text.splitlines()[:80])
    doc = ""
    if path.endswith(".py"):
        m = re.match(r'\s*(?:#[^\n]*\n\s*)*[rRuU]?("""|\'\'\')(.*?)\1', head, re.S)
        doc = m.group(2) if m else ""
    elif path.endswith(".go"):
        m = re.search(r"((?:^//[^\n]*\n)+)package \w+", head, re.M)
        doc = re.sub(r"^//\s?", "", m.group(1), flags=re.M) if m else ""
        doc = "" if doc.lower().startswith(("copyright", "licensed")) else doc
    elif path.endswith((".ts", ".js", ".tsx", ".jsx")):
        m = re.match(r"\s*/\*\*(.*?)\*/", head, re.S)
        doc = re.sub(r"^\s*\* ?", "", m.group(1), flags=re.M) if m else ""
    return " ".join(re.sub(r"(?m)^\s*([=~^*#-])\1{2,}\s*$", "", doc).split())


def own_description(source_map: dict[str, bytes], files: Iterable[str]) -> str:
    """A directory's description from its own package file, else any of its files.

    Only the shallowest files count: a child package's docstring would describe
    the child, not the directory.
    """
    files = list(files)
    if not files:
        return ""
    top = min(f.count("/") for f in files)
    own = sorted(
        (f for f in files if f.count("/") == top),
        key=lambda f: (f.rsplit("/", 1)[-1] not in _PACKAGE_FILES, f),
    )
    for path in own[:12]:
        if path in source_map:
            doc = _file_doc(source_map[path].decode("utf-8", errors="replace"), path)
            if doc:
                return doc
    return ""


def _summary_for(mg: Any, summaries: dict[str, str], source_map: dict[str, bytes]) -> str:
    """The part's own page summary, or its code's own description when the page is structural."""
    summary = summaries.get(mg.key, "")
    lead = clean_summary(summary)
    if lead and not _CODE_BUILT.search(lead):
        return lead
    return clean_summary(own_description(source_map, mg.file_paths))


def _top_level(groups: list[Any]) -> list[Any]:
    """Groups no other group contains; one lone top is opened a level down."""
    keys = {mg.key for mg in groups}

    def under(key: str, others: Iterable[str]) -> bool:
        return any(key.startswith(o + "/") for o in others if o != key)

    top = [mg for mg in groups if not under(mg.key, keys)]
    if len(top) == 1:
        root = top[0].key
        below = [mg for mg in groups if mg.key.startswith(root + "/")]
        below_keys = {mg.key for mg in below}
        top += [mg for mg in below if not under(mg.key, below_keys)]
    return top


def top_parts(
    groups: list[Any], summaries: dict[str, str], source_map: dict[str, bytes]
) -> list[Part]:
    """The repository's top-level code parts, biggest first, each with its own page's summary."""
    code = [
        mg for mg in groups
        if not mg.key.startswith(".") and not is_support_path(mg.key + "/x")
    ]
    parts = [
        Part(
            title=mg.display,
            path=mg.key,
            files=len(getattr(mg, "context_paths", None) or mg.file_paths),
            summary=_summary_for(mg, summaries, source_map),
        )
        for mg in _top_level(code)
    ]
    parts.sort(key=lambda p: (-p.files, p.path))
    return parts[:_MAX_PARTS]


def part_edges(graph: Any, groups: list[Any], parts: list[Part]) -> list[PartEdge]:
    """Import edges between the parts, heaviest first, with the names imported most."""
    owner = _file_owners(groups, {p.path for p in parts})
    agg: dict[tuple[str, str], Counter[str]] = {}
    for src, dst, data in graph.edges(data=True):
        a, b = owner.get(str(src)), owner.get(str(dst))
        if data.get("edge_type") == "imports" and a and b and a != b:
            agg.setdefault((a, b), Counter()).update(data.get("imported_names") or ["(module)"])
    rows = sorted(agg.items(), key=lambda kv: (-kv[1].total(), kv[0]))[:_MAX_EDGES]
    return [
        PartEdge(a, b, tuple(n for n, _ in sorted(v.items(), key=lambda x: (-x[1], x[0]))[:4]))
        for (a, b), v in rows
    ]


def _file_owners(groups: list[Any], wanted: set[str]) -> dict[str, str]:
    """``file -> part``: the deepest part containing the file's group.

    A lone top-level part is listed beside its own children, and a child's
    files belong to the child.
    """
    owner: dict[str, str] = {}
    for mg in sorted(groups, key=lambda g: len(g.key)):
        part = max(
            (p for p in wanted if mg.key == p or mg.key.startswith(p + "/")), key=len, default=None
        )
        for path in (getattr(mg, "context_paths", None) or mg.file_paths) if part else ():
            owner[path] = part
    return owner


def run_journeys(run: Any) -> list[Journey]:
    """The walkthrough pages written for the run's flows, this run's text first.

    A flow page this run did not regenerate is read from the stored page, so a
    scoped update that rewrites only the front page still links its walkthroughs.
    """
    prior = getattr(run.gen, "_prior_pages", {}) or {}
    written = getattr(run, "flow_page_contents", {}) or {}
    out: list[Journey] = []
    for plan in run.flow_plans():
        stored = prior.get(plan.page_id)
        if stored is not None and stored.provider_name == "template":
            stored = None  # a keyless stub lists steps; it tells no journey
        content = written.get(plan.page_id) or getattr(stored, "content", "")
        if content:
            out.append(journey(plan.title, content))
    return out[:_MAX_JOURNEYS]


def _reading(journeys: list[Journey], parts: list[Part]) -> list[ReadingStop]:
    stops = [
        ReadingStop(SLOT_TITLES[SLOT_GETTING_STARTED], "install it, use it once, set up to develop"),
        ReadingStop(SLOT_TITLES[SLOT_HOW_IT_WORKS], "the main journeys and what happens behind each"),
    ]
    stops += [ReadingStop(j.title, "walkthrough of one main operation") for j in journeys]
    stops += [ReadingStop(p.title, "module page for a top-level part") for p in parts[:3]]
    return stops


def overview_material(
    docs: dict[str, bytes],
    *,
    surfaces: Surfaces,
    journeys: list[Journey],
    parts: list[Part],
    edges: list[PartEdge],
    terms: list[Any],
) -> OverviewMaterial:
    """The overview's material from the run's documentation and the pieces read off the run."""
    sections = doc_sections(docs)
    user_docs = rank_sections(
        sections,
        [f"{p} " for p in surfaces.programs] + list(_QUICK_WORDS),
        heading_words=_QUICK_WORDS,
        path_filter=lambda p: is_user_doc(p) and not p.lower().startswith("readme"),
        char_cap=4000,
        per_section=2000,
    )
    return OverviewMaterial(
        readme=readme_intro(sections, char_cap=6000),
        user_docs=user_docs,
        programs=surfaces.programs,
        commands=surfaces.commands,
        tools=surfaces.tools,
        api=surfaces.api,
        journeys=journeys,
        parts=parts,
        edges=edges,
        terms=list(terms),
        reading=_reading(journeys, parts),
    )
