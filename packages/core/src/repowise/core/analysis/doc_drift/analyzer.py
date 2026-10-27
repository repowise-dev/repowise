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
from dataclasses import dataclass, field
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
from .extractor import (
    extract_with_suppressed,
    is_checkable_document,
    is_guide_document,
    symbol_name,
)
from .models import (
    DocDriftFindingData,
    DocDriftReport,
    DocReference,
    DriftKind,
    DriftVerdict,
    ResolvedDocReference,
    SymbolScope,
)
from .resolver import RepoIndex, Resolution, resolve
from .suggest import (
    NO_SUGGESTION,
    RenameLookup,
    Suggestion,
    apply_suggestion,
    git_renames,
    suggest_all,
)
from .symbols import SymbolMiss, SymbolOptions, SymbolRecheck, resolve_symbols

logger = structlog.get_logger(__name__)

_REASONS: dict[str, str] = {
    "path_no_candidate": "Document names {target}, which no longer exists.",
    "path_no_candidate_in_guide": (
        "Document names {target}, which does not exist. This is a guide, so it "
        "may be an illustration rather than drift."
    ),
    "anchor_no_heading": "Link points at #{fragment}, which {doc} no longer declares.",
    "command_no_target": "Document shows `{raw}`, but no such target is declared.",
    "symbol_no_definition": (
        "Document names `{raw}`, which was defined in code when this line was "
        "written and is defined nowhere now."
    ),
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
        symbols: SymbolOptions | None = None,
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
            symbols: the index's symbol names and, on an update, what to
                re-resolve. The ``symbol`` kind runs only with these and a
                ``repo_root``; see :mod:`~.symbols`.
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
        self._symbols = symbols if self._repo_root is not None else None

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

    def _enabled(self, cfg: dict) -> set[DriftKind]:
        enabled = {kind for kind in DriftKind if cfg.get(f"check_{kind.value}", True)}
        if self._symbols is None:
            enabled.discard(DriftKind.SYMBOL)
        return enabled

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
        step = on_step or (lambda _stage: None)
        enabled = self._enabled(cfg)

        documents = self._documents()
        step("collect")

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
        step("index")

        tally = _Tally()
        candidates = _SymbolCandidates(self._symbols.recheck if self._symbols else None)
        for rel, text in documents.items():
            refs = tally.extract(text, rel, enabled)
            if DriftKind.SYMBOL in enabled:
                candidates.collect(rel, refs)
            tally.resolve_document(index, rel, refs, enabled)
        self._resolve_symbols(candidates.refs, tally)
        step("resolve")

        min_conf = float(cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
        shown = [pair for pair in tally.misses if pair[0].confidence >= min_conf]
        findings = self._findings(shown, index, on_disk, documents)

        logger.debug(
            "doc_drift_complete",
            documents=len(documents),
            references=tally.references_checked,
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
            references_checked=tally.references_checked,
            verdict_summary=tally.verdicts,
            anchor_renderer=index.renderers.summary(),
            hidden_below_threshold=len(tally.misses) - len(shown),
            suppressed=tally.suppressed,
            documents=frozenset(documents),
            resolved_references=tally.resolved,
            symbol_scope=candidates.scope(),
        )

    def _resolve_symbols(self, refs: list[DocReference], tally: _Tally) -> None:
        """The symbol kind, batched over every document's candidates."""
        if not refs or self._symbols is None or self._repo_root is None:
            return
        for outcome in resolve_symbols(
            refs,
            root=self._repo_root,
            symbol_names=self._symbols.names,
            rename_lookup=self._rename_lookup,
        ):
            if outcome is not None:  # ``None``: not a symbol reference
                tally.record(*outcome)

    def _findings(
        self,
        shown: list[tuple[Resolution, SymbolMiss | None]],
        index: RepoIndex,
        on_disk: Callable[[str], bool] | None,
        documents: dict[str, str],
    ) -> list[DocDriftFindingData]:
        """The findings a reader sees, with suggestions, most confident first."""
        # Suggestions only for findings a reader will see: the rename lookup is
        # a git call.
        suggestions = suggest_all(
            [res for res, _ in shown], index, rename_lookup=self._rename_lookup, on_disk=on_disk
        )
        lines: dict[str, list[str]] = {}
        findings = []
        for (res, miss), suggestion in zip(shown, suggestions, strict=True):
            doc = res.ref.doc_path
            if doc not in lines:
                lines[doc] = documents.get(doc, "").split("\n")
            findings.append(_to_finding(res, suggestion, lines[doc], miss))
        findings.sort(key=lambda f: (-f.confidence, f.file_path, f.line_number))
        return findings


@dataclass
class _Tally:
    """What the pass has counted and kept so far."""

    misses: list[tuple[Resolution, SymbolMiss | None]] = field(default_factory=list)
    resolved: list[ResolvedDocReference] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=lambda: {v.value: 0 for v in DriftVerdict})
    references_checked: int = 0
    suppressed: int = 0

    def extract(self, text: str, rel: str, enabled: set[DriftKind]) -> list[DocReference]:
        """*rel*'s references, counting the enabled ones an inline marker silenced."""
        refs, silenced = extract_with_suppressed(text, rel)
        # A silenced symbol candidate is not known to be a reference at all.
        self.suppressed += sum(
            1 for ref in silenced if ref.kind in enabled and ref.kind is not DriftKind.SYMBOL
        )
        return refs

    def record(self, res: Resolution, miss: SymbolMiss | None = None) -> None:
        self.references_checked += 1
        self.verdicts[res.verdict.value] += 1
        if res.verdict is DriftVerdict.MISSING:
            self.misses.append((res, miss))

    def resolve_document(
        self, index: RepoIndex, rel: str, refs: list[DocReference], enabled: set[DriftKind]
    ) -> None:
        """Resolve *rel*'s references of every enabled kind but ``SYMBOL``."""
        is_guide = is_guide_document(rel)
        for ref in refs:
            if ref.kind not in enabled or ref.kind is DriftKind.SYMBOL:
                continue
            res = resolve(index, ref, is_guide=is_guide)
            self.record(res)
            if res.verdict is not DriftVerdict.MISSING and res.resolved_target not in ("", rel):
                # Retention, not recomputation. Only a resolution naming a real
                # file sets ``resolved_target``, so an ambiguous or uncheckable
                # reference cannot land here as a fact. A document naming itself
                # is dropped: a table of contents answers the reverse question
                # with the file the reader is already in.
                self.resolved.append(_to_resolved(res))


class _SymbolCandidates:
    """The symbol candidates this run re-resolves, and the scope that makes them."""

    def __init__(self, recheck: SymbolRecheck | None) -> None:
        self.recheck = recheck
        self.refs: list[DocReference] = []
        self.whole_docs: set[str] = set()
        self.single_refs: set[tuple[str, str]] = set()

    def collect(self, doc: str, refs: list[DocReference]) -> None:
        candidates = [ref for ref in refs if ref.kind is DriftKind.SYMBOL]
        if self.recheck is None:
            self.refs += candidates
        elif self.recheck.whole(doc):
            self.whole_docs.add(doc)
            self.refs += candidates
        else:
            wanted = [r for r in candidates if self.recheck.wants(doc, symbol_name(r.raw))]
            self.single_refs.update((doc, r.target) for r in wanted)
            self.refs += wanted

    def scope(self) -> SymbolScope | None:
        """``None`` on a full pass, which speaks for every symbol finding."""
        if self.recheck is None:
            return None
        return SymbolScope(frozenset(self.whole_docs), frozenset(self.single_refs))


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


def _to_finding(
    res: Resolution,
    suggestion: Suggestion = NO_SUGGESTION,
    lines: list[str] | None = None,
    miss: SymbolMiss | None = None,
) -> DocDriftFindingData:
    ref = res.ref
    if miss is not None:
        suggestion = miss.suggestion
    edit = apply_suggestion(_line_at(lines or [], ref.line), ref, suggestion[0])
    return DocDriftFindingData(
        kind=ref.kind,
        file_path=ref.doc_path,
        line_number=ref.line,
        target=ref.target,
        confidence=res.confidence,
        reason=_reason(res),
        origin=res.origin,
        evidence=_evidence(res, miss),
        raw=ref.raw,
        context=ref.context[:300],
        suggestion=suggestion[0],
        suggestion_basis=suggestion[1],
        suggested_line=edit[0] if edit else "",
        suggestion_columns=edit[1] if edit else (),
        defined_in=miss.defined_in if miss is not None else (),
    )


def _reason(res: Resolution) -> str:
    ref = res.ref
    target, _, fragment = ref.target.partition("#")
    return _REASONS[res.origin].format(
        target=target or ref.target,
        fragment=fragment,
        doc=target or ref.doc_path,
        raw=ref.raw,
    )


def _evidence(res: Resolution, miss: SymbolMiss | None) -> list[str]:
    ref = res.ref
    evidence = [
        f"{ref.doc_path}:{ref.line} states `{ref.raw}`",
        f"resolution: {res.detail}",
    ]
    if miss is not None:
        evidence.append(miss.evidence)
    if ref.section:
        evidence.append(f"under: {ref.section}")
    return evidence


def _line_at(lines: list[str], number: int) -> str:
    """The 1-indexed *number*th line, without its carriage return, or ``""``."""
    return lines[number - 1].rstrip("\r") if 0 < number <= len(lines) else ""


def summarize_confidence(findings: list[DocDriftFindingData]) -> dict:
    """Bucket *findings* into the house high/medium/low tiers.

    Same boundaries as dead code, so the word "high" means the same thing in
    both reports. Pinned by ``tests/unit/doc_drift/test_confidence_parity.py``.
    """
    return bucket_confidences(f.confidence for f in findings)
