"""Golden ``ASTParser.parse_file`` output for every file under ``tests/fixtures/lang_samples``.

One golden per language directory pins the whole ``ParsedFile`` (symbols,
imports, calls, heritage, type refs, exports, references and the rest) except
``file_info``'s machine-local fields, so a parser refactor cannot change what a
language extracts unseen. Sources are read with LF endings, as on a Linux
checkout. Rewrite with ``REPOWISE_REWRITE_GOLDEN=1`` only for an intended
behaviour change.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import _detect_language
from tests.unit.ingestion.parser._helpers import _make_file_info

SAMPLES = Path(__file__).resolve().parents[3] / "fixtures" / "lang_samples"
GOLDEN_DIR = Path(__file__).parent / "golden"
LANGUAGES = sorted(p.name for p in SAMPLES.iterdir() if p.is_dir())

# ``file_info`` fields that describe the machine, not the parse.
_VOLATILE_INFO = ("abs_path", "last_modified", "git_hash")


def _plain(value):
    """JSON-ready, with sets in a fixed order."""
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _parse_language(parser: ASTParser, language: str) -> dict:
    out = {}
    for path in sorted(p for p in (SAMPLES / language).rglob("*") if p.is_file()):
        rel = path.relative_to(SAMPLES).as_posix()
        source = path.read_bytes().replace(b"\r\n", b"\n")
        info = _make_file_info(f"lang_samples/{rel}", _detect_language(path))
        info.size_bytes = len(source)
        row = dataclasses.asdict(parser.parse_file(info, source))
        for key in _VOLATILE_INFO:
            row["file_info"].pop(key)
        out[rel] = _plain(row)
    return out


@pytest.mark.parametrize("language", LANGUAGES)
def test_parse_matches_golden(parser: ASTParser, language: str) -> None:
    actual = _parse_language(parser, language)
    golden = GOLDEN_DIR / f"{language}.json"
    if os.environ.get("REPOWISE_REWRITE_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert actual == json.loads(golden.read_text(encoding="utf-8"))
