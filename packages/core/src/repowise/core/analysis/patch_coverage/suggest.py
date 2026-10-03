"""Suggested path-scoped gates (``coverage.gates``) for ``repowise coverage suggest-gates``.

Pure: each source takes what the command read (CODEOWNERS text, the files git
tracks, each file's graph community) and returns gate proposals. Nothing here
writes config or picks a threshold; the user keeps what they want and sets
``fail_under`` themselves.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ...ingestion.languages.registry import REGISTRY
from ...test_paths import is_test_related_path

#: Where GitHub and GitLab look for CODEOWNERS; the first found is read.
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")

#: Directories whose children are a monorepo's packages.
MONOREPO_ROOTS = ("packages", "apps", "services", "libs")

#: A graph community with fewer source files than this is not worth a gate.
MIN_COMMUNITY_FILES = 3

#: Largest communities suggested; the rest are counted in a note.
COMMUNITY_LIMIT = 10

# CODEOWNERS patterns every file matches: a gate on them is the whole change.
_CATCH_ALL = frozenset({"*", "**", "/**", "/*"})

_SOURCE_SUFFIXES = REGISTRY.non_infra_code_extensions()

# A ``#`` at the start of a line or after whitespace (space or tab) opens a comment.
_COMMENT = re.compile(r"(?:^|\s)#.*$")
# Fields split on whitespace a backslash does not escape (``docs/my\ file.md``).
_FIELDS = re.compile(r"(?<!\\)\s+")
# A GitLab section header, ``[Name]`` or ``^[Name][2]``, with optional default owners.
_SECTION = re.compile(r"^\^?\[[^\]]+\](?:\[\d+\])?(?:\s+(.*))?$")
_GLOB_CHARS = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class SuggestedGate:
    name: str
    paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "paths": list(self.paths)}


@dataclass
class GateSource:
    """One source of suggestions: its label, where it read from, and its gates."""

    source: str  # "codeowners" | "layout" | "graph"
    detail: str  # what was read, or why nothing was
    gates: list[SuggestedGate] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "detail": self.detail,
            "gates": [g.to_dict() for g in self.gates],
        }


def codeowners_gates(text: str) -> list[SuggestedGate]:
    """One gate per owner, over the patterns that name them.

    CODEOWNERS gives a file to the last pattern matching it, and gitignore
    globs let a later pattern win the same way, so a later pattern that may
    overlap an owner's paths (narrower or broader) and is not theirs, owner-less
    lines included, is carried as a ``!`` exclusion. A catch-all overrides
    every earlier pattern, so it drops the gates before it, and is itself no
    gate: a gate on every file is the whole-change gate. An owner whose every
    pattern a later one fully overrides gets no gate. Under a GitLab
    ``[Section] @owners`` header, owner-less lines take the section's owners.
    """
    paths: dict[str, list[str]] = {}
    for pattern, owners in _codeowners_rules(text):
        if pattern in _CATCH_ALL:
            paths.clear()
            continue
        for owner, owned in paths.items():
            if owner not in owners and any(_may_overlap(p, pattern) for p in owned):
                owned.append(f"!{pattern}")
        for owner in owners:
            paths.setdefault(owner, []).append(pattern)
    return [
        SuggestedGate(_gate_name(owner), tuple(owned))
        for owner, owned in paths.items()
        if _any_live(owned)
    ]


def _codeowners_rules(text: str) -> Iterator[tuple[str, list[str]]]:
    """``(pattern, owners)`` per rule line; owner-less lines take a GitLab section's owners."""
    defaults: list[str] = []
    for raw in text.splitlines():
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        if section := _SECTION.match(line):
            defaults = _FIELDS.split(section.group(1)) if section.group(1) else []
            continue
        pattern, *owners = _FIELDS.split(line)
        yield pattern, owners or defaults


def layout_gates(tracked: Iterable[str]) -> list[SuggestedGate]:
    """One gate per package of a monorepo, else per top-level source directory.

    A directory counts when git tracks a source file under it that is not a
    test. The monorepo layout wins when any of its roots holds a package.
    """
    sources = [p for p in tracked if _is_source(p)]
    packages = sorted(
        {
            "/".join(parts[:2])
            for parts in (PurePosixPath(p).parts for p in sources)
            if len(parts) > 2 and parts[0] in MONOREPO_ROOTS
        }
    )
    if packages:
        # ``apps/web`` and ``packages/web`` both exist: name them by their root too.
        leaves = [d.split("/")[1] for d in packages]
        return [
            SuggestedGate(d.replace("/", "-") if leaves.count(leaf) > 1 else leaf, (f"/{d}/",))
            for d, leaf in zip(packages, leaves, strict=True)
        ]
    top = sorted(
        {
            parts[0]
            for parts in (PurePosixPath(p).parts for p in sources)
            if len(parts) > 1 and not parts[0].startswith(".")
        }
    )
    return [SuggestedGate(d, (f"/{d}/",)) for d in top]


def community_gates(
    communities: Mapping[str, int], sizes: Mapping[str, int] | None = None
) -> tuple[list[SuggestedGate], int]:
    """Gates from graph communities (``{file: community_id}``), and how many were not tried.

    Largest first, each community with at least :data:`MIN_COMMUNITY_FILES`
    source files becomes a gate over its repository-root files and the
    directories holding the rest, named by their longest common directory, or
    by its largest file's stem (*sizes*, bytes) when that is the root. A
    directory an earlier gate lists is not listed again, and a gate left with
    fewer than :data:`MIN_COMMUNITY_FILES` of its own files is dropped.
    Ceiling: a directory still holds other communities' files, since a glob
    names directories, not the graph's split.
    """
    members: dict[int, list[str]] = defaultdict(list)
    for path, community in communities.items():
        if _is_source(path):
            members[community].append(path)
    big = sorted(
        (sorted(files) for files in members.values() if len(files) >= MIN_COMMUNITY_FILES),
        key=lambda files: (-len(files), files[0]),
    )
    gates: list[SuggestedGate] = []
    claimed: list[str] = []
    tried = 0
    for files in big:
        if len(gates) == COMMUNITY_LIMIT:
            break
        tried += 1
        if gate := _community_gate(files, claimed, sizes or {}):
            gates.append(gate)
    return gates, len(big) - tried


