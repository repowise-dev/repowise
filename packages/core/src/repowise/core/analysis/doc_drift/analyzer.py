"""The documentation drift pass.

Reads markdown the pipeline already decoded, extracts the assertions each
document makes about the repository, resolves them against the real tree, and
reports the ones the tree no longer satisfies.

Shaped like :class:`~repowise.core.analysis.dead_code.analyzer.DeadCodeAnalyzer`:
a synchronous class taking its inputs at construction and exposing
``analyze(config, *, on_step)``. The pipeline wraps it in ``asyncio.to_thread``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import structlog

from .constants import (
    DEFAULT_MIN_CONFIDENCE,
    MANIFEST_NAMES,
    MAX_DOC_BYTES,
    bucket_confidences,
)
from .extractor import extract_with_suppressed, is_checkable_document, is_guide_document
from .models import (
    DocDriftFindingData,
    DocDriftReport,
    DriftKind,
    DriftVerdict,
    ResolvedDocReference,
)
from .resolver import RepoIndex, Resolution, resolve
from .suggest import RenameLookup, Suggestion, git_renames, suggest_all

logger = structlog.get_logger(__name__)

_REASONS: dict[str, str] = {
    "path_no_candidate": "Document names {target}, which no longer exists.",
    "path_no_candidate_in_guide": (
        "Document names {target}, which does not exist. This is a guide, so it "
        "may be an illustration rather than drift."
    ),
    "anchor_no_heading": "Link points at #{fragment}, which {doc} no longer declares.",
    "command_no_target": "Document shows `{raw}`, but no such target is declared.",
}


class DocDriftAnalyzer:
    """Find assertions in a repository's own markdown that the tree refutes."""

    def __init__(
        self,
        repo_id: str = "",
        source_map: dict[str, bytes] | None = None,
        tracked_paths: set[str] | frozenset[str] | None = None,
        *,
        repo_root: Path | None = None,
        rename_lookup: RenameLookup | None = None,
        on_disk: Callable[[str], bool] | None = None,
        opaque_dirs: frozenset[str] | None = None,
    ) -> None:
        """
        Args:
            repo_id: repository this report speaks for. Left empty by the
                pipeline and supplied by the persistence layer, as dead code
                does --- the id is a storage concern the analysis does not
                need.
            source_map: raw bytes the ingestion phase already read, keyed by
                repo-relative path. Markdown is present here: it is a
                registered language spec, and the parser returns an empty
                ``ParsedFile`` for it rather than ``None``, so its bytes reach
                the map like any other file.
            tracked_paths: every path the index knows about. Defaults to the
                ``source_map`` keys, which are already gitignore-filtered by
                the traverser (and also honour ``.repowiseIgnore`` and
                per-directory ignores, which a bare gitignore spec does not).
            repo_root: the working tree, when there is one. Defaults
                ``on_disk`` to a case-exact filesystem probe and
                ``rename_lookup`` to :func:`~.suggest.git_renames`.
            rename_lookup: old path -> new path, called once per run for
                suggestions. ``None`` (and no root) skips rename suggestions.
            on_disk: whether a repo-relative path exists in the working tree;
                see :attr:`~.resolver.RepoIndex.on_disk`.
            opaque_dirs: directories the tree cannot list (submodules); see
                :attr:`~.resolver.RepoIndex.opaque_dirs`.
        """
        self._repo_id = repo_id
        self._source_map = source_map or {}
        self._tracked = frozenset(tracked_paths or self._source_map.keys())
        self._repo_root = Path(repo_root) if repo_root is not None else None
        if self._repo_root is not None:
            rename_lookup = rename_lookup or partial(git_renames, self._repo_root)
        self._on_disk = on_disk
        self._rename_lookup = rename_lookup
        self._opaque_dirs = opaque_dirs

    # -- input preparation ------------------------------------------------

    def _decode(self, rel: str) -> str | None:
        raw = self._source_map.get(rel)
        if raw is None:
            return None
        # Reproduces the 500KB ``max_file_size_kb`` ceiling that already keeps
        # oversized non-AST files out of the index. Redundant on the normal
        # path (the traverser applied it first) and deliberately kept, so a
        # caller handing this analyzer a source_map it built itself cannot
        # walk into a 40MB generated document.
        if len(raw) > MAX_DOC_BYTES:
            return None
        return raw.decode("utf-8", errors="replace")

    def _documents(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel in self._source_map:
            if not is_checkable_document(rel):
                continue
            text = self._decode(rel)
            if text is not None:
                out[rel] = text
        return out

    def _manifests(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel in self._source_map:
            if rel.rsplit("/", 1)[-1] not in MANIFEST_NAMES:
                continue
            text = self._decode(rel)
            if text is not None:
                out[rel] = text
        return out

    # -- the pass ---------------------------------------------------------

    def analyze(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
    ) -> DocDriftReport:
        """Run the drift pass.

        Config keys: ``min_confidence`` (float), and ``check_<kind>`` booleans
        for each :class:`~.models.DriftKind`, all defaulting to on.
        """
        cfg = config or {}
        enabled = {
            kind for kind in DriftKind if cfg.get(f"check_{kind.value}", True)
        }

        documents = self._documents()
        if on_step:
            on_step("collect")

        on_disk = self._on_disk
        if on_disk is None and self._repo_root is not None:
            on_disk = _exact_probe(self._repo_root)
        index = RepoIndex.build(
            tracked_paths=self._tracked,
            doc_text=documents,
            manifest_text=self._manifests(),
            on_disk=on_disk,
            opaque_dirs=self._opaque_dirs,
        )
        if on_step:
            on_step("index")

        misses: list[Resolution] = []
        resolved: list[ResolvedDocReference] = []
        verdict_counts: dict[str, int] = {v.value: 0 for v in DriftVerdict}
        references_checked = 0
        suppressed = 0

        for rel, text in documents.items():
            is_guide = is_guide_document(rel)
            refs, silenced = extract_with_suppressed(text, rel)
            suppressed += sum(1 for ref in silenced if ref.kind in enabled)
            for ref in refs:
                if ref.kind not in enabled:
                    continue
                references_checked += 1
                res = resolve(index, ref, is_guide=is_guide)
                verdict_counts[res.verdict.value] += 1
                if res.verdict is DriftVerdict.MISSING:
                    misses.append(res)
                elif res.resolved_target and res.resolved_target != rel:
                    # Retention, not recomputation. Only a resolution naming
                    # a real file sets ``resolved_target``, so an ambiguous or
                    # uncheckable reference cannot land here as a fact. A
                    # document naming itself is dropped: a table of contents
                    # answers the reverse question with the file the reader is
                    # already in.
                    resolved.append(_to_resolved(res))
        if on_step:
            on_step("resolve")

        min_conf = float(cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
        shown = [res for res in misses if res.confidence >= min_conf]
        hidden = len(misses) - len(shown)
        # Suggestions only for findings a reader will see: the rename lookup is
        # a git call.
        suggestions = suggest_all(
            shown, index, rename_lookup=self._rename_lookup, on_disk=on_disk
        )
        findings = [
            _to_finding(res, suggestion)
            for res, suggestion in zip(shown, suggestions, strict=True)
        ]
        findings.sort(key=lambda f: (-f.confidence, f.file_path, f.line_number))

        logger.debug(
            "doc_drift_complete",
            documents=len(documents),
            references=references_checked,
            findings=len(findings),
            renderer=index.renderers.summary(),
        )

        return DocDriftReport(
            repo_id=self._repo_id,
            analyzed_at=datetime.now(UTC),
            total_findings=len(findings),
            findings=findings,
            confidence_summary=summarize_confidence(findings),
            documents_scanned=len(documents),
            references_checked=references_checked,
            verdict_summary=verdict_counts,
            anchor_renderer=index.renderers.summary(),
            hidden_below_threshold=hidden,
            suppressed=suppressed,
            documents=frozenset(documents),
            resolved_references=resolved,
        )


def _to_resolved(res: Any) -> ResolvedDocReference:
    ref = res.ref
    return ResolvedDocReference(
        doc_path=ref.doc_path,
        target_path=res.resolved_target,
        kind=ref.kind,
        line=ref.line,
        section=ref.section,
    )


def _exact_probe(root: Path) -> Callable[[str], bool]:
    """Whether a path exists beneath *root* with exactly this spelling.

    Case-exact on every filesystem, so a document that differs from the tree
    only in case is drift locally as it is on Linux CI. Paths that climb out
    are refused. Listings are cached for the life of the probe (one run).
    """
    listings: dict[Path, frozenset[str]] = {}

    def probe(rel: str) -> bool:
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            return False
        parent = root
        for name in rel.split("/"):
            if name in ("", "."):
                continue
            names = listings.get(parent)
            if names is None:
                try:
                    names = frozenset(os.listdir(parent))
                except (OSError, ValueError):
                    names = frozenset()
                listings[parent] = names
            if name not in names:
                return False
            parent = parent / name
        return True

    return probe


def _to_finding(res: Resolution, suggestion: Suggestion = ("", "")) -> DocDriftFindingData:
    ref = res.ref
    target, _, fragment = ref.target.partition("#")
    reason = _REASONS[res.origin].format(
        target=target or ref.target,
        fragment=fragment,
        doc=target or ref.doc_path,
        raw=ref.raw,
    )
    evidence = [
        f"{ref.doc_path}:{ref.line} states `{ref.raw}`",
        f"resolution: {res.detail}",
    ]
    if ref.section:
        evidence.append(f"under: {ref.section}")
    return DocDriftFindingData(
        kind=ref.kind,
        file_path=ref.doc_path,
        line_number=ref.line,
        target=ref.target,
        confidence=res.confidence,
        reason=reason,
        origin=res.origin,
        evidence=evidence,
        raw=ref.raw,
        context=ref.context[:300],
        suggestion=suggestion[0],
        suggestion_basis=suggestion[1],
    )


def summarize_confidence(findings: list[DocDriftFindingData]) -> dict:
    """Bucket *findings* into the house high/medium/low tiers.

    Same boundaries as dead code, so the word "high" means the same thing in
    both reports. Pinned by ``tests/unit/doc_drift/test_confidence_parity.py``.
    """
    return bucket_confidences(f.confidence for f in findings)
