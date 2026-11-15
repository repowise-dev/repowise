"""Name and order the groups. The model never decides which files they hold.

This is the half of the planner an LLM is good at, and it is bounded so that
the half it is bad at cannot leak back in. Groups arrive with opaque ids and
the model returns a title, a scope sentence and a section for each id. It is
never shown a blank canvas and never asked which directory belongs where, so
the two failures the probes measured — dropping directories and inventing
plausible-looking paths — have nowhere to enter through. A group the model
forgets to name keeps a deterministic name; a group id it invents is discarded.
Coverage is therefore unchanged by anything the model does or fails to do.

The file inventory conveys breadth, never importance. Ranking the input by
PageRank and sampling the top files produced 12.7% coverage against 91.7% for
the full inventory, because hub files are infrastructure — ``models.py``,
``client.ts``, ``__init__.py`` — and an outline built around them describes the
plumbing rather than the product. Every truncated field in the payload is
nonetheless ordered for what its truncation should keep — filenames round-robin
across directories, entry points by execution-start evidence — because the sort
in front of a ``[:n]`` decides what the model sees, and none of those orders is
centrality.

Repo docs do not come in here as prose. Nine thousand characters of README
injected alongside a 145-directory structural task collapsed the outline to
three pages. Vocabulary arrives already bound to a group by
:mod:`vocabulary`, as a short list of suggested names.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import structlog

from ..entry_points import rank_entry_point_paths
from ..nav_titles import recase
from ..onboarding.grounding import check_grounding
from .grouping import ConceptGroup

logger = structlog.get_logger(__name__)

MAX_TITLE_WORDS = 7

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


def _tail_title(directory: str) -> str:
    """The last one or two meaningful segments of *directory*, humanised."""
    segments = [s for s in directory.split("/") if s]
    meaningful = [s for s in segments if s.lower() not in _STOP_SEGMENTS] or segments
    return " ".join(_humanise(s) for s in meaningful[-2:]).strip()


# Directory names that say what kind of code is inside rather than what it is
# for. On their own they name nothing ("Utils"), so they keep the segment above.
_GENERIC_TAILS = frozenset(
    {
        "api", "base", "common", "components", "config", "helpers", "impl", "misc",
        "models", "shared", "types", "util", "utils", "views",
    }
)


def _subject_title(directory: str) -> str:
    """The directory's own name, with its parent's when its own is generic.

    The last segment is what a reader calls the directory ("Dead Code", not
    "Analysis Dead Code"); the parent only joins when the last segment names a
    kind of file rather than a subject. A collision between two same-named
    directories is the disambiguator's job, which qualifies by parent.
    """
    segments = [s for s in directory.split("/") if s]
    meaningful = [s for s in segments if s.lower() not in _STOP_SEGMENTS] or segments
    if not meaningful:
        return ""
    take = 2 if meaningful[-1].lower() in _GENERIC_TAILS and len(meaningful) > 1 else 1
    return " ".join(_humanise(s) for s in meaningful[-take:]).strip()


def sibling_runs(group: ConceptGroup) -> list[tuple[str, int]]:
    """``(directory, files)`` for a group gathered from sibling directories.

    The grouper merges thin sibling directories into one page and targets it
    at one of them, because a page id has to be a real directory. When that
    target does not contain the rest, it is not what the page is about:
    ``mcp_server/_savings`` held a sixth of a page that was mostly
    ``tool_answer`` and ``tool_health``, and the page was named after the
    savings. Counted per directory directly under the members' shared parent,
    largest first. Empty for a group its target contains.
    """
    target = group.target_path
    if all(m.startswith(target + "/") for m in group.members):
        return []
    parent = _common_prefix(group.dirs)
    cut = len(parent) + 1 if parent else 0
    counts: Counter[str] = Counter()
    for member in group.members:
        head, sep, _rest = member[cut:].partition("/")
        counts[f"{parent}/{head}".lstrip("/") if sep else parent] += 1
    if len(counts) < 2:
        return []
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def deterministic_title(group: ConceptGroup, layer_label: str = "") -> str:
    """A readable name from structure alone, for a run with no provider.

    The last one or two meaningful segments of the directory the group is
    mostly about, cased through :func:`~..nav_titles.recase`. Not as good as a
    named capability and not meant to be: per D5 the map is free and the prose
    needs a key, so a keyless wiki gets a real tree with plainer labels rather
    than a worse tree.

    A group gathered from sibling directories is named after the one holding
    the most files, never after two of them glued with "and": "Json and Sansio"
    reads as two subjects where the page has one main one, and the members are
    listed in the page itself. *layer_label* is kept so callers need not
    change, and no longer prefixes the name: "Application Flask" said nothing the layer
    badge does not, and a one-word name is a fine name.
    """
    del layer_label
    if group.packages:
        return recase(_package_rollup_title(group.packages))
    runs = sibling_runs(group)
    title = _subject_title(runs[0][0] if runs else group.target_path)
    if not title:
        # The repository root has no path segments to name it by. It is a real
        # place and a common one — top-level entry points live there — so it
        # gets a name of its own rather than one derived from an empty path.
        title = "Repository Root Files"
    words = title.split()
    if len(words) > MAX_TITLE_WORDS:
        title = " ".join(words[:MAX_TITLE_WORDS])
    return recase(title)


# A chapter's subject is the directory it heads. ``core`` is the one stop
# segment that survives here: as one of two segments it adds nothing ("Core
# Ingestion" says no more than "Ingestion"), but as the whole subject it is
# exactly what a reader means by the core package. The rest are containers with
# no subject of their own, and no path ever ends at one on purpose.
_CHAPTER_CONTAINERS = (_STOP_SEGMENTS - {"core"}) | {"dist", "build"}

# A route parameter: ``[id]``, ``[...slug]``, ``{id}``, ``(group)``, ``:id``.
# It names a variable rather than a subject, so a chapter at
# ``app/repos/[id]`` is about repos.
_DYNAMIC_SEGMENT = re.compile(r"^([\[{(].*[]})]|:.+)$")


def _chapter_subject(target_path: str) -> str:
    """The deepest segment of *target_path* that names something."""
    segments = [s for s in target_path.split("/") if s]
    if not segments:
        return ""
    for segment in reversed(segments):
        if segment.lower() in _CHAPTER_CONTAINERS or _DYNAMIC_SEGMENT.match(segment):
            continue
        return segment
    # Every segment is a container. The last one is still where the reader is.
    return segments[-1]


def _spelling_index(titles: Sequence[str]) -> dict[str, str]:
    """Lowercased word -> how this project spells it in its own page titles.

    The leaf titles are named before any chapter needs a name, so they are the
    one place that says whether this repository writes ``ui`` as "UI" or "Ui",
    or ``vscode`` as "VS Code" (two adjacent words count as one spelling of
    their concatenation). Taken from them rather than from an acronym list,
    which would be a rule tuned to the repositories whose acronyms happened to
    be on it. Without a model the titles are themselves derived from paths and
    the index only confirms what ``_humanise`` already wrote.
    """
    counts: dict[str, Counter[str]] = {}
    pairs: dict[str, Counter[str]] = {}
    for title in titles:
        words = [w for w in re.split(r"[^A-Za-z0-9]+", title) if w]
        # An all-lowercase occurrence says nothing about spelling — a title
        # carrying the word mid-sentence, or one quoting a path — and taking
        # it would open a page title in lower case.
        for w in words:
            if not w.islower():
                counts.setdefault(w.lower(), Counter())[w] += 1
        for a, b in pairwise(words):
            if not (a + b).islower():
                pairs.setdefault((a + b).lower(), Counter())[f"{a} {b}"] += 1
    # A pair only spells a word the titles never write as one word, so "Data
    # Base" cannot respell a project that also writes "Database".
    for key, c in pairs.items():
        counts.setdefault(key, c)
    # Ties break on the string so an unchanged repository names it the same
    # way every run.
    return {key: min(c, key=lambda s: (-c[s], s)) for key, c in counts.items()}


def _respell(phrase: str, spelling: dict[str, str]) -> str:
    """*phrase* with every word spelled the way *spelling* says the project does.

    The casing table has the last word (see :func:`~..nav_titles.recase`): a
    leaf title written "Ui Zoom" is not evidence that the project spells it
    "Ui".
    """
    return recase(" ".join(spelling.get(word.lower(), word) for word in phrase.split(" ")))


def chapter_title(target_path: str, titles: Sequence[str] = ()) -> str:
    """Name the chapter heading *target_path*, spelled like *titles*.

    A chapter is not a leaf and must not be named like one. The namer, shown a
    chapter's directory, names the loose files at that directory's root and
    then that name stands over everything below: "Blast Radius UI" heading
    eighteen UI modules, "Core Service Utilities" heading six core subsystems.
    That is worse than no chapter at all, because the name now claims to head
    things it does not describe. Measured, not assumed — a hint telling the
    model which groups were chapters and what they contained changed two names
    of nine and neither of those two.

    What a chapter reliably *is* is the place: the directory, and the reader's
    own word for it. So it gets that word, and the only thing the leaf titles
    are asked for is how this project spells it. No trailing "Overview": every
    chapter carried it, so a column of sibling rows repeated a word that told a
    reader nothing. Costs no call and cannot drift between runs.
    """
    segment = _chapter_subject(target_path)
    if not segment:
        # A chapter at the repository root. It has no segment to name it by and
        # the overview page already covers the repository, so say where it is
        # rather than inventing a subject.
        return "Repository Root"
    # A path of containers only (a root ``src``) is named for what it holds.
    subject = _CONTAINER_NOUNS.get(segment.lower()) or _humanise(segment)
    return _respell(recase(subject), _spelling_index(titles))


def chapter_titles(leaves: dict[str, str], chapters: Sequence[str]) -> dict[str, str]:
    """Title every directory in *chapters*, spelled like the leaf titles.

    *leaves* maps a leaf page's directory to its title — every page that owns
    files, including a directory that is both a chapter and a leaf. Titles are
    made unique against each other and against the leaves, which do not move: a
    leaf may carry a model-written name and a chapter yielding to it is the
    cheaper of the two collisions to resolve. The qualifier a collision adds is
    respelled too, so "Ui Health" reads "UI Health".
    """
    keys = set(chapters)
    titles = list(leaves.values())
    spelling = _spelling_index(titles)
    ordered = sorted(keys)
    named = disambiguate_titles(
        [(chapter_title(c, titles), c) for c in ordered],
        reserved={t for path, t in leaves.items() if path not in keys},
    )
    return dict(zip(ordered, (recase(_respell(t, spelling)) for t in named), strict=True))


def scc_where(members: list[str], *, max_parts: int = 2) -> str:
    """Where a dependency cycle lives, from its members' directories.

    A cycle page was titled by the hash of its member list — ``Circular
    Dependency: scc-0fa5536d91fc``. Nothing a reader could search for names a
    hash, which is why every one of them on this repository's index had no
    inbound link at all: the title said only that the page existed.

    Naming it after the place it happens in is the cheapest fix that keeps the
    page and its id. Structural and repo-agnostic: the directories the members
    share, or the two most-represented ones when they share nothing, which is
    the honest description of a cycle that spans subsystems.

    Returns an empty string when there is nothing to say, so the caller keeps
    the id rather than rendering a title that claims less than the id does.
    """
    dirs = [m.rsplit("/", 1)[0] for m in members if "/" in m]
    if not dirs:
        return ""

    common = _common_prefix(dirs)
    if common:
        # Everything is under one directory, so name that directory. The last
        # two segments for the same reason concept titles use them: the tail
        # carries the meaning and the head is usually a container.
        return _tail_title(common)

    # No shared root: the cycle crosses subsystems, which is the interesting
    # thing about it. Name the biggest two, in size order, ties on path so the
    # title does not move between runs on an unchanged repository.
    counts = Counter(d.split("/")[0] for d in dirs)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_parts]
    return " and ".join(_humanise(seg) for seg, _n in top)


def disambiguate_titles(
    titled: list[tuple[str, str]], *, reserved: set[str] | None = None
) -> list[str]:
    """Make a set of ``(title, target_path)`` pairs' titles unique.

    Path-derived names collide easily: two packages that each hold a ``ui``
    directory produce the same last-two-segments name. The pages themselves
    stay distinct because identity is structural, but a reader sees two
    identical rows in the tree and cannot tell which is which, and anything
    that keys on a title (interlinking, an overview listing) has to guess.

    A colliding title takes on the next path segment above the ones it
    already used, then two of them, and only if even that repeats a number.
    Never the path in parentheses: a disambiguator is part of a navigation
    label, and a path there reads as the tree leaking its plumbing. Input order does not matter: the pairs are resolved in path
    order, so the same set always produces the same names.

    *reserved* names are already taken by pages not in *titled*, so a caller
    naming a second population against a settled one yields to it rather than
    renaming it. Passing the settled titles in *titled* instead would let this
    rename them — and a caller that then keeps only its own slice silently
    discards those renames, leaving the collision it asked to resolve.

    Case is not a difference. "UI Overview" over "Ui Overview" is two rows a
    reader reads as one thing written twice, which is the whole condition this
    function exists to remove, and it is the reading the planner's own
    disambiguation has always taken.
    """
    order = sorted(range(len(titled)), key=lambda i: titled[i][1])
    out = list(titled)
    used: set[str] = {t.lower() for t in (reserved or ())}
    for i in order:
        title, target = titled[i]
        if title.lower() not in used:
            used.add(title.lower())
            out[i] = (title, target)
            continue
        candidate = next(
            (q for q in _qualified_titles(title, target) if q.lower() not in used),
            None,
        ) or next(
            f"{title} {n}" for n in range(2, len(titled) + 3) if f"{title} {n}".lower() not in used
        )
        used.add(candidate.lower())
        out[i] = (candidate, target)
    return [t for t, _ in out]


# What a container directory holds, for telling a chapter at one apart from
# the chapter it sits in: ``packages/ui/src`` under ``packages/ui`` is the UI
# source, ``web/src/lib`` under ``web`` is the web library.
_CONTAINER_NOUNS = {
    "src": "Source",
    "source": "Source",
    "lib": "Library",
    "app": "App",
    "internal": "Internal",
    "pkg": "Packages",
    "packages": "Packages",
    "dist": "Distribution",
    "build": "Build",
}


def _qualified_titles(title: str, target: str) -> list[str]:
    """Readable variants of *title* that say which *target* it is, nearest first.

    Nearest is the directory itself when it is a container: that is what
    separates a package's chapter from the chapter for its ``src`` ("UI
    Source"). Then the closest directory above that names something: never a
    container ("Src Core" reads as a path leaking into the title), never a
    route parameter, never a word the title already has ("Web Web"). Then the
    two closest together, for two packages that share both a name and a parent
    name.
    """
    segments = [s for s in target.split("/") if s]
    taken = {w.lower() for w in title.split()}
    out = _container_qualified(title, segments[-1] if segments else "", taken)
    words: list[str] = []
    for segment in reversed(segments[:-1]):
        if segment.lower() in _CHAPTER_CONTAINERS or _DYNAMIC_SEGMENT.match(segment):
            continue
        word = recase(_humanise(segment))
        if taken.isdisjoint(w.lower() for w in word.split()):
            out.append(f"{word} {title}")
            words.append(word)
    out.extend(f"{outer} {inner} {title}" for inner, outer in pairwise(words))
    return out


def _container_qualified(title: str, segment: str, taken: set[str]) -> list[str]:
    """*title* qualified by what the container *segment* holds, if it is one."""
    noun = _CONTAINER_NOUNS.get(segment.lower())
    if not noun or noun.lower() in taken:
        return []
    return [f"{title} {noun}"]


def _package_rollup_title(packages: Sequence[str]) -> str:
    """The shared parent plus a member list, never one member's name alone."""
    parent = _common_prefix(list(packages))
    lead = _humanise(_chapter_subject(parent)) or "Root"
    if not lead.lower().endswith("packages"):
        lead = f"{lead} Packages"
    names = [_humanise(p.rsplit("/", 1)[-1]) for p in sorted(packages)]
    more = f" and {len(names) - 2} More" if len(names) > 2 else ""
    title = f"{lead}: {names[0]}, {names[1]}{more}"
    if len(title.split()) <= MAX_TITLE_WORDS:
        return title
    return f"{lead} ({len(names)})"


