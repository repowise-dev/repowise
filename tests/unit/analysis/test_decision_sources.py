"""Deterministic (LLM-free) extraction from ADRs and CHANGELOGs (Phase 1B)."""

from __future__ import annotations

from repowise.core.analysis.decision_extractor import (
    SOURCE_NAMES,
    DecisionExtractor,
    enabled_source_names,
)

_NYGARD_ADR = """\
# 1. Use PostgreSQL for primary storage

Date: 2026-01-04

## Status

Accepted

## Context

We need a relational store with strong transactional guarantees and JSON support.

## Decision

We will use PostgreSQL as the primary datastore for all services.

## Consequences

- Operational familiarity across the team.
- Requires a managed Postgres instance per environment.
"""

_MADR_ADR = """\
---
status: superseded
title: Adopt gRPC for service-to-service calls
---

## Context and Problem Statement

REST over JSON was adding latency on the hot path between internal services.

## Decision Outcome

Adopt gRPC for all internal service-to-service communication.
"""

_CHANGELOG = """\
# Changelog

## [2.0.0] - 2026-05-01

### Changed
- Migrate the auth layer from sessions to JWT tokens.

### Removed
- Drop support for the legacy XML config format.

### Added
- New dashboard widget.
"""


async def test_discover_adrs_parses_nygard_template(tmp_path):
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgres.md").write_text(_NYGARD_ADR, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)  # no provider → deterministic only
    decisions = await ex.discover_adrs()

    assert len(decisions) == 1
    d = decisions[0]
    assert d.source == "adr"
    assert d.status == "active"  # "Accepted" → active
    assert "Use PostgreSQL" in d.title
    assert "PostgreSQL as the primary datastore" in d.decision
    assert "managed Postgres instance per environment." in d.consequences[-1]
    # Deterministic parse lifts fields verbatim → grounded source span.
    assert "PostgreSQL as the primary datastore" in d.source_text


async def test_discover_adrs_maps_superseded_status_from_frontmatter(tmp_path):
    adr_dir = tmp_path / "decisions"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0007-grpc.md").write_text(_MADR_ADR, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)
    decisions = await ex.discover_adrs()

    assert len(decisions) == 1
    d = decisions[0]
    assert d.status == "superseded"
    assert d.title == "Adopt gRPC for service-to-service calls"
    assert "Adopt gRPC" in d.decision


async def test_mine_changelog_extracts_decision_sections(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)  # no provider → raw bullets
    decisions = await ex.mine_changelog()

    texts = [d.decision for d in decisions]
    assert any("Migrate the auth layer" in t for t in texts)
    assert any("Drop support for the legacy XML" in t for t in texts)
    # "Added" section is intentionally excluded — it is not a structural decision.
    assert not any("dashboard widget" in t for t in texts)
    assert all(d.source == "changelog" for d in decisions)


async def test_mine_changelog_finds_changelog_under_docs(tmp_path):
    # Many projects keep the changelog under docs/ rather than at the root.
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)  # no provider → raw bullets
    decisions = await ex.mine_changelog()

    texts = [d.decision for d in decisions]
    assert any("Migrate the auth layer" in t for t in texts)
    assert all(d.source == "changelog" for d in decisions)


async def test_extract_all_runs_deterministic_sources_and_gates(tmp_path):
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgres.md").write_text(_NYGARD_ADR, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)
    report = await ex.extract_all()

    assert report.by_source["adr"] == 1
    assert report.by_source["changelog"] >= 2
    # All survive the gate (deterministic fields are verbatim).
    sources = {d.source for d in report.decisions}
    assert "adr" in sources
    assert "changelog" in sources
    adr = next(d for d in report.decisions if d.source == "adr")
    assert adr.verification == "exact"
    # The repo-wide code_comment harvest was removed (#751); extract_all must
    # neither run it nor report it.
    assert "code_comment" not in report.by_source
    assert not any(d.source == "code_comment" for d in report.decisions)


async def test_extract_all_honors_enabled_sources(tmp_path):
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgres.md").write_text(_NYGARD_ADR, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)
    seen: list[str] = []
    report = await ex.extract_all(
        on_step=seen.append,
        enabled_sources=("adr", "inline_marker"),
    )

    assert set(seen) == {"adr", "inline_marker"}
    assert set(report.by_source) == {"adr", "inline_marker"}
    assert report.by_source["adr"] == 1
    # The changelog file exists but its source was disabled.
    assert not any(d.source == "changelog" for d in report.decisions)


def test_enabled_source_names_defaults_and_overrides():
    # No config → everything on.
    assert enabled_source_names(None) == SOURCE_NAMES
    assert enabled_source_names({}) == SOURCE_NAMES

    cfg = {"decisions": {"sources": {"comment": False, "changelog": False}}}
    enabled = enabled_source_names(cfg)
    assert "comment" not in enabled
    assert "changelog" not in enabled
    assert "adr" in enabled and "inline_marker" in enabled

    # Unknown / stale keys (e.g. the removed code_comment) are ignored.
    assert enabled_source_names({"decisions": {"sources": {"code_comment": False}}}) == (
        SOURCE_NAMES
    )
    # Malformed sections never break extraction.
    assert enabled_source_names({"decisions": "nope"}) == SOURCE_NAMES
    assert enabled_source_names({"decisions": {"sources": "nope"}}) == SOURCE_NAMES
