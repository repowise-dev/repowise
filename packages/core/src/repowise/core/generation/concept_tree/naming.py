"""Name and order the groups. The model never decides which files they hold.

This is the half of the planner an LLM is good at, and it is bounded so that
the half it is bad at cannot leak back in. Groups arrive with opaque ids and
the model returns a title, a scope sentence and a section for each id. It is
never shown a blank canvas and never asked which directory belongs where, so
the two failures the probes measured — dropping directories and inventing
plausible-looking paths — have nowhere to enter through. A group the model
forgets to name keeps a deterministic name; a group id it invents is discarded.
Coverage is therefore unchanged by anything the model does or fails to do.

The payload conveys breadth, never importance. Ranking the input by PageRank
and sampling the top files produced 12.7% coverage against 91.7% for the full
inventory, because hub files are infrastructure — ``models.py``, ``client.ts``,
``__init__.py`` — and an outline built around them describes the plumbing
rather than the product.

Repo docs do not come in here as prose. Nine thousand characters of README
injected alongside a 145-directory structural task collapsed the outline to
three pages. Vocabulary arrives already bound to a group by
:mod:`vocabulary`, as a short list of suggested names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from ..onboarding.grounding import check_grounding
from .grouping import ConceptGroup

logger = structlog.get_logger(__name__)

MAX_TITLE_WORDS = 7
MIN_TITLE_WORDS = 2

_FENCE = re.compile(r"^```[a-zA-Z]*\n|\n```$")


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object out of a model response, tolerating decoration.

    Providers here are asked for JSON by instruction rather than by an API
    flag, because no provider in the set exposes a native structured-output
    mode. So the response can arrive fenced, prefaced, or trailed with prose,
    and the recovery ladder is the same one decision extraction already uses:
    strip fences, parse, and failing that pull the outermost braces out.
    Returns an empty dict rather than raising — an unparseable outline falls
    back to deterministic names, which is a worse wiki, not a broken run.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.split("\n") if not line.strip().startswith("```"))
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Deterministic naming — the keyless path (D5)
# ---------------------------------------------------------------------------

_STOP_SEGMENTS = {"src", "lib", "app", "packages", "core", "internal", "pkg", "source"}


def _humanise(segment: str) -> str:
    words = re.split(r"[-_.\s]+", segment)
    out: list[str] = []
    for word in words:
        if not word:
            continue
        # Split camelCase so ``pageGenerator`` reads as two words.
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+", word) or [word]
        cased = [p.capitalize() if p.islower() else p for p in parts]
        # A trailing number belongs to the word it was written against: the
        # ``c4`` directory is the C4 model, and "C 4 Nodes" reads as a
        # tokeniser leaking into a page title.
        merged: list[str] = []
        for part in cased:
            if part.isdigit() and merged:
                merged[-1] += part
            else:
                merged.append(part)
        out.extend(merged)
    return " ".join(out)


def deterministic_title(group: ConceptGroup, layer_label: str = "") -> str:
    """A readable name from structure alone, for a run with no provider.

    Uses the last one or two meaningful path segments, prefixed by the layer
    when that is what makes the name unambiguous. Not as good as a named
    capability and not meant to be: per D5 the map is free and the prose needs
    a key, so a keyless wiki gets a real tree with plainer labels rather than a
    worse tree.
    """
    segments = [s for s in group.target_path.split("/") if s]
    meaningful = [s for s in segments if s.lower() not in _STOP_SEGMENTS] or segments
    tail = [_humanise(s) for s in meaningful[-2:]]
    title = " ".join(tail).strip()
    if layer_label and len(title.split()) < MIN_TITLE_WORDS:
        # Drop what the label already says. A ``docs`` directory in a "Docs &
        # Tooling" layer was titled "Docs Tooling Docs", which reads as a
        # generation bug rather than a name.
        label_words = {w.lower() for w in layer_label.split()}
        kept = [w for w in title.split() if w.lower() not in label_words]
        title = f"{layer_label} {' '.join(kept)}".strip()
    if not title:
        # The repository root has no path segments to name it by. It is a real
        # place and a common one — top-level entry points live there — so it
        # gets a name of its own rather than one derived from an empty path.
        title = "Repository Root Files"
    words = title.split()
    if len(words) > MAX_TITLE_WORDS:
        title = " ".join(words[:MAX_TITLE_WORDS])
    if len(title.split()) < MIN_TITLE_WORDS:
        title = f"{title} Components".strip()
    return title


def disambiguate_titles(titled: list[tuple[str, str]]) -> list[str]:
    """Make a set of ``(title, target_path)`` pairs' titles unique.

    Path-derived names collide easily: two packages that each hold a ``ui``
    directory produce the same last-two-segments name. The pages themselves
    stay distinct because identity is structural, but a reader sees two
    identical rows in the tree and cannot tell which is which, and anything
    that keys on a title (interlinking, an overview listing) has to guess.

    A colliding title takes on the next path segment above the ones it
    already used, and falls back to its full target path if even that
    repeats. Input order does not matter: the pairs are resolved in path
    order, so the same set always produces the same names.
    """
    order = sorted(range(len(titled)), key=lambda i: titled[i][1])
    out = list(titled)
    used: set[str] = set()
    for i in order:
        title, target = titled[i]
        if title not in used:
            used.add(title)
            out[i] = (title, target)
            continue
        segments = [s for s in target.split("/") if s]
        candidate = title
        for extra in reversed(segments[:-2] or segments[:-1]):
            candidate = f"{_humanise(extra)} {title}"
            if candidate not in used:
                break
        if candidate in used:
            candidate = f"{title} ({target})"
        used.add(candidate)
        out[i] = (candidate, target)
    return [t for t, _ in out]


def deterministic_scope(group: ConceptGroup) -> str:
    """A scope line stating coverage and its boundary, with no model."""
    # The root directory is the empty string, which is a real directory and
    # not an absent one; filtering it out left a group reading "0 directories".
    dirs = list(group.dirs)
    if len(dirs) == 1:
        where = dirs[0] or "the repository root"
    else:
        where = f"{len(dirs)} directories under {group.target_path or 'the repository root'}"
    return (
        f"Covers the {group.file_count} source files in {where}. "
        "Does not cover code outside those directories, which is documented on "
        "its own pages."
    )


# ---------------------------------------------------------------------------
# LLM naming
# ---------------------------------------------------------------------------


@dataclass
class NamedGroup:
    """What the namer decided about one group."""

    group: ConceptGroup
    title: str
    scope: str
    section: str
    order: int = 0
    #: True when this name came from the fallback rather than the model, either
    #: because there was no provider or because the model skipped the group.
    fallback: bool = False


SYSTEM_PROMPT = (
    "You are an information architect naming the sections of a technical wiki "
    "about a specific codebase, read by senior engineers and AI coding agents. "
    "You name and order groups of files that have already been decided for you. "
    "You never decide which files belong to which group. Reply with JSON only."
)

NAMING_INSTRUCTIONS = """\
Below is the complete set of file groups for the `{repo}` codebase. The grouping \
is fixed: every group is a contiguous part of the directory tree, every source \
file is in exactly one group, and you must not change, merge, split or drop any \
group.