def deterministic_scope(group: ConceptGroup) -> str:
    """A scope line stating coverage and its boundary, with no model."""
    if group.packages:
        names = [p.rsplit("/", 1)[-1] for p in group.packages]
        listed = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
        return (
            f"Covers {len(names)} small sibling packages ({listed}), "
            f"{group.file_count} source files in all. Each is its own package; "
            "code outside them is documented on its own pages."
        )
    # The root directory is the empty string, which is a real directory and
    # not an absent one; filtering it out left a group reading "0 directories".
    dirs = list(group.dirs)
    if len(dirs) == 1:
        where = dirs[0] or "the repository root"
    else:
        # Relative to what the directories share, not to ``target_path``. The
        # target is one of the group's directories and is not always a prefix
        # of the others — ``_assign_targets`` falls through to the first sorted
        # member when the true ancestor is not itself a member — so naming it
        # here produced "4 directories under src/lib/a" directly above a
        # membership line holding three directories that are not under it. The
        # sentence falsified itself against the line above.
        common = _common_prefix(dirs)
        where = f"{len(dirs)} directories" + (f" under {common}" if common else "")
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
        runs = sibling_runs(group)
        if runs:
            # The target is one sibling among several and not what the page is
            # about, so the model is shown where the files actually are, with
            # how many each holds. Shown only the target, it named a page that
            # was mostly answer and health tools "MCP Answer Economics".
            parent = _common_prefix(group.dirs)
            cut = len(parent) + 1 if parent else 0
            entry["dir"] = parent or "."
            entry["subdirs"] = [f"{d[cut:] or '.'} ({n} files)" for d, n in runs[:8]]
        elif len(group.dirs) > 1:
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
        if group.packages:
            # Sections only: the title stays the deterministic parent + members.
            entry["packages"] = [p.rsplit("/", 1)[-1] for p in group.packages]
        label = labels.get(group.dominant_layer)
        if label:
            entry["layer"] = label
        summary = mod_summaries.get(group.target_path)
        if summary:
            entry["summary"] = summary.replace("\n", " ")[:200]
        hint = hints.get(gid)
        if hint:
            entry["suggested_name"] = hint
        # Ranked, not alphabetical by basename: this is truncated to three, so
        # the sort decides which three the namer ever sees. Basenames are taken
        # after ranking for the reason the docstring gives — a full path is
        # something the model would quote back as a citation.
        members = set(group.members)
        group_entries = rank_entry_point_paths(e for e in entries if e in members)
        if group_entries:
            entry["entry_points"] = [e.rsplit("/", 1)[-1] for e in group_entries[:3]]
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
    return recase(title)


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
        if group.packages:
            # A model shown several packages names the page after one of them.
            title, scope = "", ""
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
