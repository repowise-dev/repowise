"""Tests for post-generation KG enrichment (Phase 11)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from repowise.core.generation.kg_enrichment import enrich_tour_with_wiki_links
from repowise.core.generation.onboarding.slots import (
    ONBOARDING_ORDER,
    SLOT_PREREQUISITES,
    SLOT_ACTIVE_LANDSCAPE,
    SLOT_CODEBASE_MAP,
    SLOT_DEVELOPMENT_GUIDE,
    SLOT_HOW_IT_WORKS,
    SLOT_KEY_CONCEPTS,
    SLOT_PROJECT_OVERVIEW,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakePage:
    page_id: str
    target_path: str
    page_type: str = "file_page"


def _write_kg(tmp_path: Path, tour: list[dict], **extra: object) -> Path:
    kg = {"nodes": [], "edges": [], "layers": [], "tour": tour, **extra}
    kg_path = tmp_path / "knowledge-graph.json"
    kg_path.write_text(json.dumps(kg), encoding="utf-8")
    return kg_path


# ---------------------------------------------------------------------------
# enrich_tour_with_wiki_links
# ---------------------------------------------------------------------------


class TestEnrichTourWithWikiLinks:
    def test_adds_wiki_page_ids(self, tmp_path):
        kg_path = _write_kg(tmp_path, tour=[
            {"order": 1, "title": "Entry", "nodeIds": ["file:src/main.py", "file:src/utils.py"]},
            {"order": 2, "title": "Core", "nodeIds": ["file:src/core.py"]},
        ])
        pages = [
            FakePage(page_id="file_page:src/main.py", target_path="src/main.py"),
            FakePage(page_id="file_page:src/core.py", target_path="src/core.py"),
        ]
        count = enrich_tour_with_wiki_links(kg_path, pages)
        assert count == 2

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:src/main.py"]
        assert kg["tour"][1]["wikiPageIds"] == ["file_page:src/core.py"]

    def test_missing_pages_get_empty_list(self, tmp_path):
        kg_path = _write_kg(tmp_path, tour=[
            {"order": 1, "title": "Entry", "nodeIds": ["file:src/missing.py"]},
        ])
        count = enrich_tour_with_wiki_links(kg_path, [])
        assert count == 0

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == []

    def test_preserves_existing_kg_data(self, tmp_path):
        kg_path = _write_kg(tmp_path, tour=[
            {"order": 1, "title": "Entry", "nodeIds": ["file:a.py"]},
        ], version="1.0.0", project={"name": "test"})
        pages = [FakePage(page_id="file_page:a.py", target_path="a.py")]
        enrich_tour_with_wiki_links(kg_path, pages)

        kg = json.loads(kg_path.read_text())
        assert kg["version"] == "1.0.0"
        assert kg["project"]["name"] == "test"
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:a.py"]

    def test_multiple_files_in_step(self, tmp_path):
        kg_path = _write_kg(tmp_path, tour=[
            {"order": 1, "title": "Step", "nodeIds": [
                "file:a.py", "file:b.py", "file:c.py",
            ]},
        ])
        pages = [
            FakePage(page_id="file_page:a.py", target_path="a.py"),
            FakePage(page_id="file_page:c.py", target_path="c.py"),
        ]
        count = enrich_tour_with_wiki_links(kg_path, pages)
        assert count == 1

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:a.py", "file_page:c.py"]

    def test_non_file_node_ids_skipped(self, tmp_path):
        kg_path = _write_kg(tmp_path, tour=[
            {"order": 1, "title": "Step", "nodeIds": [
                "class:src/models.py:User", "file:src/models.py",
            ]},
        ])
        pages = [
            FakePage(page_id="file_page:src/models.py", target_path="src/models.py"),
        ]
        enrich_tour_with_wiki_links(kg_path, pages)

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:src/models.py"]

    def test_empty_tour_returns_zero(self, tmp_path):
        kg_path = _write_kg(tmp_path, tour=[])
        count = enrich_tour_with_wiki_links(kg_path, [])
        assert count == 0

    def test_invalid_json_returns_zero(self, tmp_path):
        kg_path = tmp_path / "knowledge-graph.json"
        kg_path.write_text("not json", encoding="utf-8")
        count = enrich_tour_with_wiki_links(kg_path, [])
        assert count == 0

    def test_missing_file_returns_zero(self, tmp_path):
        kg_path = tmp_path / "nonexistent.json"
        count = enrich_tour_with_wiki_links(kg_path, [])
        assert count == 0

    def test_idempotent(self, tmp_path):
        kg_path = _write_kg(tmp_path, tour=[
            {"order": 1, "title": "Entry", "nodeIds": ["file:a.py"]},
        ])
        pages = [FakePage(page_id="file_page:a.py", target_path="a.py")]
        enrich_tour_with_wiki_links(kg_path, pages)
        enrich_tour_with_wiki_links(kg_path, pages)

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:a.py"]


# ---------------------------------------------------------------------------
# Onboarding prerequisites
# ---------------------------------------------------------------------------


class TestOnboardingPrerequisites:
    def test_all_slots_have_prerequisites(self):
        for slot in ONBOARDING_ORDER:
            assert slot in SLOT_PREREQUISITES, f"Missing prerequisites for {slot}"

    def test_root_slots_have_no_prerequisites(self):
        assert SLOT_PREREQUISITES[SLOT_PROJECT_OVERVIEW] == ()

    def test_downstream_slots_reference_valid_slots(self):
        all_slots = set(ONBOARDING_ORDER)
        for slot, prereqs in SLOT_PREREQUISITES.items():
            for prereq in prereqs:
                assert prereq in all_slots, f"{slot} prereq {prereq} not in ONBOARDING_ORDER"

    def test_no_circular_prerequisites(self):
        visited: set[str] = set()

        def _walk(slot: str, path: frozenset[str]) -> None:
            assert slot not in path, f"Circular prerequisite: {slot} in {path}"
            if slot in visited:
                return
            visited.add(slot)
            for prereq in SLOT_PREREQUISITES.get(slot, ()):
                _walk(prereq, path | {slot})

        for slot in ONBOARDING_ORDER:
            _walk(slot, frozenset())

    def test_prerequisites_respect_ordering(self):
        order_index = {s: i for i, s in enumerate(ONBOARDING_ORDER)}
        for slot, prereqs in SLOT_PREREQUISITES.items():
            for prereq in prereqs:
                assert order_index[prereq] < order_index[slot], (
                    f"{slot} (index {order_index[slot]}) has prereq {prereq} "
                    f"(index {order_index[prereq]}) which comes later"
                )

    def test_active_landscape_depends_on_codebase_map(self):
        assert SLOT_CODEBASE_MAP in SLOT_PREREQUISITES[SLOT_ACTIVE_LANDSCAPE]

    def test_how_it_works_depends_on_key_concepts(self):
        assert SLOT_KEY_CONCEPTS in SLOT_PREREQUISITES[SLOT_HOW_IT_WORKS]
