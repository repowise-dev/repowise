from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "architecture" / "language-support.md"
MODELS = ROOT / "packages" / "core" / "src" / "repowise" / "core" / "ingestion" / "models.py"


def test_language_support_edge_type_sets_are_real_model_symbols() -> None:
    text = DOC.read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"`([A-Z][A-Z0-9_]*_EDGE_TYPES)`", text)))
    assert names, "language-support.md documents no *_EDGE_TYPES identifiers"

    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    attributes = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        )
        if isinstance(target, ast.Name)
    }
    missing = sorted(set(names) - attributes)
    assert not missing, f"docs reference missing ingestion.models symbols: {missing}"
