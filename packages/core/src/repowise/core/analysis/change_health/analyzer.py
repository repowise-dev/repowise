"""Run the health pass over one revision's content, in memory.

Both sides of a comparison go through this class with the same analyzer
version, the same rules, and the same file universe, so a difference in the
findings is a difference in the code rather than in how it was analysed.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ...ingestion import compute_content_hash
from ...ingestion.graph.builder import GraphBuilder
from ...ingestion.models import (
    EXTENSION_TO_LANGUAGE,
    SPECIAL_FILENAMES,
    FileInfo,
    ParsedFile,
    Symbol,
)
from ...ingestion.package_roots import package_roots_above
from ...ingestion.parse_cache import ParseCache
from ...ingestion.parser import parse_file
from ...test_paths import is_test_related_path
from ..health import (
    HEALTH_ANALYZER_VERSION,
    HealthAnalyzer,
    HealthFileMetricData,
    HealthFindingData,
)
from ..health.complexity.languages import has_health_dialect
from ..health.source_reader import MappingSourceReader
from ..health.walk_cache import HealthWalkCache

if TYPE_CHECKING:
    from .index_inputs import IndexInputs

#: Files above this size are skipped rather than parsed on both sides.
MAX_FILE_BYTES = 1_500_000

_GENERATED_MARKERS = (b"GENERATED CODE", b"AUTO-GENERATED", b"@generated", b"DO NOT EDIT")


def language_of(path: str) -> str | None:
    """The language *path* is written in, whether or not health can read it."""
    name = PurePosixPath(path).name
    if name in SPECIAL_FILENAMES:
        return SPECIAL_FILENAMES[name]
    return EXTENSION_TO_LANGUAGE.get(PurePosixPath(path).suffix)


def language_for(path: str) -> str | None:
    """The language the health pass can actually analyse, else ``None``.

    Recognising a language is not the same as being able to score it: Markdown
    and JSON parse fine and produce no findings, so counting them as analysed
    would let a docs-only change read as a clean bill of health.
    """
    language = language_of(path)
    return language if has_health_dialect(language) else None


def is_generated(source: bytes) -> bool:
    """True when the file announces itself as generated in its header."""
    header = source[:512].upper()
    return any(marker.upper() in header for marker in _GENERATED_MARKERS)


def is_binary(source: bytes) -> bool:
    return b"\0" in source[:8192]


@dataclass(frozen=True, slots=True)
class AnalysisCaches:
    """Parse and walk results kept by content, shared by both sides of a comparison.

    A file's parse and complexity walk depend only on its bytes, path and the
    analyzer's versions (the keys carry all of them), so a base or head file
    seen in an earlier comparison is not parsed or walked again. Everything
    after the walk reads the whole changed set and is always recomputed.

    The files are the index's cache formats in their own directory, so a
    comparison never evicts the index's entries. Each save keeps only the
    entries the last comparison used, which bounds the size. Loading and
    saving never raise: a read-only or missing directory is a cold cache.
    """

    directory: Path
    parse: ParseCache
    walk: HealthWalkCache

    @classmethod
    def open(cls, cache_dir: str | Path) -> AnalysisCaches:
        directory = Path(cache_dir)
        caches = cls(
            directory, ParseCache(directory), HealthWalkCache(directory, HEALTH_ANALYZER_VERSION)
        )
        caches.parse.load()
        caches.walk.load()
        return caches

    def save(self) -> None:
        # Ignored by its own .gitignore, so a working-tree comparison never
        # counts the cache as an untracked change of the next one.
        with contextlib.suppress(OSError):
            self.directory.mkdir(parents=True, exist_ok=True)
            ignore = self.directory / ".gitignore"
            if not ignore.exists():
                ignore.write_text("*\n", encoding="utf-8")
        self.parse.save()
        self.walk.save()


@dataclass(slots=True)
class RevisionAnalysis:
    """One side's findings plus what it could not look at."""

    findings: list[HealthFindingData] = field(default_factory=list)
    analyzed: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)
    #: Dependents count per file, as this side's centrality gates read it.
    dependents: dict[str, int] = field(default_factory=dict)
    symbols: dict[str, list[Symbol]] = field(default_factory=dict)
    #: Per-file scores and deductions, by path.
    metrics: dict[str, HealthFileMetricData] = field(default_factory=dict)
    #: Where clones came from: "index" (partners outside *sources* counted),
    #: "analyzed_files" (only among *sources*), None when duplication was off.
    clone_source: str | None = None

    def findings_for(self, paths: set[str]) -> list[HealthFindingData]:
        return [f for f in self.findings if f.file_path in paths]


