"""A default export imported under another local name is used.

``import mathRule from "./math"`` binds ``default``. A named default
declaration (``export default function markdownMath``) publishes its symbol as
``default``, which is the only link from the import back to it; without it the
function reads as unused and deletion-ready.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.pipeline.incremental import build_repo_graph


def _unused(root: Path, files: dict[str, str]) -> set[str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _parsed, source_map, builder, _structure, _count = build_repo_graph(
        root, [], collect_sources=True, read_only=True
    )
    report = DeadCodeAnalyzer(
        builder.graph(),
        {},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
    ).analyze({"min_confidence": 0.0})
    return {f.symbol_name for f in report.findings if f.symbol_name}


_IMPORTER = {
    "src/node.ts": 'import mathRule, { REGEX } from "./math";\n'
    "export function use() { return [mathRule, REGEX]; }\n",
    "src/index.ts": 'import { use } from "./node";\nconsole.log(use());\n',
}


@pytest.mark.parametrize(
    "math",
    [
        "export const REGEX = /x/;\nexport default function markdownMath(md: unknown) {\n  return md;\n}\n",
        "export const REGEX = /x/;\nfunction markdownMath(md: unknown) {\n  return md;\n}\n"
        "export default markdownMath;\n",
        "export const REGEX = /x/;\nexport default class markdownMath {}\n",
        "export const REGEX = /x/;\nexport default async function markdownMath() {}\n",
        "export const REGEX = /x/;\nexport default abstract class markdownMath {}\n",
    ],
)
def test_a_renamed_default_import_uses_the_default_export(tmp_path: Path, math: str) -> None:
    unused = _unused(tmp_path, {**_IMPORTER, "src/math.ts": math})
    assert "markdownMath" not in unused


def test_a_default_export_nobody_imports_is_still_reported(tmp_path: Path) -> None:
    files = {
        "src/node.ts": 'import { REGEX } from "./math";\nexport function use() { return REGEX; }\n',
        "src/index.ts": _IMPORTER["src/index.ts"],
        "src/math.ts": "export const REGEX = /x/;\nexport const spare = 1;\n"
        "export default function markdownMath(md: unknown) {\n  return md;\n}\n",
    }
    unused = _unused(tmp_path, files)
    assert {"markdownMath", "spare"} <= unused
