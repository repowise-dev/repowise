"""Plan the outline: group, name, validate, repair, validate again.

Two passes rather than one, because a single call cannot hold coverage and
allocation at the same time — pushing on structure made a planner drop
directories and invent paths at nine times the rate. Here the two jobs are
separated at the source, so the second pass has almost nothing to do: grouping
already guarantees coverage, and repair only ever revisits names.

The repair pass sees only what failed. Handing back the whole tree invites the
model to rewrite the parts that were fine, which is how a "fix these four
titles" request turns into a different outline.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from .grouping import ConceptGroup, GroupingParams, group_files, params_for
from .models import ConceptOutline, ConceptPage, ConceptSection, OutlineReport
from .naming import (
    NAMING_INSTRUCTIONS,
    SYSTEM_PROMPT,
    NamedGroup,
    _humanise,
    build_payload,
    decode_response,
    deterministic_scope,
    deterministic_title,
    ground_scopes,
    parse_json_object,
)
from .validation import validate_outline
from .vocabulary import bind_terms, extract_terms

logger = structlog.get_logger(__name__)

COST_OPERATION = "outline_planning"

MIN_SECTIONS = 5
MAX_SECTIONS = 11


@dataclass
class PlannerInputs:
    """Everything the planner needs, already resolved by the caller.

    Deliberately plain data. The planner is then testable without a pipeline,
    a database or a provider, which matters because its interesting properties
    are about determinism and those need to be asserted cheaply and often.
    """

    repo_name: str
    #: Production source files, tests already excluded (D8).
    production_files: list[str]
    repo_root: Path | None = None
    #: File path -> curated layer id.
    layer_of_file: dict[str, str] = field(default_factory=dict)
    #: Layer id -> display name, for section fallbacks and payload hints.
    layer_labels: dict[str, str] = field(default_factory=dict)
    #: Directory -> module summary written from the code.
    summaries: dict[str, str] = field(default_factory=dict)
    entry_points: set[str] = field(default_factory=set)
    #: Test files, kept only so the validator can prove none leaked in.
    test_files: set[str] = field(default_factory=set)


@contextlib.contextmanager
def _billed_as(provider: Any, operation: str) -> Iterator[None]:
    """Label this step's spend on the Costs page; no-op without a tracker."""
    tracker = getattr(provider, "_cost_tracker", None)
    if tracker is not None and hasattr(tracker, "record_as"):
        with tracker.record_as(operation):
            yield
    else:
        yield


def _build_outline(named: list[NamedGroup]) -> ConceptOutline:
    """Assemble sections from named groups, preserving the namer's order."""
    outline = ConceptOutline()
    by_section: dict[str, ConceptSection] = {}
    for entry in named:
        section = by_section.get(entry.section)
        if section is None:
            section = ConceptSection(title=entry.section)
            by_section[entry.section] = section
            outline.sections.append(section)
        section.pages.append(
            ConceptPage(
                title=entry.title,
                scope=entry.scope,
                group=entry.group,
                named_by_model=not entry.fallback,
            )
        )
    outline.number_sections()
    return outline


def _merge_thin_sections(outline: ConceptOutline) -> None:
    """Fold a section holding a single page into its neighbour.

    A heading over one page is not a grouping, it is a heading pretending to
    be one, and it makes a table of contents look padded. The page keeps its
    own title, so nothing is lost by moving it.
    """
    if len(outline.sections) <= 1:
        return
    changed = True
    while changed and len(outline.sections) > 1:
        changed = False
        for i, section in enumerate(outline.sections):
            if len(section.pages) != 1:
                continue
            target = i - 1 if i > 0 else i + 1
            outline.sections[target].pages.extend(section.pages)
            del outline.sections[i]
            changed = True
            break
    outline.number_sections()


def _disambiguate_titles(named: list[NamedGroup]) -> None:
    """Make path-derived titles unique by qualifying them with their parent.

    Two groups carved out of one directory tree — one holding a directory's own
    files, one holding its subdirectories — produce the same name from the same
    path segments. Rather than numbering them, which tells a reader nothing,
    the later one borrows a segment from further up its own path.
    """
    seen: dict[str, int] = {}
    for entry in sorted(named, key=lambda n: (n.title.lower(), n.group.target_path)):
        key = entry.title.lower()
        if key not in seen:
            seen[key] = 1
            continue
        segments = [s for s in entry.group.target_path.split("/") if s]
        for extra in reversed(segments[:-1]):
            candidate = f"{deterministic_title(entry.group)} {_humanise(extra)}".strip()
            candidate = " ".join(dict.fromkeys(candidate.split()))
            if candidate.lower() not in seen and len(candidate.split()) <= 7:
                entry.title = candidate
                break
        else:
            seen[key] += 1
            entry.title = f"{entry.title} {seen[key]}"
        seen[entry.title.lower()] = 1


