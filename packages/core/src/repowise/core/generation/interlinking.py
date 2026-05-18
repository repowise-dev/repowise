"""Post-generation interlinking — resolve refs to wiki page IDs.

After every page is generated, this module scans the markdown for
backtick-quoted references to files / symbols and resolves them to
``page_id``s of *other* generated pages. Resolved hits land in
``page.metadata["wiki_links"]``; the reverse index is materialized as
``page.metadata["backlinks"]`` on each target. The frontend MDX
preprocessor reads these maps to render anchor tags.

No LLM call — pure regex + dict lookup. Runs in roughly
``O(N_pages * avg_refs_per_page)`` and is therefore cheap enough to
run on every generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from .models import GeneratedPage, compute_page_id

log = structlog.get_logger(__name__)


# Backtick-wrapped identifier-ish strings: file paths, symbol names,
# qualified names. The same expression used by the hallucination
# validator — we now repurpose it as a *resolver* instead of a *warner*.
_BACKTICK_REF_RE = re.compile(r"(?<!`)` *([A-Za-z_/.][\w./\-]*?) *`(?!`)")

# Markdown code fence — content inside is verbatim source, not refs.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


# ---------------------------------------------------------------------------
# Link index
# ---------------------------------------------------------------------------


@dataclass
class LinkIndex:
    """Lookup tables that map a ref string to a target page_id.

    Built once per generation run; queried per page.
    """

    by_path: dict[str, str] = field(default_factory=dict)
    by_basename: dict[str, str] = field(default_factory=dict)
    by_symbol_qname: dict[str, str] = field(default_factory=dict)
    by_target: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        pages: list[GeneratedPage],
        parsed_files: list[Any] | None = None,
    ) -> LinkIndex:
        """Compose lookup tables from generated pages + parsed files."""
        idx = cls()

        # Path / target → page_id. ``target_path`` is the canonical key
        # for non-symbol pages; file_page targets are file paths.
        for page in pages:
            if not page.target_path:
                continue
            idx.by_target[page.target_path] = page.page_id
            if page.page_type in {"file_page", "api_contract", "infra_page"}:
                idx.by_path[page.target_path] = page.page_id
                base = Path(page.target_path).name
                # Don't overwrite — first wins to avoid ambiguous basename collisions.
                idx.by_basename.setdefault(base, page.page_id)
            elif page.page_type == "symbol_spotlight":
                # target_path is "file::name" — map the qualified form
                if "::" in page.target_path:
                    idx.by_symbol_qname.setdefault(
                        page.target_path, page.page_id
                    )

        # Augment with symbol qualified_names from the parser even when
        # the symbol itself didn't get a spotlight — the link resolves
        # to its host file_page instead.
        if parsed_files:
            for pf in parsed_files:
                host_page = idx.by_path.get(pf.file_info.path)
                if not host_page:
                    continue
                for sym in pf.symbols:
                    qname = getattr(sym, "qualified_name", "") or sym.name
                    idx.by_symbol_qname.setdefault(qname, host_page)
                    # Last-segment match for `Foo.bar` → `bar`
                    if "." in qname:
                        idx.by_symbol_qname.setdefault(
                            qname.rsplit(".", 1)[-1], host_page
                        )

        return idx

    def resolve(self, ref: str) -> str | None:
        """Resolve a single ref to a ``page_id``; ``None`` if unmatched."""
        if not ref:
            return None
        # Direct page-target hit (highest confidence).
        if ref in self.by_target:
            return self.by_target[ref]
        # File path hit.
        if ref in self.by_path:
            return self.by_path[ref]
        # Basename file hit (e.g. "wiki.py" → packages/.../wiki.py).
        if "/" not in ref and "." in ref and ref in self.by_basename:
            return self.by_basename[ref]
        # Symbol qualified-name hit.
        if ref in self.by_symbol_qname:
            return self.by_symbol_qname[ref]
        return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WikiLink:
    anchor: str  # the user-visible text matched by the ref
    target_page_id: str
    kind: str  # "file" | "symbol" | "page"


def _strip_code_fences(content: str) -> str:
    """Drop fenced code blocks so source examples don't generate refs."""
    return _FENCE_RE.sub("", content)


def resolve_wiki_links(
    page: GeneratedPage,
    index: LinkIndex,
    *,
    max_links_per_page: int = 50,
) -> list[WikiLink]:
    """Return the set of resolved wiki links for *page*.

    Deduplicates by ``target_page_id`` — a page that mentions
    ``foo/bar.py`` ten times yields one link, not ten.
    """
    text = _strip_code_fences(page.content or "")
    seen_targets: set[str] = set()
    links: list[WikiLink] = []
    for raw_ref in _BACKTICK_REF_RE.findall(text):
        ref = raw_ref.strip()
        target = index.resolve(ref)
        if target is None or target == page.page_id:
            continue
        if target in seen_targets:
            continue
        seen_targets.add(target)
        kind = "file" if "/" in ref or ref.endswith(
            (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java")
        ) else "symbol"
        links.append(WikiLink(anchor=ref, target_page_id=target, kind=kind))
        if len(links) >= max_links_per_page:
            break
    return links


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def attach_wiki_links_and_backlinks(
    pages: list[GeneratedPage],
    parsed_files: list[Any] | None = None,
) -> None:
    """Populate ``metadata['wiki_links']`` and ``metadata['backlinks']``.

    Mutates each :class:`GeneratedPage` in place. Idempotent and safe
    to call on a partially-populated page set — pages with no resolved
    refs simply get empty lists.
    """
    if not pages:
        return

    index = LinkIndex.build(pages, parsed_files)
    by_id: dict[str, GeneratedPage] = {p.page_id: p for p in pages}

    # First pass — resolve forward links per page.
    for page in pages:
        wiki_links = resolve_wiki_links(page, index)
        page.metadata["wiki_links"] = [
            {"anchor": w.anchor, "target_page_id": w.target_page_id, "kind": w.kind}
            for w in wiki_links
        ]

    # Second pass — build the reverse index. ``backlinks`` on each target
    # lists the source pages that linked to it, capped at a reasonable
    # number for UI rendering.
    backlink_map: dict[str, list[dict]] = {pid: [] for pid in by_id}
    for page in pages:
        forward = page.metadata.get("wiki_links") or []
        for link in forward:
            target_id = link.get("target_page_id")
            if not target_id or target_id not in backlink_map:
                continue
            backlink_map[target_id].append(
                {
                    "source_page_id": page.page_id,
                    "source_title": page.title,
                    "source_page_type": page.page_type,
                    "anchor": link.get("anchor", ""),
                }
            )

    _BACKLINK_CAP = 25
    for page_id, sources in backlink_map.items():
        if not sources:
            by_id[page_id].metadata["backlinks"] = []
            continue
        # Dedup by source_page_id — a single source linking via 5
        # different anchors counts once.
        seen: set[str] = set()
        unique: list[dict] = []
        for entry in sources:
            sid = entry["source_page_id"]
            if sid in seen:
                continue
            seen.add(sid)
            unique.append(entry)
            if len(unique) >= _BACKLINK_CAP:
                break
        by_id[page_id].metadata["backlinks"] = unique

    log.info(
        "wiki_links.resolved",
        pages=len(pages),
        total_forward_links=sum(
            len(p.metadata.get("wiki_links") or []) for p in pages
        ),
        pages_with_backlinks=sum(
            1 for p in pages if p.metadata.get("backlinks")
        ),
    )


__all__ = [
    "LinkIndex",
    "WikiLink",
    "attach_wiki_links_and_backlinks",
    "resolve_wiki_links",
]


# Re-export for type hints
_ = compute_page_id
