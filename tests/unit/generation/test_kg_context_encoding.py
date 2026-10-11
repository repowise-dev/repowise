"""``KnowledgeGraphContext`` must not die on a knowledge graph it cannot decode.

``_load`` opened the artifact with a bare ``open()``, which decodes with the
locale codec, cp1252 on a default Windows install. A byte undefined in that
codec (0x81/0x8D/0x8F/0x90/0x9D) raises ``UnicodeDecodeError``, and because
that is a ``ValueError`` rather than an ``OSError`` it escaped the handler and
took the whole generation run down.

Two halves: the file is read as utf-8, and a genuinely undecodable file
degrades to "no KG" instead of raising.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.generation.kg_context import KnowledgeGraphContext


def _write_kg(path: Path, nodes: list[dict]) -> None:
    path.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")


def test_non_ascii_knowledge_graph_loads(tmp_path: Path) -> None:
    """Accented text in a node label is utf-8 on disk and must decode as utf-8,
    whatever the process locale says."""
    kg = tmp_path / ".repowise" / "knowledge-graph.json"
    kg.parent.mkdir(parents=True)
    _write_kg(kg, [{"filePath": "src/app.py", "label": "Ingestión, la búsqueda"}])

    ctx = KnowledgeGraphContext(kg_path=kg)

    assert ctx.available is True


def test_undecodable_knowledge_graph_degrades(tmp_path: Path) -> None:
    kg = tmp_path / ".repowise" / "knowledge-graph.json"
    kg.parent.mkdir(parents=True)
    kg.write_bytes(b'{"nodes": [{"filePath": "\x81\x90 broken"}]}')

    ctx = KnowledgeGraphContext(kg_path=kg)

    assert ctx.available is False
