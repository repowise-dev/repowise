"""Architecture Decision Record discovery and section parsing.

ADRs follow the Nygard/MADR templates, so a structured file is parsed
without a model; see :meth:`DecisionExtractor.discover_adrs`.
"""

from __future__ import annotations

import re
from pathlib import Path

from repowise.core.fs_walk import walk_repo
from repowise.core.ingestion.traverser import load_gitignore_spec

from .source_files import _SKIP_DIRS

# Conventional ADR homes, as repo-root-relative posix directories. Every ``.md``
# directly inside one is a candidate regardless of filename; matching is on the
# whole relative dir, so a stray ``vendor/x/adr/`` does not qualify.
_ADR_DIRS = frozenset(
    {
        "adr",
        "adrs",
        "docs/adr",
        "docs/adrs",
        "docs/decisions",
        "decisions",
        "architecture",
        "doc/adr",
    }
)
_MAX_ADR_FILES = 60

# Nygard/MADR section headings. Mapped to the ExtractedDecision fields they
# populate during the deterministic (LLM-free) parse.
_ADR_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_ADR_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ADR_STATUS_MAP = {
    "accepted": "active",
    "approved": "active",
    "active": "active",
    "proposed": "proposed",
    "draft": "proposed",
    "rejected": "deprecated",
    "deprecated": "deprecated",
    "superseded": "superseded",
}


def find_adr_files(repo_path: Path) -> list[Path]:
    """Collect candidate ADR files from the conventional dirs + name match.

    One :func:`walk_repo` pass answers both halves: files directly under a
    conventional ADR directory (root-anchored, as the old shallow globs
    were) rank ahead of loose ``*adr*.md`` name matches, and the cap is
    applied to the two buckets in that order.

    Ignored paths are excluded. :mod:`repowise.core.fs_walk` prunes junk
    dirs and nested repos but deliberately reads no ignore files, so
    gitignore/``info/exclude`` handling is this function's job: without it
    a scratch dir that ``git status`` cannot see contributes ``active``
    decision records citing files no clone of the repo contains.
    """
    ignore = load_gitignore_spec(repo_path)
    conventional: list[Path] = []
    loose: list[Path] = []
    for dirpath, dirnames, filenames in walk_repo(repo_path, prune_dirs=_SKIP_DIRS):
        rel_dir = dirpath.relative_to(repo_path).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        # Prune ignored and packaging-metadata subtrees in place. Ignored
        # DIRECTORIES match with a trailing slash, as git matches them.
        dirnames[:] = [
            d
            for d in dirnames
            if not d.endswith((".egg-info", ".dist-info"))
            and not ignore.match_file(f"{rel_dir}/{d}/" if rel_dir else f"{d}/")
        ]

        in_adr_dir = rel_dir in _ADR_DIRS
        for fname in filenames:
            bucket = _adr_bucket(fname, in_adr_dir, conventional, loose)
            if bucket is None:
                continue
            if ignore.match_file(f"{rel_dir}/{fname}" if rel_dir else fname):
                continue
            bucket.append(dirpath / fname)

        if len(conventional) + len(loose) >= _MAX_ADR_FILES:
            break

    # No dedup pass: walk_repo yields each directory once, and each file
    # lands in exactly one bucket.
    return [*conventional, *loose][:_MAX_ADR_FILES]


def _adr_bucket(
    fname: str, in_adr_dir: bool, conventional: list[Path], loose: list[Path]
) -> list[Path] | None:
    """Which candidate list *fname* belongs in, or None if it is no ADR."""
    low = fname.lower()
    if not low.endswith(".md"):
        return None
    if in_adr_dir:
        return conventional
    if "adr" in low and low != "readme.md" and "template" not in low:
        return loose
    return None


def read_front_matter(content: str) -> tuple[str, str, str]:
    """``(status, title, body)`` from an ADR's optional YAML front matter.

    Status and title are empty when the front matter does not set them, and
    the body is the document after the front matter (all of it when absent).
    """
    status = ""
    title = ""
    fm = _ADR_FRONTMATTER_RE.match(content)
    if not fm:
        return status, title, content
    for line in fm.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip().lower()
        val = v.strip().strip("\"'")
        if key == "status":
            status = val
        elif key == "title":
            title = val
    return status, title, content[fm.end() :]


def split_headings(text: str) -> dict[str, str]:
    """Map lowercased markdown headings to their section bodies."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _ADR_HEADING_RE.match(line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def bullets(text: str) -> list[str]:
    """Extract markdown bullet items from a section body."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("-", "*", "+")):
            item = s[1:].strip()
            if item:
                out.append(item)
    return out