def plan_deterministic(
    inputs: PlannerInputs, *, params: GroupingParams | None = None
) -> tuple[ConceptOutline, list[ConceptGroup]]:
    """The keyless outline: real structure, names derived from paths and layers.

    Per D5 this is the same tree the LLM path produces — identical grouping,
    identical identities — with plainer titles. Adding a key upgrades the
    prose, never the shape, so a user who adds one later does not get their
    wiki re-partitioned underneath them.

    That guarantee only holds if both paths partition with the same bounds, so
    *params* is threaded rather than re-derived. It was not, once: the caller's
    bounds were dropped here and the two paths produced different page counts
    for the same repository.
    """
    groups = group_files(
        inputs.production_files, layer_of_file=inputs.layer_of_file, params=params
    )
    named = [
        NamedGroup(
            group=g,
            title=deterministic_title(g, inputs.layer_labels.get(g.dominant_layer, "")),
            scope=deterministic_scope(g),
            section=inputs.layer_labels.get(g.dominant_layer) or "Reference",
            order=i,
            fallback=True,
        )
        for i, g in enumerate(groups)
    ]
    _disambiguate_titles(named)
    outline = _build_outline(named)
    _merge_thin_sections(outline)
    outline.naming_mode = "deterministic"
    return outline, groups


_REPAIR_INSTRUCTIONS = """\
These page titles from the outline you just produced need fixing. Nothing else \
about the outline is changing — do not restructure it, and do not rename any \
page that is not listed here.

Return a corrected title AND a scope sentence for exactly these group ids.

The title is {min_words} to {max_words} words, names the capability rather than \
the directory or the layer, and is unique across the whole outline.

The scope is a full English sentence saying what the page covers and what it \
deliberately does not. It is never a path: do not echo the `dir` value back.

Titles already in use elsewhere in the outline (do not reuse any of these):
{taken}

GROUPS TO RENAME:
{failures}

Return ONLY this JSON:
{{"names": {{"g07": {{"title": "...", "scope": "..."}}, ...}}}}
"""


def _ground_pages(outline: ConceptOutline) -> list[str]:
    """Strip unsupported citations from every page's scope. Returns the tokens."""
    from ..onboarding.grounding import check_grounding

    ungrounded: list[str] = []
    for page in outline.pages:
        cleaned, bad = check_grounding(page.scope, page.members)
        page.scope = cleaned
        ungrounded.extend(bad)
    return ungrounded


def _usable_scope(scope: str, target_path: str) -> bool:
    """Whether a returned scope is a sentence rather than an echoed path.

    Asked for a title and a scope for a group described by its directory, a
    model will sometimes hand the directory back in the scope field. That
    reads as a broken page rather than a terse one, so it is rejected and the
    deterministic sentence stands.
    """
    text = scope.strip()
    if len(text.split()) < 4:
        return False
    if text.rstrip("/") == target_path.rstrip("/"):
        return False
    # A lone path has no spaces before its first separator run; a sentence does.
    return "/" not in text.split(" ", 1)[0] or " " in text.strip()


def _repair_targets(outline: ConceptOutline, report: OutlineReport) -> set[str]:
    """The page titles worth a second call. Everything else is left alone.

    Three kinds qualify: titles the model repeated, titles that are just the
    directory respelled, and groups the model never named at all. The last is
    the common one on a large repository — a request to name seventy groups
    reliably loses a few near the end of the list — and it is also the
    cheapest to fix, because the second call carries only the stragglers.
    """
    bad = set(report.duplicate_titles)
    targets = {p.title for p in outline.pages if p.title.lower() in bad}
    targets |= set(report.bare_directory_titles)
    targets |= {t.rsplit(" (", 1)[0] for t in report.bad_length_titles}
    targets |= {p.title for p in outline.pages if not p.named_by_model}
    return targets


