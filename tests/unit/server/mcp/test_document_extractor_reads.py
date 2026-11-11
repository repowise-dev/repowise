"""MCP text readers use extracted content without treating it as source code."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from repowise.core.ingestion.document_extractors import document_extractors
from repowise.core.registry import plugins
from repowise.server.mcp_server._helpers import read_repo_file_text
from repowise.server.mcp_server.tool_answer.source import _read_repo_text
from repowise.server.mcp_server.tool_context.skeleton import _resolve_skeleton
from tests.unit.ingestion.document_extractor_spawn_plugin import SpawnExtractor


@pytest.fixture
def extractor_registry(monkeypatch):
    monkeypatch.setattr(document_extractors, "_impls", {})
    monkeypatch.setattr(document_extractors, "_chosen", None)
    monkeypatch.setattr(plugins, "load_plugins", lambda: [])
    monkeypatch.setenv(
        "REPOWISE_PLUGINS", "tests.unit.ingestion.document_extractor_spawn_plugin:register"
    )
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_CALLS", "")
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_FAIL", "0")
    document_extractors.register("spawn-test-extractor", SpawnExtractor())


def test_generic_mcp_text_reader_returns_extracted_content(tmp_path, extractor_registry):
    document = tmp_path / "spec.xyz"
    document.write_bytes(b"\x00binary source")

    assert read_repo_file_text(tmp_path, "spec.xyz") == (
        "# Extracted document\n\n\x00binary source\n"
    )


def test_code_source_reader_skips_extracted_documents(tmp_path, extractor_registry):
    document = tmp_path / "spec.xyz"
    document.write_bytes(b"\x00binary source")

    assert _read_repo_text(tmp_path, "spec.xyz") is None


def test_code_source_reader_keeps_a_shebang_script_on_the_code_path(tmp_path, extractor_registry):
    script = tmp_path / "script.xyz"
    script.write_text("#!/usr/bin/env python3\nVALUE = 1\n", encoding="utf-8")

    assert _read_repo_text(tmp_path, "script.xyz") == script.read_text(encoding="utf-8")


def test_shared_source_reader_preserves_text_newline_semantics(tmp_path, extractor_registry):
    source = tmp_path / "main.py"
    source.write_bytes(b"FIRST = 1\r\nSECOND = 2\rTHIRD = 3\n")
    assert _read_repo_text(tmp_path, "main.py") == source.read_text(encoding="utf-8")


def test_extracted_mcp_reads_enforce_input_budget_before_plugin_call(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.ingestion.traverser import _DEFAULT_MAX_FILE_SIZE_BYTES

    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_CALLS", str(calls))
    document = tmp_path / "spec.xyz"
    document.write_bytes(b"x" * (_DEFAULT_MAX_FILE_SIZE_BYTES + 1))

    assert read_repo_file_text(tmp_path, "spec.xyz") is None
    assert not calls.exists()


def test_literal_scan_budgets_include_skipped_documents(tmp_path, extractor_registry, monkeypatch):
    from repowise.server.mcp_server import _line_hits

    (tmp_path / "spec.xyz").write_bytes(b"document")
    monkeypatch.setattr(_line_hits, "MAX_SCAN_FILES", 0)
    rows, reasons = _line_hits.scan_literal(tmp_path, ["spec.xyz"], "document", False)
    assert rows == []
    assert reasons == ["over 0 files"]
    monkeypatch.setattr(_line_hits, "MAX_SCAN_FILES", 10)
    monkeypatch.setattr(_line_hits, "MAX_SCAN_BYTES", 0)
    rows, reasons = _line_hits.scan_literal(tmp_path, ["spec.xyz"], "document", False)
    assert rows == []
    assert "over 0 MB" in reasons[0]


def test_literal_scan_checks_time_before_document_skip(tmp_path, extractor_registry, monkeypatch):
    from repowise.server.mcp_server import _line_hits

    (tmp_path / "spec.xyz").write_bytes(b"document")
    clock = iter([0.0, _line_hits.MAX_SCAN_SECONDS + 1])
    monkeypatch.setattr(_line_hits.time, "monotonic", lambda: next(clock))
    rows, reasons = _line_hits.scan_literal(tmp_path, ["spec.xyz"], "document", False)
    assert rows == []
    assert "time budget" in reasons[0]


def test_literal_scan_keeps_supported_claimed_suffix_scripts(tmp_path, extractor_registry):
    from repowise.server.mcp_server._line_hits import scan_literal

    (tmp_path / "script.xyz").write_bytes(b"#!/usr/bin/env python3\nVALUE = 1\n")
    (tmp_path / "spec.xyz").write_bytes(b"VALUE = document\n")
    rows, reasons = scan_literal(tmp_path, ["spec.xyz", "script.xyz"], "VALUE", False)
    assert reasons == []
    assert [row["path"] for row in rows] == ["script.xyz"]


def test_source_guards_do_not_read_outside_root(tmp_path, extractor_registry, monkeypatch):
    from repowise.server.mcp_server import _edit_sites

    outside = tmp_path / "outside.xyz"
    outside.write_bytes(b"document")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "link.xyz").symlink_to(outside)
    assert _read_repo_text(root, "link.xyz") is None
    assert read_repo_file_text(root, "link.xyz") is None
    assert _edit_sites._live_definition(
        root, SimpleNamespace(file_path="../outside.xyz"), "name", "python"
    ) == (0, 0)


def test_symbol_reads_do_not_expose_generated_source_coordinates(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.ingestion import source_reader
    from repowise.server.mcp_server.tool_symbol.misses import _live_grep_fallback
    from repowise.server.mcp_server.tool_symbol.text import _read_file_text

    (tmp_path / "spec.xyz").write_bytes(b"binary document")

    def unexpected_conversion(*args, **kwargs):
        pytest.fail("Source-only reads must not invoke extraction")

    monkeypatch.setattr(source_reader, "extract_documents_bounded", unexpected_conversion)
    assert _read_file_text(tmp_path, "spec.xyz") is None
    assert _live_grep_fallback(tmp_path, "spec.xyz", "synthetic") == []
    (tmp_path / "main.py").write_text("def synthetic(): pass\n")
    assert _live_grep_fallback(tmp_path, "main.py", "synthetic")[0]["line"] == 1


@pytest.mark.asyncio
async def test_symbol_range_rejects_extracted_document(
    tmp_path, extractor_registry, monkeypatch, setup_mcp
):
    from repowise.core.ingestion import source_reader
    from repowise.server.mcp_server import get_symbol

    (tmp_path / "spec.xyz").write_bytes(b"binary document")
    monkeypatch.setattr(source_reader, "extract_documents_bounded", lambda *a, **k: [b"# Generated\n"])
    result = await get_symbol("spec.xyz:1-3")
    assert "error" in result
    assert not result.get("verified")
    assert "source" not in result


def test_optional_preview_degrades_on_extraction_failure(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.ingestion import document_extractors as extraction
    from repowise.core.ingestion import source_reader
    from repowise.server.mcp_server.tool_context.preview import _file_preview

    (tmp_path / "spec.xyz").write_bytes(b"binary document")

    def fail(*args, **kwargs):
        raise extraction.DocumentExtractionError("converter failed")

    monkeypatch.setattr(source_reader, "extract_documents_bounded", fail)
    assert _file_preview(tmp_path, "spec.xyz") is None


@pytest.mark.parametrize("preview_stage", ["resolution", "card"])
@pytest.mark.asyncio
async def test_document_preview_does_not_block_event_loop(
    tmp_path, extractor_registry, monkeypatch, preview_stage
):
    from repowise.core.ingestion import source_reader
    from repowise.server.mcp_server.tool_context.resolution import _live_file
    from repowise.server.mcp_server.tool_context.targets import _attach_file_preview

    (tmp_path / "spec.xyz").write_bytes(b"binary document")
    started = threading.Event()
    release = threading.Event()

    def convert(*args, **kwargs):
        started.set()
        assert release.wait(2), "Event loop did not release the converter"
        return [b"# Converted\n"]

    monkeypatch.setattr(source_reader, "extract_documents_bounded", convert)
    query = SimpleNamespace(repo_root=tmp_path, target="spec.xyz")
    docs = {}
    operation = (
        _live_file(query) if preview_stage == "resolution"
        else _attach_file_preview(SimpleNamespace(query=query, target="spec.xyz"), docs)
    )
    task = asyncio.create_task(operation)
    try:
        async with asyncio.timeout(1):
            while not started.is_set():
                await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        result = await task
    if preview_stage == "resolution":
        assert result.live_unindexed_file
    else:
        assert docs["file_preview"]["head"] == ["# Converted"]


@pytest.mark.parametrize("document_alias", [False, True])
def test_symlink_routing_uses_indexed_path(
    tmp_path, extractor_registry, monkeypatch, document_alias
):
    from repowise.core.ingestion import source_reader
    from repowise.core.ingestion.traverser import FileTraverser
    from repowise.server.mcp_server import _code_rationale, _edit_sites
    from repowise.server.mcp_server.tool_answer.data_shape import _read_lines
    from repowise.server.mcp_server.tool_answer.projection import _file_size
    from repowise.server.mcp_server.tool_symbol.text import _read_file_text

    target = tmp_path / ("implementation.py" if document_alias else "implementation.xyz")
    alias = tmp_path / ("alias.xyz" if document_alias else "alias.py")
    target.write_text("VALUE = 1\n")
    alias.symlink_to(target.name)
    files = {info.path: info for info in FileTraverser(tmp_path).traverse()}
    assert files[alias.name].language == ("markdown" if document_alias else "python")
    calls = []

    def convert(requests, **kwargs):
        calls.extend(request[2] for request in requests)
        return [b"# Converted\n"] * len(requests)

    monkeypatch.setattr(source_reader, "extract_documents_bounded", convert)
    assert read_repo_file_text(tmp_path, alias.name) == (
        "# Converted\n" if document_alias else "VALUE = 1\n"
    )
    assert calls == ([alias] if document_alias else [])
    for read in (_read_repo_text, _read_file_text, _code_rationale._read_text):
        assert read(tmp_path, alias.name) == (None if document_alias else "VALUE = 1\n")
    for read in (_read_lines, _edit_sites._read_lines):
        assert read(tmp_path, alias.name) == (None if document_alias else ["VALUE = 1"])
    assert _file_size(tmp_path, alias.name) == (
        None if document_alias else (1, len(b"VALUE = 1\n"))
    )
    if document_alias:
        assert _edit_sites._live_definition(
            tmp_path, SimpleNamespace(file_path=alias.name), "VALUE", "python"
        ) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink", [False, True])
async def test_skeleton_reader_skips_extracted_documents(tmp_path, extractor_registry, symlink):
    document = tmp_path / "spec.xyz"
    if symlink:
        (tmp_path / "implementation.py").write_text("VALUE = 1\n")
        document.symlink_to("implementation.py")
    else:
        document.write_bytes(b"\x00binary source")
    result: dict = {}

    class EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class EmptySession:
        async def execute(self, _query):
            return EmptyResult()

    await _resolve_skeleton(
        session=EmptySession(),
        repository=SimpleNamespace(id=1),
        target="spec.xyz",
        target_type="file",
        result_data=result,
        repo_root=tmp_path,
    )

    assert result["skeleton"]["error"] == (
        "Source skeletons are not available for extracted documents."
    )
