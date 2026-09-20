"""Run the health pass over one revision's content, in memory.

Both sides of a comparison go through this class with the same analyzer
version, the same rules, and the same file universe, so a difference in the
findings is a difference in the code rather than in how it was analysed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath

from ...ingestion.graph.builder import GraphBuilder
from ...ingestion.models import EXTENSION_TO_LANGUAGE, SPECIAL_FILENAMES, FileInfo, ParsedFile
from ...ingestion.parser import parse_file
from ...test_paths import is_test_related_path
from ..health import HealthAnalyzer, HealthFindingData
from ..health.complexity.languages import get_language_map
from ..health.source_reader import MappingSourceReader

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
    if language is None:
        return None
    if language == "sql":  # walked by the sqlglot path, not a node map
        return language
    return language if get_language_map(language) is not None else None


def is_generated(source: bytes) -> bool:
    """True when the file announces itself as generated in its header."""
    header = source[:512].upper()
    return any(marker.upper() in header for marker in _GENERATED_MARKERS)


def is_binary(source: bytes) -> bool:
    return b"\0" in source[:8192]


@dataclass(slots=True)
class RevisionAnalysis:
    """One side's findings plus what it could not look at."""

    findings: list[HealthFindingData] = field(default_factory=list)
    analyzed: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)

    def findings_for(self, paths: set[str]) -> list[HealthFindingData]:
        return [f for f in self.findings if f.file_path in paths]


class RevisionHealthAnalyzer:
    """Analyze a set of paths at one revision from supplied bytes alone."""

    def __init__(self, *, repo_root: str | None = None, config: dict | None = None) -> None:
        self.repo_root = repo_root
        self.config = config

    def analyze(self, sources: dict[str, bytes], *, subject_paths: set[str]) -> RevisionAnalysis:
        """Analyze *sources*, reporting findings for the whole supplied set.

        *subject_paths* names the files the caller actually cares about; the
        remainder are closure context that shapes resolution without being
        charged to the change.
        """
        result = RevisionAnalysis()
        parsed, usable = self._parse(sources, result)
        if not parsed:
            return result
        analyzer = HealthAnalyzer(
            self._graph(parsed, usable),
            parsed_files=parsed,
            repo_root=self.repo_root,
            source_reader=MappingSourceReader(usable),
        )
        report = analyzer.analyze(config=self.config, changed_files=set(usable))
        result.findings = list(report.findings)
        result.analyzed = set(usable)
        return result

    # -- internals ----------------------------------------------------------

    def _parse(
        self, sources: dict[str, bytes], result: RevisionAnalysis
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
                parsed.append(parse_file(_file_info(path, language, raw), raw))
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
