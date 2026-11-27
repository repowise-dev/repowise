"""The unreachable-file pass: files nothing can reach that are not tests, fixtures or config."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ....code_origin import code_origin
from ....ingestion.languages.registry import REGISTRY
from ..constants import (
    _DEAD_CODE_EXEMPT_LANGUAGES,
    _NAMESPACE_IMPORT_LANGUAGES,
    _NON_CODE_LANGUAGES,
    _is_fixture_path,
)
from ..file_reachability import is_file_reachable
from ..finding_helpers import _LINES_UNKNOWN, _is_synthetic_node
from ..models import DeadCodeFindingData, DeadCodeKind
from ..name_occurrences import file_identifiers, occurrence_files, searchable_token
from ..risk_factors import (
    NO_GIT_SIGNAL_CONFIDENCE,
    RISK_CAP_CONFIDENCE,
    path_risk_factors,
    risk_evidence,
)
from ..symbol_rules import _is_public_top_level

if TYPE_CHECKING:
    from ..analyzer import DeadCodeAnalyzer


#: ``(days, confidence)`` rungs for a file with no commit in 90 days, oldest
#: first. ``_is_old`` uses strict >, so days-1 gives >= semantics: a year or
#: more untouched is 1.0, very likely dead.
_GIT_AGE_RUNGS: tuple[tuple[int, float], ...] = ((364, 1.0), (179, 0.9), (89, 0.8))

#: The ceiling for a file whose missing importer says little: its language's
#: resolver misses common live shapes, or production code writes its name. Git
#: age only says how long nobody touched the file, so it must not lift the file
#: past this. The same weakest-evidence value as a file with no git row: below
#: the deletion-ready threshold, still reported.
UNPROVEN_FILE_CONFIDENCE = NO_GIT_SIGNAL_CONFIDENCE

#: Languages where having no importer is weak evidence. Every language whose
#: import support is not "full" (Elixir, Scala, F# and the regex-tier
#: resolvers), plus three whose file edges miss common live shapes: PHP
#: computed includes (``require_once(__DIR__ . '/x.php')``), Ruby constants
#: Zeitwerk autoloads, and Swift same-module types (44% call precision measured
#: on one app). An unreachable file has no edge by definition, so no edge-level
#: fact can lift it; a language leaves this set when its resolver closes the gap.
_WEAK_RESOLVER_LANGUAGES: frozenset[str] = frozenset(
    tag for tag, tier in REGISTRY.import_support_map().items() if tier != "full"
) | {"php", "ruby", "swift"}

#: Declarations whose name another file writes to use the file: a module
#: (Elixir ``defmodule``, Ruby ``module``). A type's name is
#: ``clamp_named_types``' question, asked after the passes.
_NAMED_DECLARATION_KINDS: frozenset[str] = frozenset({"module"})

#: Languages that name each other's files: a C header in C++ or Objective-C,
#: a TypeScript module in JavaScript or a component file.
_LANGUAGE_FAMILY: dict[str, str] = {
    "c": "cpp",
    "objectivec": "cpp",
    "javascript": "typescript",
    "vue": "typescript",
    "svelte": "typescript",
    "astro": "typescript",
}


def _family(language: str | None) -> str | None:
    return _LANGUAGE_FAMILY.get(language or "", language)


class UnreachableFilePass:
    """Scores each file the shared reachability predicate cannot reach."""

    def __init__(self, ctx: DeadCodeAnalyzer) -> None:
        self.ctx = ctx
        self.graph = ctx.graph
        self._origins: dict[str, str] = {}

    def run(
        self,
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect files nothing can reach that are not tests, fixtures, or config."""
        # The rescues do not vary by candidate: build them once per pass.
        rescues = self.ctx._reachability_rescues(whitelist)
        candidates: list[tuple[str, dict]] = []

        for node in self.graph.nodes():
            if _is_synthetic_node(str(node)):
                continue

            node_data = self.graph.nodes[node]
            # The question is about files, and most graph nodes are symbols.
            if node_data.get("node_type") == "symbol":
                continue
            # These skips are scoping, not reachability; every reachability
            # rule lives in :func:`is_file_reachable`, which the overview
            # assembler shares so the two cannot disagree.
            if node_data.get("language", "unknown") in _DEAD_CODE_EXEMPT_LANGUAGES:
                continue
            if node_data.get("is_test", False):
                continue
            if _is_fixture_path(str(node)):
                continue

            if is_file_reachable(str(node), self.graph, rescues):
                continue
            candidates.append((str(node), node_data))

        mentions = self._production_mentions(candidates)
        return [
            self._make_unreachable_finding(path, node_data, mentions.get(path))
            for path, node_data in candidates
        ]

    def _names_of(self, path: str) -> set[bytes]:
        """What another file writes to use *path*: its stem and its declared names.

        A dotted module name (``AppWeb.HealthController``) is searched by its
        last segment, the part every spelling of it shares.
        """
        names = [PurePosixPath(path).stem]
        names += [
            sym["name"].replace("::", ".").rsplit(".", 1)[-1]
            for _, sym in self.ctx._defined_symbols(path)
            if _is_public_top_level(sym, _NAMED_DECLARATION_KINDS)
        ]
        return {searchable_token(name) for name in names} - {b""}

    def _production_mentions(
        self, candidates: list[tuple[str, dict]]
    ) -> dict[str, tuple[str, str]]:
        """``path -> (writer, name)`` for each candidate production code names.

        One identifier scan over the indexed source. A writer counts only when
        it is another file of the candidate's language whose origin is
        production: a test naming its subject, a doc listing it or a file in
        another language says nothing about runtime use. A name is not proof
        of use, so this caps a finding and never drops it. Ceiling: a common
        stem (``utils``) matches by coincidence, which can only cap a dead file
        too low, the safe direction.
        """
        if not candidates or not self.ctx._source_map:
            return {}
        wanted = {path: self._names_of(path) for path, _ in candidates}
        languages = {path: _family(data.get("language")) for path, data in candidates}
        writers = occurrence_files(
            self.ctx._source_map, set().union(*wanted.values()), self._code_identifiers
        )
        mentions: dict[str, tuple[str, str]] = {}
        for path, names in wanted.items():
            found = sorted(
                (writer, name.decode())
                for name in names
                for writer in writers.get(name, ())
                if writer != path
                and _family(self.graph.nodes[writer].get("language")) == languages[path]
                and self._is_production(writer)
            )
            if found:
                mentions[path] = found[0]
        return mentions

    def _code_identifiers(self, path: str) -> frozenset[bytes]:
        """*path*'s identifiers, or none when it is not code (docs, data, config)."""
        language = self.graph.nodes[path].get("language") if path in self.graph else None
        if language is None or language in _NON_CODE_LANGUAGES:
            return frozenset()
        return file_identifiers(self.ctx._source_map[path])

    def _is_production(self, path: str) -> bool:
        """Whether ``code_origin`` reads *path* as production code."""
        if path not in self._origins:
            self._origins[path] = code_origin(
                path,
                self.ctx._source_map.get(path),
                is_test=self.graph.nodes[path].get("is_test"),
                repo_root=self.ctx._repo_root,
            )
        return self._origins[path] == "production"

    def _make_unreachable_finding(
        self,
        node: str,
        node_data: dict,
        mention: tuple[str, str] | None = None,
    ) -> DeadCodeFindingData:
        """Create an unreachable file finding with confidence scoring.

        *mention* is ``(writer, name)`` when production code writes the file's
        name (see :meth:`_production_mentions`).
        """
        git_meta = self.ctx.git_meta_map.get(node)
        # No row at all is not "no commits in 90 days": that rung is for a
        # file git has data on. Scored below the deletion-ready threshold and
        # said so in the evidence, on every path that builds this map.
        no_git_signal = not git_meta
        git_meta = git_meta or {}
        commit_90d = git_meta.get("commit_count_90d", 0)
        last_commit = git_meta.get("last_commit_at")
        age_days = git_meta.get("age_days")
        primary_owner = git_meta.get("primary_owner_name")

        confidence = (
            NO_GIT_SIGNAL_CONFIDENCE
            if no_git_signal
            else self._git_age_confidence(commit_90d, last_commit, age_days)
        )

        # The git-age rungs are an evidence scale and stay literal; the caps
        # below are tier boundaries and read the constants.

        # Reduce confidence when dynamic imports exist in the same package.
        if self.ctx._dynamic_import_dirs and str(Path(node).parent) in self.ctx._dynamic_import_dirs:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # Runtime-load risk factors (config, bootstrap, scripts, assets) make
        # "in_degree=0" weak evidence: cap and say so in the evidence.
        risk_factors = path_risk_factors(node)
        if risk_factors:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # A namespace-imported file is reached without an edge (a same-namespace
        # ``new T()``), so no edge says little and its age says nothing more.
        namespace_imported = node_data.get("language") in _NAMESPACE_IMPORT_LANGUAGES
        if namespace_imported:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # The same holds, less strongly, for a weak resolver or a file whose
        # name production code writes: see UNPROVEN_FILE_CONFIDENCE. Said in
        # the evidence only when it is what holds the confidence down.
        unproven = confidence > UNPROVEN_FILE_CONFIDENCE
        weak_resolver = unproven and node_data.get("language") in _WEAK_RESOLVER_LANGUAGES
        mention = mention if unproven else None
        if weak_resolver or mention:
            confidence = UNPROVEN_FILE_CONFIDENCE

        # A whole file is a review candidate, never deletion-ready: see
        # ``REVIEW_ONLY_KINDS``. Confidence still ranks it.
        safe = False

        evidence = ["in_degree=0 (no files import this)"]
        if no_git_signal:
            evidence.append("No git history recorded for this file")
        elif commit_90d == 0:
            evidence.append("No commits in last 90 days")
        if self.ctx._dynamic_import_files and confidence <= RISK_CAP_CONFIDENCE:
            evidence.append("Package uses dynamic imports or runtime-resolved edges")
        risk_line = risk_evidence(risk_factors)
        if risk_line:
            evidence.append(risk_line)
        if namespace_imported:
            evidence.append(
                "Imported by namespace, not by file, so having no importer is weak evidence"
            )
        if weak_resolver:
            evidence.append(
                f"Some {node_data.get('language')} references resolve to no file, "
                "so having no importer is weak evidence"
            )
        if mention:
            evidence.append(
                f"Its name '{mention[1]}' is written in {mention[0]}, "
                "which may use it without an import"
            )
        lines = self.ctx._file_line_count(node)
        if lines is None:
            evidence.append(_LINES_UNKNOWN)

        return DeadCodeFindingData(
            kind=DeadCodeKind.UNREACHABLE_FILE,
            file_path=node,
            symbol_name=None,
            symbol_kind=None,
            confidence=confidence,
            reason="File has no importers (in_degree=0)",
            last_commit_at=last_commit if isinstance(last_commit, datetime) else None,
            commit_count_90d=commit_90d,
            lines=lines,
            evidence=evidence,
            safe_to_delete=safe,
            primary_owner=primary_owner,
            age_days=age_days,
            risk_factors=list(risk_factors),
        )

    def _git_age_confidence(
        self, commit_90d: int, last_commit: Any, age_days: int | None
    ) -> float:
        """The git-age evidence ladder for a file no edge reaches."""
        if commit_90d != 0:
            return 0.4
        if last_commit:
            for days, confidence in _GIT_AGE_RUNGS:
                if self._is_old(last_commit, days=days):
                    return confidence
        if age_days is not None and age_days < 30:
            return 0.55  # Recently created: may be work in progress
        return 0.7

    def _is_old(self, dt: Any, days: int = 180) -> bool:
        """Return True if datetime is more than `days` before the indexed commit."""
        if isinstance(dt, datetime):
            return self.ctx.days_since(dt) > days
        return False
