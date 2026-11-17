"""Java record accessors carry their return type, so a chain through one resolves."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _parse(tmp_path: Path, rel: str, src: str) -> ParsedFile:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src)
    info = FileInfo(
        path=rel,
        abs_path=str(f),
        language="java",
        size_bytes=f.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return parse_file(info, src.encode("utf-8"))


_FILES = {
    "src/Interval.java": (
        "package p;\n"
        "public class Interval {\n"
        "    public String format() { return \"\"; }\n"
        "}\n"
    ),
    "src/Polling.java": (
        "package p;\n"
        "public record Polling(Interval interval, String name) {}\n"
    ),
    "src/Use.java": (
        "package p;\n"
        "public class Use {\n"
        "    String run(Polling polling) {\n"
        "        return polling.interval().format();\n"
        "    }\n"
        "}\n"
    ),
}


def test_accessor_signature_declares_its_type(tmp_path: Path) -> None:
    pf = _parse(tmp_path, "src/Polling.java", _FILES["src/Polling.java"])
    sigs = {s.name: s.signature for s in pf.symbols}
    assert sigs["interval"] == "interval() -> Interval"
    assert sigs["toString"] == "toString() -> String"
    assert sigs["equals"] == "equals(Object o) -> boolean"


def test_a_chain_through_an_accessor_resolves(tmp_path: Path) -> None:
    parsed = {rel: _parse(tmp_path, rel, src) for rel, src in _FILES.items()}
    resolver = CallResolver(parsed, {p: set() for p in parsed}, repo_path=str(tmp_path))
    callees = {rc.callee_id for rc in resolver.resolve_file("src/Use.java", parsed["src/Use.java"].calls)}
    assert "src/Polling.java::Polling::interval" in callees
    assert "src/Interval.java::Interval::format" in callees
