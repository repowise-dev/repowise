"""Tests for post-generation KG enrichment (Phase 11)."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from repowise.core.generation import onboarding
from repowise.core.generation.kg_enrichment import enrich_tour_with_wiki_links
from repowise.core.generation.onboarding import slots as slots_module

SLOTS_PATH = Path(slots_module.__file__).resolve()
# The installed product tree, so "is anything reading this?" is asked of the
# packages rather than of the tests that assert on them.
PACKAGES_ROOT = Path(onboarding.__file__).resolve().parents[4]

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
        kg_path = _write_kg(
            tmp_path,
            tour=[
                {
                    "order": 1,
                    "title": "Entry",
                    "nodeIds": ["file:src/main.py", "file:src/utils.py"],
                },
                {"order": 2, "title": "Core", "nodeIds": ["file:src/core.py"]},
            ],
        )
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
        kg_path = _write_kg(
            tmp_path,
            tour=[
                {"order": 1, "title": "Entry", "nodeIds": ["file:src/missing.py"]},
            ],
        )
        count = enrich_tour_with_wiki_links(kg_path, [])
        assert count == 0

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == []

    def test_preserves_existing_kg_data(self, tmp_path):
        kg_path = _write_kg(
            tmp_path,
            tour=[
                {"order": 1, "title": "Entry", "nodeIds": ["file:a.py"]},
            ],
            version="1.0.0",
            project={"name": "test"},
        )
        pages = [FakePage(page_id="file_page:a.py", target_path="a.py")]
        enrich_tour_with_wiki_links(kg_path, pages)

        kg = json.loads(kg_path.read_text())
        assert kg["version"] == "1.0.0"
        assert kg["project"]["name"] == "test"
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:a.py"]

    def test_multiple_files_in_step(self, tmp_path):
        kg_path = _write_kg(
            tmp_path,
            tour=[
                {
                    "order": 1,
                    "title": "Step",
                    "nodeIds": [
                        "file:a.py",
                        "file:b.py",
                        "file:c.py",
                    ],
                },
            ],
        )
        pages = [
            FakePage(page_id="file_page:a.py", target_path="a.py"),
            FakePage(page_id="file_page:c.py", target_path="c.py"),
        ]
        count = enrich_tour_with_wiki_links(kg_path, pages)
        assert count == 1

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:a.py", "file_page:c.py"]

    def test_non_file_node_ids_skipped(self, tmp_path):
        kg_path = _write_kg(
            tmp_path,
            tour=[
                {
                    "order": 1,
                    "title": "Step",
                    "nodeIds": [
                        "class:src/models.py:User",
                        "file:src/models.py",
                    ],
                },
            ],
        )
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
        kg_path = _write_kg(
            tmp_path,
            tour=[
                {"order": 1, "title": "Entry", "nodeIds": ["file:a.py"]},
            ],
        )
        pages = [FakePage(page_id="file_page:a.py", target_path="a.py")]
        enrich_tour_with_wiki_links(kg_path, pages)
        enrich_tour_with_wiki_links(kg_path, pages)

        kg = json.loads(kg_path.read_text())
        assert kg["tour"][0]["wikiPageIds"] == ["file_page:a.py"]


# ---------------------------------------------------------------------------
# Onboarding slot tables
# ---------------------------------------------------------------------------


class TestOnboardingTablesAreRead:
    """Every lookup table in ``slots.py`` must have a reader in the product.

    A table that declares behaviour nothing implements is worse than no table:
    it reads as a live feature to anyone deciding what onboarding already does.
    This is derived from the source rather than a fixed list, so a new unread
    table fails here instead of quietly joining the file.

    Tests asserting a table's shape cannot catch this — they pass whether or
    not anything consults it, which is how one such table survived for months.
    """

    def _tables(self) -> dict[str, ast.AST]:
        """Module-level collection constants — the shape a lookup table takes."""
        tree = ast.parse(SLOTS_PATH.read_text())
        found: dict[str, ast.AST] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, ast.Dict | ast.Tuple | ast.List):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    found[target.id] = value
        return found

    def _readers(self, name: str) -> list[Path]:
        return [
            path
            for path in PACKAGES_ROOT.rglob("*.py")
            # Explicit utf-8: this sweeps every source file in the repo, and a
            # bare read_text() decodes with the locale codec, so on Windows the
            # first module holding a non-cp1252 byte failed the whole test.
            if path != SLOTS_PATH and name in path.read_text(encoding="utf-8")
        ]

    def test_every_table_has_a_reader(self):
        tables = self._tables()

        # Anti-vacuous: a parse that found nothing would pass the loop below
        # without checking anything at all.
        assert len(tables) >= 3, f"Parsed too few tables from {SLOTS_PATH.name}: {sorted(tables)}"
        assert "ONBOARDING_ORDER" in tables

        unread = [name for name in tables if not self._readers(name)]
        assert not unread, (
            f"Declared in {SLOTS_PATH.name} and read by nothing in the product: "
            f"{sorted(unread)}. Wire it up or delete it."
        )
