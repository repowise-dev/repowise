"""The repository's own documentation, cut into cited sections.

The front page and the onboarding pages are written for someone who has never
opened the repository, and the repository's own README and user guides are
where it already explains itself to that person. They reach the model as
sections, one per heading, so a sentence can cite the lines that support it rather than the whole
README.

Two rankings pick sections for a page: :func:`rank_sections` by phrases a
section uses (install, quickstart, a program's name), and
:func:`rank_by_overlap` by how much vocabulary a section shares with a set of
walkthroughs. Both read only documentation paths (:func:`is_doc_path`) and stop
at a character cap, so the material stays near the size the pages were tuned on.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from ..flow_entries import DocSection, doc_files, split_sections

#: A file larger than this is not a page of prose (a generated reference dump).
_MAX_DOC_BYTES = 200_000

#: Directories whose documents are written for people using the project.
USER_DOC_DIRS = frozenset(
    {"start", "getting-started", "getting_started", "guide", "guides", "reference", "usage"}
)
_DOC_ROOTS = frozenset({"docs", "doc", "documentation"})
_SKIP_SEGMENTS = frozenset({"node_modules", "tests", "test", "fixtures", "vendor"})
_README_NAMES = frozenset({"readme.md", "readme.rst"})
#: Lines that open most READMEs and say nothing: badges, images, raw HTML,
#: link definitions.
_DECORATION = re.compile(
    r"\s*(\[[^\]]+\]:\s*\S+|\[!\[|!\[|<img|<a\s|<p\s|</?p>|</?div|<picture|</?source|</?h\d)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z_][a-z0-9_]{3,}")
_STOP = frozenset(
    ["the", "and", "for", "with", "that", "this", "from", "into", "when", "then", "than", "each", "which", "what", "where", "while", "are", "was", "were", "will", "can", "has", "have", "its", "not", "but", "all", "any", "one", "two", "use", "uses", "used", "using", "also", "only", "more", "most", "their", "there", "these", "those", "they", "them", "does", "done", "page", "pages", "walkthrough", "stage", "stages", "step", "steps", "user", "users"]
)


def is_readme(path: str) -> bool:
    return path.lower() in _README_NAMES


def is_doc_path(path: str) -> bool:
    """Project documentation: the root README, a top-level docs dir, CONTRIBUTING."""
    low = path.lower()
    top = low.split("/", 1)[0]
    return (
        is_readme(path)
        or top in _DOC_ROOTS
        or (low.endswith(("contributing.md", "contributing.rst")) and low.count("/") <= 1)
    )


def is_user_doc(path: str) -> bool:
    """A document written for someone using the project, not developing it."""
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        is_readme(path)
        or bool(set(parts[:-1]) & (USER_DOC_DIRS - {"reference"}))
        or name.startswith(("quickstart", "quick-start", "quick_start", "getting", "install", "tutorial"))
    )


def is_dev_doc(path: str) -> bool:
    """A document about working on the repository itself."""
    low = path.lower()
    return any(k in low for k in ("contributing", "develop", "hacking", "local_dev"))


def _wanted(path: str) -> bool:
    low = path.lower()
    if not low.endswith((".md", ".rst")) or not is_doc_path(path):
        return False
    name = low.rsplit("/", 1)[-1]
    if "changelog" in name or name.startswith(("changes", "history", "release")):
        return False
    return not any(seg in _SKIP_SEGMENTS for seg in low.split("/"))


def doc_sources(repo_path: Path | None, source_map: Mapping[str, bytes]) -> dict[str, bytes]:
    """The documentation files: parsed ones from *source_map*, plus the rest read from disk.

    Ingestion parses Markdown but not reStructuredText, and a project documented
    in ``.rst`` (a Sphinx site) would otherwise reach the pages with its README
    alone. The files on disk are the ones :func:`flow_entries.doc_files` finds,
    the same set the walkthrough pages quote.
    """
    docs = {p: b for p, b in source_map.items() if _wanted(p)}
    if repo_path is None or not repo_path.is_dir():
        return docs
    for rel in doc_files(repo_path):
        if rel not in docs and _wanted(rel):
            data = _read_small(repo_path / rel)
            if data is not None:
                docs[rel] = data
    return docs


def _read_small(file: Path) -> bytes | None:
    try:
        return file.read_bytes() if file.stat().st_size <= _MAX_DOC_BYTES else None
    except OSError:
        return None


def doc_sections(source_map: Mapping[str, bytes]) -> list[DocSection]:
    """Every documentation section in *source_map*, in path order."""
    out: list[DocSection] = []
    for path in sorted(source_map):
        if _wanted(path):
            text = source_map[path].decode("utf-8", errors="replace")
            out.extend(split_sections(path, text.splitlines()))
    return out


def clip(section: DocSection, max_chars: int) -> DocSection:
    """*section* cut to *max_chars*, its span ending at the last line kept."""
    if len(section.text) <= max_chars:
        return section
    text = section.text[:max_chars].rsplit("\n", 1)[0]
    return replace(section, text=text + "\n[...]", end=section.start + text.count("\n"))


def _fill(scored: Iterable[DocSection], char_cap: int, per_section: int) -> list[DocSection]:
    picked, used = [], 0
    for section in scored:
        cut = clip(section, per_section)
        if used + len(cut.text) > char_cap:
            continue
        picked.append(cut)
        used += len(cut.text)
    return picked


def rank_sections(
    sections: Iterable[DocSection],
    phrases: Iterable[str],
    *,
    heading_words: Iterable[str] = (),
    path_filter: Callable[[str], bool] = is_doc_path,
    char_cap: int = 12000,
    per_section: int = 3600,
) -> list[DocSection]:
    """Sections that use *phrases* most, user-facing documents first, under a cap."""
    phrases = [p.lower() for p in phrases]
    heading_words = [w.lower() for w in heading_words]
    scored: list[tuple[float, DocSection]] = []
    for s in sections:
        # A heading with nothing under it says nothing a page can use.
        if not path_filter(s.path) or "\n" not in s.text.strip():
            continue
        low, head = s.text.lower(), s.heading.lower()
        score = sum(low.count(p) for p in phrases) + 3 * sum(p in head for p in phrases)
        score += 4 * sum(w in head for w in heading_words)
        if not score:
            continue
        if is_user_doc(s.path):
            score *= 1.5
        scored.append((score / math.sqrt(len(s.text) / 800 + 1), s))
    scored.sort(key=lambda x: (-x[0], x[1].path, x[1].start))
    return _fill((s for _, s in scored), char_cap, per_section)


def terms(text: str) -> set[str]:
    """Content words of *text*, for overlap ranking."""
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def rank_by_overlap(
    sections: Iterable[DocSection],
    query: str,
    *,
    strong: set[str] = frozenset(),
    boost_words: tuple[str, ...] = (),
    char_cap: int = 12000,
    per_section: int = 2400,
    floor: float = 0.5,
) -> list[DocSection]:
    """Sections ranked by idf-weighted overlap with *query*'s vocabulary.

    *strong* holds the identifiers of the walkthroughs (traced function names,
    command words): a section must name one to qualify, and each counts three
    times. Normalised by the section's distinct-term count, and cut below
    *floor* of the best score so a weak tail does not fill the cap.
    """
    docs = [(s, terms(s.text)) for s in sections if is_doc_path(s.path)]
    df = Counter(t for _, ts in docs for t in ts)
    idf = {t: math.log(len(docs) / k) for t, k in df.items()}
    wanted = terms(query) | set(strong)
    scored: list[tuple[float, DocSection]] = []
    for s, ts in docs:
        hit, strong_hit = wanted & ts, strong & ts
        if not hit or (strong and not strong_hit):
            continue
        weight = sum(idf[t] for t in hit) + 2 * sum(idf[t] for t in strong_hit)
        boost = 1.5 if any(w in f"{s.path} {s.heading}".lower() for w in boost_words) else 1.0
        scored.append((boost * weight / math.sqrt(len(ts) + 20), s))
    if not scored:
        return []
    scored.sort(key=lambda x: (-x[0], x[1].path, x[1].start))
    top = scored[0][0]
    return _fill((s for score, s in scored if score >= floor * top), char_cap, per_section)


def readme_intro(sections: Iterable[DocSection], char_cap: int = 6000) -> list[DocSection]:
    """The root README from the top, one section per heading, decoration removed.

    Each section keeps its own span, so a sentence about the quickstart cites
    the quickstart's lines rather than the README as a whole.
    """
    readme = [s for s in sections if is_readme(s.path)]
    out, used = [], 0
    for s in readme:
        # Cut first, then drop decoration, so the span still counts real lines.
        cut = clip(s, char_cap - used)
        kept = [ln for ln in cut.text.splitlines() if not _DECORATION.match(ln)]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        if text and text != "[...]":
            out.append(replace(cut, text=text))
            used += len(text)
        if cut.text != s.text or used >= char_cap:
            break  # cut mid-document: what follows was not read
    return out