async def plan_outline(
    inputs: PlannerInputs,
    *,
    provider: Any | None = None,
    deterministic: bool = False,
    params: GroupingParams | None = None,
    repair: bool = True,
) -> tuple[ConceptOutline, OutlineReport]:
    """Produce a validated outline for *inputs*.

    Falls back to :func:`plan_deterministic` whenever there is no provider, the
    run is in deterministic mode, or the model's response cannot be used. A
    failed naming call costs the wiki its titles, never its structure.
    """
    all_files = set(inputs.production_files)
    resolved = params or params_for(len(all_files))

    if deterministic or provider is None:
        outline, _groups = plan_deterministic(inputs, params=params)
        report = validate_outline(
            outline,
            all_files=all_files,
            test_files=inputs.test_files,
            max_files_per_page=resolved.max_files,
        )
        return outline, report

    groups = group_files(
        inputs.production_files, layer_of_file=inputs.layer_of_file, params=params
    )
    payload, index = build_payload(
        groups,
        layer_labels=inputs.layer_labels,
        summaries=inputs.summaries,
        entry_points=inputs.entry_points,
    )
    payload["repo"] = inputs.repo_name

    terms: list[str] = []
    bound: dict[str, str] = {}
    if inputs.repo_root is not None:
        terms = extract_terms(inputs.repo_root)
        bound = bind_terms(terms, index)
        for gid, term in bound.items():
            for entry in payload["groups"]:
                if entry["id"] == gid:
                    entry["suggested_name"] = term
                    break
    logger.info(
        "concept_outline_vocabulary",
        terms_found=len(terms),
        terms_bound=len(bound),
    )

    sections_lo = min(MIN_SECTIONS, max(2, len(groups) // 8))
    sections_hi = max(sections_lo + 1, min(MAX_SECTIONS, len(groups) // 3 or 2))
    instructions = NAMING_INSTRUCTIONS.format(
        repo=inputs.repo_name,
        min_words=2,
        max_words=7,
        min_sections=sections_lo,
        max_sections=sections_hi,
    )
    body = json.dumps(payload, separators=(",", ":"))

    data: dict[str, Any] = {}
    try:
        with _billed_as(provider, COST_OPERATION):
            response = await provider.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=instructions + body,
                max_tokens=16000,
                temperature=0.2,
            )
        data = parse_json_object(getattr(response, "content", "") or "")
    except Exception as exc:
        logger.warning("concept_outline_naming_failed", error=str(exc))

    # Decoding sits inside the guard too. It used to sit outside, which meant a
    # response that parsed as JSON but held the wrong shape reached the decoder
    # and could take the run down — the one failure mode this design exists to
    # rule out. Decoding is defensive in its own right; this is the second line.
    try:
        named, invented_ids = decode_response(
            data, index, layer_labels=inputs.layer_labels
        )
    except Exception as exc:
        logger.warning("concept_outline_decode_failed", error=str(exc))
        named, invented_ids = decode_response({}, index, layer_labels=inputs.layer_labels)
    ungrounded = ground_scopes(named)
    if invented_ids:
        logger.warning("concept_outline_invented_group_ids", ids=invented_ids[:10])

    outline = _build_outline(named)
    _merge_thin_sections(outline)
    outline.naming_mode = "llm" if any(not n.fallback for n in named) else "deterministic"
    outline.vocabulary = {bound[gid]: index[gid].target_path for gid in bound}

    report = validate_outline(
        outline,
        all_files=all_files,
        test_files=inputs.test_files,
        max_files_per_page=resolved.max_files,
    )
    report.invented_paths = sorted(set(report.invented_paths) | set(ungrounded))

    if repair:
        targets = _repair_targets(outline, report)
        if targets:
            await _repair_titles(outline, index, targets, provider=provider)
            # Repair writes new prose, so it has to face the same citation
            # check the first pass did. Grounding after the last write rather
            # than after the first is the difference between checking the page
            # and checking a draft of it.
            ungrounded.extend(_ground_pages(outline))
            report = validate_outline(
                outline,
                all_files=all_files,
                test_files=inputs.test_files,
                max_files_per_page=resolved.max_files,
            )
            report.invented_paths = sorted(set(report.invented_paths) | set(ungrounded))

    logger.info(
        "concept_outline_planned",
        pages=report.page_count,
        sections=report.section_count,
        coverage=round(report.coverage, 4),
        invented=len(report.invented_paths),
        naming_mode=outline.naming_mode,
    )
    return outline, report


async def _repair_titles(
    outline: ConceptOutline,
    index: dict[str, ConceptGroup],
    targets: set[str],
    *,
    provider: Any,
) -> None:
    """Re-ask for just the failing titles, then apply only what improved.

    A repair that made things worse is discarded: the replacement has to be
    non-empty, correctly sized and not already in use, or the original stands.
    That keeps a bad second call from being worse than no second call.
    """
    gid_of = {g.structural_key: gid for gid, g in index.items()}
    failing = [p for p in outline.pages if p.title in targets]
    if not failing:
        return
    taken = sorted({p.title for p in outline.pages if p.title not in targets})

    lines = []
    for page in failing:
        gid = gid_of.get(page.structural_key, "")
        names = sorted(m.rsplit("/", 1)[-1] for m in page.members)[:6]
        lines.append(
            json.dumps(
                {
                    "id": gid,
                    "current_title": page.title,
                    "dir": page.target_path,
                    "files": len(page.members),
                    "names": names,
                },
                separators=(",", ":"),
            )
        )

    prompt = _REPAIR_INSTRUCTIONS.format(
        min_words=2,
        max_words=7,
        taken="\n".join(f"- {t}" for t in taken) or "(none)",
        failures="\n".join(lines),
    )
    try:
        with _billed_as(provider, COST_OPERATION):
            response = await provider.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                max_tokens=2000,
                temperature=0.2,
            )
        data = parse_json_object(getattr(response, "content", "") or "")
    except Exception as exc:
        logger.warning("concept_outline_repair_failed", error=str(exc))
        return

    names = data.get("names")
    if not isinstance(names, dict):
        return
    in_use = {p.title.lower() for p in outline.pages}
    fixed = 0
    for page in failing:
        gid = gid_of.get(page.structural_key, "")
        entry = names.get(gid)
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        words = len(title.split())
        if not title or not (2 <= words <= 7) or title.lower() in in_use:
            continue
        in_use.discard(page.title.lower())
        in_use.add(title.lower())
        page.title = title
        page.named_by_model = True
        scope = str(entry.get("scope") or "").strip()
        if _usable_scope(scope, page.target_path):
            page.scope = scope
        fixed += 1
    logger.info("concept_outline_repaired", requested=len(failing), applied=fixed)