For EVERY group id, return a title, a one-sentence scope, and the section it \
belongs to. Then order the sections.

TITLES
- Name the CAPABILITY or SUBSYSTEM, not the directory and not the layer. \
Good: "Dependency Graph Construction", "Output Distillation", \
"Code Health Scoring". Bad: "core/ingestion", "Ingestion Layer", \
"Utilities and Helpers".
- {min_words} to {max_words} words. Every title must be unique.
- An enumerative title is right only when a group genuinely spans several small \
areas, e.g. "Dead Code, Security, and Change Risk".
- Where a suggested name is given for a group, prefer it: it is the project's \
own word for this, taken from its documentation.

SCOPE
- One sentence saying what the page covers AND what it deliberately does not, \
so that adjacent pages do not describe each other.
- Refer only to paths that appear in that group's own listing. Do not name a \
file or directory that is not in the input.

SECTIONS
- Group the pages into {min_sections} to {max_sections} sections. Order them as \
a narrative: orientation first, then the core engine, then interfaces in order \
of distance from the engine, then operations, then reference.
- No section may contain exactly one page.
- Section titles are 1 to 5 words.

Return ONLY this JSON:
{{"sections": [{{"title": "...", "groups": ["g01", "g07", ...]}}],
  "names": {{"g01": {{"title": "...", "scope": "..."}}, ...}}}}

