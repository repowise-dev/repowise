"""The documentation drift pass.

Reads markdown the pipeline already decoded, extracts the assertions each
document makes about the repository, resolves them against the real tree, and
reports the ones the tree no longer satisfies.

Shaped like :class:`~repowise.core.analysis.dead_code.analyzer.DeadCodeAnalyzer`:
a synchronous class taking its inputs at construction and exposing
``analyze(config, *, on_step)``. The pipeline wraps it in ``asyncio.to_thread``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from .constants import (
    DEFAULT_MIN_CONFIDENCE,
    MAX_DOC_BYTES,
    bucket_confidences,
)
from .extractor import extract, is_checkable_document, is_guide_document
from .models import (
    DocDriftFindingData,
    DocDriftReport,
    DriftKind,
    DriftVerdict,
)
from .resolver import RepoIndex, resolve

logger = structlog.get_logger(__name__)

_MANIFEST_NAMES = frozenset({"Makefile", "makefile", "GNUmakefile", "package.json"})

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
        """
        self._repo_id = repo_id
        self._source_map = source_map or {}
        self._tracked = frozenset(tracked_paths or self._source_map.keys())

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
            if rel.rsplit("/", 1)[-1] not in _MANIFEST_NAMES:
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

        index = RepoIndex.build(
            tracked_paths=self._tracked,
            doc_text=documents,
            manifest_text=self._manifests(),
        )
        if on_step:
            on_step("index")

        findings: list[DocDriftFindingData] = []
        verdict_counts: dict[str, int] = {v.value: 0 for v in DriftVerdict}
        references_checked = 0

        for rel, text in documents.items():
            is_guide = is_guide_document(rel)
            for ref in extract(text, rel):
                if ref.kind not in enabled:
                    continue
                references_checked += 1
                res = resolve(index, ref, is_guide=is_guide)
                verdict_counts[res.verdict.value] += 1
                if res.verdict is DriftVerdict.MISSING:
                    findings.append(_to_finding(res))
        if on_step:
            on_step("resolve")

        min_conf = float(cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
        hidden = sum(1 for f in findings if f.confidence < min_conf)
        findings = [f for f in findings if f.confidence >= min_conf]
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
            documents=frozenset(documents),
        )


def _to_finding(res: Any) -> DocDriftFindingData:
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
    )


def summarize_confidence(findings: list[DocDriftFindingData]) -> dict:
    """Bucket *findings* into the house high/medium/low tiers.

    Same boundaries as dead code, so the word "high" means the same thing in
    both reports. Pinned by ``tests/unit/doc_drift/test_confidence_parity.py``.
    """
    return bucket_confidences(f.confidence for f in findings)