class RevisionHealthAnalyzer:
    """Analyze a set of paths at one revision from supplied bytes alone."""

    def __init__(self, *, repo_root: str | None = None, config: dict | None = None) -> None:
        self.repo_root = repo_root
        self.config = config

    def analyze(
        self,
        sources: dict[str, bytes],
        *,
        subject_paths: set[str],
        dependents: dict[str, int] | None = None,
        index: IndexInputs | None = None,
        caches: AnalysisCaches | None = None,
    ) -> RevisionAnalysis:
        """Analyze *sources*, reporting findings for the whole supplied set.

        *subject_paths* names the files the caller actually cares about; the
        remainder are closure context that shapes resolution without being
        charged to the change.

        *dependents* is one count both sides score against: the index's when
        there is one, else the other side's :attr:`RevisionAnalysis.dependents`.
        Both sides see only the changed files, so a local count is a slice of
        the real one, and an import added between two changed files can carry
        a file across a centrality gate without any change to its code.

        *index* adds what the index knows (history, coverage, test pairing,
        blame findings, clone partners); without it a score is the code-shape
        reading.

        *caches* serves parses and walks seen before; the result is the same
        with or without it.
        """
        result = RevisionAnalysis()
        parsed, usable = self._parse(sources, result, caches.parse if caches else None)
        if not parsed:
            return result
        analyzer = HealthAnalyzer(
            self._graph(parsed, usable),
            parsed_files=parsed,
            repo_root=self.repo_root,
            source_reader=MappingSourceReader(usable),
            file_dependents=dependents,
            walk_cache=caches.walk if caches else None,
            package_roots=_package_roots(self.repo_root, usable),
            **(_index_kwargs(index) if index is not None else {}),
        )
        report = analyzer.analyze(config=self.config, changed_files=set(usable))
        result.findings = list(report.findings)
        result.analyzed = set(usable)
        result.dependents = analyzer.file_dependents()
        result.symbols = {pf.file_info.path: pf.symbols for pf in parsed}
        result.metrics = {m.file_path: m for m in report.metrics}
        result.clone_source = analyzer.clone_source
        return result

    # -- internals ----------------------------------------------------------

    def _parse(
        self, sources: dict[str, bytes], result: RevisionAnalysis, cache: ParseCache | None
    ) -> tuple[list[ParsedFile], dict[str, bytes]]:
        parsed: list[ParsedFile] = []
        usable: dict[str, bytes] = {}
        for path, raw in sorted(sources.items()):
            reason = self._reject(path, raw)
            if reason is not None:
                result.skipped[path] = reason
                continue
            language = language_for(path)
            assert language is not None  # _reject already screened this
            try:
                parsed.append(_parse_one(_file_info(path, language, raw), raw, cache))
            except Exception:
                result.skipped[path] = "parse_failed"
                continue
            usable[path] = raw
        return parsed, usable

    @staticmethod
    def _reject(path: str, raw: bytes) -> str | None:
        if language_for(path) is None:
            return "unsupported_language"
        if len(raw) > MAX_FILE_BYTES:
            return "too_large"
        if is_binary(raw):
            return "binary"
        if is_generated(raw):
            return "generated"
        return None

    @staticmethod
    def _graph(parsed: list[ParsedFile], sources: dict[str, bytes]):
        builder = GraphBuilder()
        for pf in parsed:
            builder.add_file(pf)
        builder.set_source_map(dict(sources))
        return builder.build()


def _parse_one(info: FileInfo, raw: bytes, cache: ParseCache | None) -> ParsedFile:
    if cache is None:
        return parse_file(info, raw)
    content_hash = compute_content_hash(raw)
    parsed = cache.get(info, content_hash)
    if parsed is None:
        parsed = parse_file(info, raw)
        # Before the graph build, which mutates the parse in place.
        cache.put(parsed, content_hash)
    return parsed


def _package_roots(repo_root: str | None, sources: dict[str, bytes]) -> set[str] | None:
    """The package roots above the analysed files, or None to let the engine decide.

    The engine's own scan walks the whole checkout, which on a large repo
    costs more than analysing a small change.
    """
    if repo_root is None:
        return None
    try:
        return package_roots_above(repo_root, sources)
    except OSError:
        return None


def _index_kwargs(index: IndexInputs) -> dict[str, Any]:
    return {
        "git_meta_map": index.git_meta,
        "coverage_map": index.coverage,
        "tested_files": index.tested_files,
        "stored_blame_findings": index.blame_findings,
        "clone_index_dir": index.clone_index_dir,
    }


def _file_info(path: str, language: str, raw: bytes) -> FileInfo:
    """A FileInfo whose ``abs_path`` is the repo-relative key.

    The health pass reads every byte through the injected source reader, which
    is keyed the same way, so no absolute path is ever resolved against disk.
    """
    return FileInfo(
        path=path,
        abs_path=path,
        language=language,  # type: ignore[arg-type]
        size_bytes=len(raw),
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=is_test_related_path(path, language),
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