GROUPS:
"""


def _sample_names(members: list[str], limit: int) -> list[str]:
    """Filenames spread across the group's directories, not the first *limit*.

    Taking the alphabetically-first names from a group spanning two
    directories draws all of them from whichever directory sorts first, so the
    namer sees half the page and names it after that half. Round-robin gives
    every directory a turn before any gets a second.
    """
    by_dir: dict[str, list[str]] = {}
    for member in sorted(members):
        by_dir.setdefault(member.rsplit("/", 1)[0], []).append(member.rsplit("/", 1)[-1])
    out: list[str] = []
    order = sorted(by_dir)
    depth = 0
    while len(out) < limit:
        added = False
        for directory in order:
            names = by_dir[directory]
            if depth < len(names):
                out.append(names[depth])
                added = True
                if len(out) >= limit:
                    break
        if not added:
            break
        depth += 1
    return out


def _common_prefix(dirs: list[str]) -> str:
    """The deepest directory every entry of *dirs* sits under."""
    if not dirs:
        return ""
    split = [d.split("/") for d in dirs]
    common = split[0]
    for parts in split[1:]:
        cut = min(len(common), len(parts))
        for i in range(cut):
            if common[i] != parts[i]:
                cut = i
                break
        common = common[:cut]
    return "/".join(common)


def build_payload(
    groups: list[ConceptGroup],
    *,
    layer_labels: dict[str, str] | None = None,
    summaries: dict[str, str] | None = None,
    suggestions: dict[str, str] | None = None,
    entry_points: set[str] | None = None,
    max_filenames: int = 6,
) -> tuple[dict[str, Any], dict[str, ConceptGroup]]:
    """Build the namer's payload and the id-to-group index that decodes it.

    Ids are positional (``g01``) rather than derived from the path, so the
    model has nothing path-shaped to echo back and a fabricated id is
    immediately recognisable as one. Filenames are included because they carry
    what a directory name does not, but they are basenames only: a model given
    full paths will quote them, and a quoted path is a citation we would then
    have to verify.
    """
    labels = layer_labels or {}
    mod_summaries = summaries or {}
    hints = suggestions or {}
    entries = entry_points or set()

    index: dict[str, ConceptGroup] = {}
    entries_out: list[dict[str, Any]] = []
    for i, group in enumerate(groups, start=1):
        gid = f"g{i:02d}"
        index[gid] = group
        entry: dict[str, Any] = {
            "id": gid,
            "dir": group.target_path,
            "files": group.file_count,
            "names": _sample_names(group.members, max_filenames),
        }
        if len(group.dirs) > 1:
            # Relative to what the directories share, not to ``target_path``.
            # The target is one of the group's directories and is not always a
            # prefix of the others: a page covering ``ingestion/git_indexer``
            # and ``ingestion/graph`` is targeted at the first, and stripping
            # that prefix from the second yielded an empty string. Both
            # directories then arrived as ".", so the namer could not see that
            # the page covered two subsystems and named it after one.
            common = _common_prefix(group.dirs)
            cut = len(common) + 1 if common else 0
            entry["subdirs"] = [d[cut:] or d.rsplit("/", 1)[-1] for d in group.dirs[:8]]
        label = labels.get(group.dominant_layer)
        if label:
            entry["layer"] = label
        summary = mod_summaries.get(group.target_path)
        if summary:
            entry["summary"] = summary.replace("\n", " ")[:200]
        hint = hints.get(gid)
        if hint:
            entry["suggested_name"] = hint
        group_entries = sorted(e.rsplit("/", 1)[-1] for e in entries if e in set(group.members))
        if group_entries:
            entry["entry_points"] = group_entries[:3]
        entries_out.append(entry)

    return {"repo": "", "groups": entries_out}, index


_MAX_TITLE_CHARS = 120


def _clean_title(raw: Any) -> str:
    # Only a string is a title. A dict or a list stringifies into something
    # that looks like a title to every downstream check and reads as garbage
    # to a person.
    if not isinstance(raw, str):
        return ""
    title = raw.strip().strip('"').strip()
    title = re.sub(r"\s+", " ", title)
    # A model asked for a title sometimes returns "Section 3: Ingestion".
    title = re.sub(r"^\d+(\.\d+)*[.:)\-]\s*", "", title)
    # Length is bounded here rather than left to the validator, which treats
    # it as taste. An unbounded title is a rendering problem, not a taste one.
    if len(title) > _MAX_TITLE_CHARS:
        title = title[:_MAX_TITLE_CHARS].rsplit(" ", 1)[0]
    return title


def decode_response(
    data: dict[str, Any],
    index: dict[str, ConceptGroup],
    *,
    layer_labels: dict[str, str] | None = None,
) -> tuple[list[NamedGroup], list[str]]:
    """Turn the model's JSON into named groups, keeping the grouping intact.

    Returns ``(named, invented_ids)``. Every group in *index* comes back
    exactly once whatever the model said: an id it skipped gets a
    deterministic name, an id it invented is dropped and reported. This is the
    property that makes coverage independent of the model.
    """
    labels = layer_labels or {}
    names = data.get("names")
    names = names if isinstance(names, dict) else {}
    sections = data.get("sections")
    sections = sections if isinstance(sections, list) else []

    section_of: dict[str, str] = {}
    order_of: dict[str, int] = {}
    section_rank: dict[str, int] = {}
    invented: list[str] = []
    counter = 0
    for rank, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        title = _clean_title(section.get("title"))
        if not title:
            continue
        section_rank.setdefault(title, rank)
        # A scalar here is not hypothetical: a model asked for a list of ids
        # returned a count instead, and ``or []`` passes a non-empty scalar
        # straight through to the loop. The whole point of this function is
        # that no model response can break the run, so the shape is checked
        # rather than assumed.
        raw_groups = section.get("groups")
        if not isinstance(raw_groups, (list, tuple)):
            raw_groups = []
        for gid in raw_groups:
            if not isinstance(gid, (str, int)):
                continue
            gid = str(gid)
            if gid not in index:
                invented.append(gid)
                continue
            if gid in section_of:
                # Claimed twice. The first section wins, deterministically.
                continue
            section_of[gid] = title
            order_of[gid] = counter
            counter += 1

    named: list[NamedGroup] = []
    for gid, group in index.items():
        entry = names.get(gid)
        entry = entry if isinstance(entry, dict) else {}
        title = _clean_title(entry.get("title"))
        scope = str(entry.get("scope") or "").strip()
        fallback = False
        if not title:
            title = deterministic_title(group, labels.get(group.dominant_layer, ""))
            fallback = True
        if not scope:
            scope = deterministic_scope(group)
        section = section_of.get(gid) or labels.get(group.dominant_layer) or "Reference"
        named.append(
            NamedGroup(
                group=group,
                title=title,
                scope=scope,
                section=section,
                order=order_of.get(gid, len(index) + len(named)),
                fallback=fallback,
            )
        )

    named.sort(
        key=lambda n: (section_rank.get(n.section, len(sections)), n.order, n.group.target_path)
    )
    return named, sorted(set(invented))


def ground_scopes(named: list[NamedGroup]) -> list[str]:
    """Strip citations from scope sentences that the group's files do not support.

    Reuses the onboarding grounding check rather than repeating its shape
    rules: a backticked token that looks like a path or a symbol and is absent
    from the group's own file list loses its backticks, so it reads as prose
    instead of as a verified reference. Returns every token demoted, which the
    validator counts as an invented path.
    """
    ungrounded: list[str] = []
    for entry in named:
        cleaned, bad = check_grounding(entry.scope, entry.group.members)
        entry.scope = cleaned
        ungrounded.extend(bad)
    return ungrounded
