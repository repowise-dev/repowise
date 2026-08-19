"""A file ingestion never read must not turn into deletion-ready findings.

Regression cover for #1237: a large entry point was dropped by the traversal
size cap, so every component it imported looked like it had no importer and was
reported ``safe_to_delete`` at high confidence.

The clamp is exercised directly rather than through a detector: what needs
pinning is which findings it touches and which it must leave alone, and
building a graph that yields one finding per case would test the detectors
instead.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.analysis.dead_code.analyzer import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE


def _finding(
    *,
    symbol_name: str | None,
    file_path: str = "widget.ts",
    kind: DeadCodeKind = DeadCodeKind.UNUSED_EXPORT,
) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=kind,
        file_path=file_path,
        symbol_name=symbol_name,
        symbol_kind="function",
        confidence=0.9,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=10,
        evidence=[],
        safe_to_delete=True,
        primary_owner=None,
        age_days=None,
    )


def _analyzer(repo: Path, skipped: list[tuple[str, str]]) -> DeadCodeAnalyzer:
    return DeadCodeAnalyzer(
        nx.DiGraph(), repo_root=repo, unindexed_source_files=skipped
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # The importer ingestion could not read. It references Widget, not Orphan.
    (tmp_path / "App.tsx").write_text(
        "import { Widget } from './widget';\nexport function App() { return <Widget />; }\n",
        encoding="utf-8",
    )
    return tmp_path


class TestUnindexedImporterClamp:
    def test_symbol_named_in_unindexed_file_is_not_deletion_ready(self, repo: Path) -> None:
        finding = _finding(symbol_name="Widget")
        _analyzer(repo, [("App.tsx", "over_max_size")])._clamp_for_unindexed_importers([finding])
        assert finding.safe_to_delete is False
        assert finding.confidence == RISK_CAP_CONFIDENCE
        assert any("not indexed" in e for e in finding.evidence)

    def test_clamped_finding_still_surfaces_at_the_default_floor(self, repo: Path) -> None:
        # The ceiling is RISK_CAP_CONFIDENCE, which sits exactly at the default
        # min_confidence so the finding stays visible as a review candidate.
        # Dropping it out of the report would be a second silent omission.
        finding = _finding(symbol_name="Widget")
        _analyzer(repo, [("App.tsx", "over_max_size")])._clamp_for_unindexed_importers([finding])
        assert finding.confidence >= 0.4

    def test_symbol_absent_from_unindexed_file_is_untouched(self, repo: Path) -> None:
        finding = _finding(symbol_name="Orphan")
        _analyzer(repo, [("App.tsx", "over_max_size")])._clamp_for_unindexed_importers([finding])
        assert finding.safe_to_delete is True
        assert finding.confidence == 0.9
        assert finding.evidence == []

    def test_file_findings_are_never_clamped_on_their_path_stem(self, repo: Path) -> None:
        # "index", "main", "utils", "app" are exactly the broad words that
        # risk_factors curates out of its own token map because they cap
        # ordinary modules. An unread importer imports symbols, not stems.
        (repo / "big.ts").write_text("const App = 1; const index = 2;\n", encoding="utf-8")
        finding = _finding(
            symbol_name=None, file_path="src/index.ts", kind=DeadCodeKind.UNREACHABLE_FILE
        )
        _analyzer(repo, [("big.ts", "over_max_size")])._clamp_for_unindexed_importers([finding])
        assert finding.safe_to_delete is True
        assert finding.confidence == 0.9

    def test_minified_files_do_not_feed_the_token_set(self, repo: Path) -> None:
        # A bundle carries its whole dependency tree inlined, so letting it
        # contribute identifiers would clamp most of the report.
        (repo / "vendor.js").write_text("var Widget=1,Orphan=2,render=3;\n", encoding="utf-8")
        analyzer = _analyzer(repo, [("vendor.js", "minified")])
        assert analyzer._unindexed_identifier_tokens() == frozenset()
        finding = _finding(symbol_name="Widget")
        analyzer._clamp_for_unindexed_importers([finding])
        assert finding.safe_to_delete is True

    def test_no_skipped_files_changes_nothing(self, repo: Path) -> None:
        finding = _finding(symbol_name="Widget")
        _analyzer(repo, [])._clamp_for_unindexed_importers([finding])
        assert finding.safe_to_delete is True
        assert finding.confidence == 0.9

    def test_missing_file_on_disk_does_not_raise(self, repo: Path) -> None:
        finding = _finding(symbol_name="Widget")
        analyzer = _analyzer(repo, [("does_not_exist.tsx", "over_max_size")])
        analyzer._clamp_for_unindexed_importers([finding])
        assert finding.safe_to_delete is True

    def test_per_file_read_is_bounded(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # These files are skipped because they are large; the scan must never
        # be the thing that loads one fully into memory.
        from repowise.core.analysis.dead_code import analyzer as analyzer_mod

        monkeypatch.setattr(analyzer_mod, "_UNINDEXED_SCAN_PER_FILE_BYTES", 64)
        (repo / "huge.ts").write_text(
            "EarlyIdentifier\n" + ("pad\n" * 200) + "LateIdentifier\n", encoding="utf-8"
        )
        tokens = _analyzer(repo, [("huge.ts", "over_max_size")])._unindexed_identifier_tokens()
        assert "EarlyIdentifier" in tokens
        assert "LateIdentifier" not in tokens, "read was not bounded by the per-file budget"

    def test_total_read_budget_stops_the_scan(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from repowise.core.analysis.dead_code import analyzer as analyzer_mod

        monkeypatch.setattr(analyzer_mod, "_UNINDEXED_SCAN_TOTAL_BYTES", 32)
        (repo / "one.ts").write_text("FirstIdentifier\n" + "pad\n" * 40, encoding="utf-8")
        (repo / "two.ts").write_text("SecondIdentifier\n", encoding="utf-8")
        tokens = _analyzer(
            repo, [("one.ts", "over_max_size"), ("two.ts", "over_max_size")]
        )._unindexed_identifier_tokens()
        assert "FirstIdentifier" in tokens
        assert "SecondIdentifier" not in tokens, "total budget did not stop the scan"

    def test_a_doc_include_reaches_the_symbol_it_embeds(self, repo: Path) -> None:
        # The reference a docs build writes names a *path*, but the identifier
        # split treats `/`, `-` and `.` as boundaries, so the sample's type
        # name falls out of the attribute on its own and the existing
        # name-keyed clamp reaches it with no path matching anywhere.
        (repo / "Array-types.topic").write_text(
            '<code-block src="exposed-data-types/src/main/kotlin/ArrayExamples.kt"/>\n',
            encoding="utf-8",
        )
        finding = _finding(symbol_name="ArrayExamples", file_path="ArrayExamples.kt")
        _analyzer(repo, [("Array-types.topic", "unknown_language")])._clamp_for_unindexed_importers(
            [finding]
        )
        assert finding.safe_to_delete is False
        assert finding.confidence == RISK_CAP_CONFIDENCE

    def test_internal_findings_are_never_clamped_on_a_name_collision(self, repo: Path) -> None:
        # An importer can only reach what a file exports, so a match on an
        # internal symbol is a collision rather than evidence. Measured, not
        # hypothetical: a published API dump names a public `add`, and without
        # this guard every unrelated private `add` in the repo clamped with it.
        (repo / "surface.api").write_text("public final fun add (I)V\n", encoding="utf-8")
        finding = _finding(symbol_name="add", kind=DeadCodeKind.UNUSED_INTERNAL)
        _analyzer(repo, [("surface.api", "unknown_language")])._clamp_for_unindexed_importers(
            [finding]
        )
        assert finding.safe_to_delete is True
        assert finding.confidence == 0.9
        assert finding.evidence == []