def _community_gate(
    files: list[str], claimed: list[str], sizes: Mapping[str, int]
) -> SuggestedGate | None:
    """One community's gate over what no earlier gate claimed, or ``None`` when too little is left."""
    dirs = [
        d
        for d in _outermost(posixpath.dirname(f) for f in files if posixpath.dirname(f))
        if not _under(d, claimed)
    ]
    loose = [f for f in files if not posixpath.dirname(f)]
    if len(loose) + sum(_under(f, dirs) for f in files) < MIN_COMMUNITY_FILES:
        return None
    claimed.extend(dirs)
    globs = [f"/{f}" for f in loose] + [f"/{d}/" for d in dirs]
    return SuggestedGate(_community_name(files, sizes), tuple(globs))


def _community_name(files: list[str], sizes: Mapping[str, int]) -> str:
    """The longest common directory's name, else the largest file's stem."""
    dirs = {posixpath.dirname(f) for f in files}
    common = "" if "" in dirs else posixpath.commonpath(sorted(dirs))
    if common:
        return PurePosixPath(common).name
    largest = min(files, key=lambda f: (-sizes.get(f, 0), f))
    return PurePosixPath(largest).stem


def _under(path: str, dirs: Iterable[str]) -> bool:
    """Whether *path* is one of *dirs* or inside one."""
    return any(path == d or path.startswith(d + "/") for d in dirs)


def unique_names(sources: Sequence[GateSource]) -> None:
    """Rename repeats across *sources* (``core``, ``core-2``), so the YAML pastes valid."""
    seen: set[str] = set()
    for src in sources:
        for i, gate in enumerate(src.gates):
            name, n = gate.name, 2
            while name in seen:
                name, n = f"{gate.name}-{n}", n + 1
            seen.add(name)
            src.gates[i] = SuggestedGate(name, gate.paths)


def render_yaml(sources: Sequence[GateSource]) -> str:
    """A ``gates:`` block, indented to paste directly below a ``coverage:`` line.

    Each source is labelled in a comment. Strings are written as JSON, which
    YAML reads as-is, so a glob starting with ``*`` or ``!`` needs no quoting.
    """
    body: list[str] = []
    for src in sources:
        body.append(f"    # {_SOURCE_TITLE[src.source]}: {src.detail}")
        for gate in src.gates:
            body.append(f"    - name: {json.dumps(gate.name)}")
            body.append(f"      paths: {json.dumps(list(gate.paths))}")
    empty = not any(src.gates for src in sources)
    return "\n".join(
        [
            "# Suggested path-scoped gates. Paste directly below your `coverage:` line in",
            "# .repowise/config.yaml, keep the ones you want and give each a fail_under (0-100).",
            "  gates: []" if empty else "  gates:",
            *body,
        ]
    ) + "\n"


_SOURCE_TITLE = {
    "codeowners": "From CODEOWNERS",
    "layout": "From top-level packages",
    "graph": "From graph communities",
}


def _is_source(path: str) -> bool:
    return PurePosixPath(path).suffix in _SOURCE_SUFFIXES and not is_test_related_path(path)


def _gate_name(owner: str) -> str:
    """``@org/team`` -> ``org-team``; an email owner keeps its local part."""
    return owner.lstrip("@").split("@", 1)[0].replace("/", "-")


def _may_overlap(owned: str, later: str) -> bool:
    """Whether two patterns could match one file, either the broader. Errs towards yes.

    Exclusions (``!``) in *owned* never need excluding again.
    """
    if owned.startswith("!"):
        return False
    a, b = _anchor(owned), _anchor(later)
    if not a or not b:  # floating, or anchored at the root with a glob
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _any_live(globs: Sequence[str]) -> bool:
    """Whether some pattern in *globs* is not fully overridden by a later ``!`` exclusion."""
    return any(
        not p.startswith("!")
        and not any(g.startswith("!") and _covers(g[1:], p) for g in globs[i + 1 :])
        for i, p in enumerate(globs)
    )


def _covers(later: str, earlier: str) -> bool:
    """Whether literal *later* matches every file *earlier* does (a directory above it)."""
    top, below = _anchor(later), _anchor(earlier)
    if not top or below is None or _GLOB_CHARS.search(later):
        return False
    return below == top or below.startswith(top + "/")


def _anchor(pattern: str) -> str | None:
    """The literal directory (or file) a pattern is confined to; ``None`` when it floats.

    Gitignore rules: a pattern with a ``/`` before its last character is
    anchored at the root, one without matches at any depth. ``""`` is an
    anchored pattern whose first path segment is already a glob.
    """
    core = pattern.rstrip("/")
    if "/" not in core:
        return None
    core = core.lstrip("/")
    head = _GLOB_CHARS.split(core, maxsplit=1)[0]
    if head == core:
        return core
    return head.rsplit("/", 1)[0] if "/" in head else ""


def _outermost(dirs: Iterable[str]) -> list[str]:
    """*dirs* without any whose ancestor is also listed."""
    kept: list[str] = []
    for d in sorted(set(dirs)):
        if not any(d.startswith(k + "/") for k in kept):
            kept.append(d)
    return kept
