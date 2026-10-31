"""Plugin routing and cache behaviour for unsupported document types."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.plugins_cmd import plugins_command
from repowise.core.ingestion.change_detector import ChangeDetector
from repowise.core.ingestion.document_extractors import (
    DocumentExtractionError,
    document_extractors,
    is_extracted_document,
)
from repowise.core.ingestion.traverser import FileTraverser, is_candidate_source_path
from repowise.core.pipeline.phases import ingestion as ingestion_phase
from repowise.core.registry import plugins
from tests.unit.ingestion.document_extractor_spawn_plugin import SpawnExtractor

SPAWN_PLUGIN = "tests.unit.ingestion.document_extractor_spawn_plugin:register"


@dataclass
class FakeExtractor:
    extensions: frozenset[str] = frozenset({".xyz"})
    version: str = "1"
    output: str = "# Extracted\n\nThe document content.\n"
    calls: int = 0
    failure: Exception | None = None

    def extract(self, path: Path, data: bytes) -> str:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.output


@pytest.fixture
def extractor_registry(monkeypatch):
    monkeypatch.setattr(document_extractors, "_impls", {})
    monkeypatch.setattr(document_extractors, "_chosen", None)
    monkeypatch.setattr(plugins, "load_plugins", lambda: [])


def _register(name: str, extractor: FakeExtractor) -> None:
    document_extractors.register(name, extractor)


def _register_spawn_extractor(
    monkeypatch, calls_file: Path, *, fail: bool = False, hang: bool = False
) -> None:
    monkeypatch.setenv("REPOWISE_PLUGINS", SPAWN_PLUGIN)
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_CALLS", str(calls_file))
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_FAIL", "1" if fail else "0")
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_HANG", "1" if hang else "0")
    document_extractors.register("spawn-test-extractor", SpawnExtractor())


def _extractor_calls(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


async def _ingest(repo: Path):
    return await ingestion_phase._run_ingestion(
        repo,
        exclude_patterns=None,
        skip_tests=False,
        skip_infra=False,
        progress=None,
    )


def test_traversal_routes_only_unsupported_claims(tmp_path, extractor_registry):
    first = FakeExtractor()
    later = FakeExtractor(output="# Later extractor\n")
    python_extractor = FakeExtractor(extensions=frozenset({".py"}))
    _register("first", first)
    _register("later", later)
    _register("python-override", python_extractor)
    (tmp_path / "spec.xyz").write_bytes(b"\x00binary source")
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "script.xyz").write_text("#!/usr/bin/env python3\nVALUE = 2\n", encoding="utf-8")

    files = {info.path: info for info in FileTraverser(tmp_path).traverse()}

    assert files["spec.xyz"].language == "markdown"
    assert files["spec.xyz"].extractor_name == "later"
    assert files["spec.xyz"].extractor_version == "1"
    assert files["main.py"].language == "python"
    assert files["main.py"].extractor_name is None
    assert files["script.xyz"].language == "python"
    assert files["script.xyz"].extractor_name is None
    assert first.calls == later.calls == python_extractor.calls == 0


def test_registration_rejects_invalid_extractor_contract(extractor_registry):
    with pytest.raises(ValueError, match="invalid extension"):
        document_extractors.register("bad-extractor", FakeExtractor(extensions=frozenset({"XYZ"})))

    with pytest.raises(ValueError, match="version"):
        document_extractors.register("missing-version", FakeExtractor(version=""))

    _register("valid", FakeExtractor())
    with pytest.raises(ValueError):
        document_extractors.register("valid", FakeExtractor(extensions=frozenset({"pdf"})))
    assert dict(document_extractors.all())["valid"].extensions == frozenset({".xyz"})


@pytest.mark.parametrize("extension", [".tar.xyz", ".drawio.xml", "..xyz"])
def test_registration_rejects_compound_extensions(extractor_registry, extension):
    with pytest.raises(ValueError, match="invalid extension"):
        _register("compound", FakeExtractor(extensions=frozenset({extension})))
    assert not document_extractors.all()


def test_validation_survives_a_preexisting_generic_seam(monkeypatch):
    from repowise.core.ingestion.document_extractors import DocumentExtractorRegistry

    monkeypatch.setattr(plugins, "_SEAMS", {})
    generic = plugins.SeamRegistry("document-extractors")
    registry = DocumentExtractorRegistry("document-extractors")
    assert registry is generic
    with pytest.raises(ValueError, match="invalid extension"):
        generic.register("invalid", FakeExtractor(extensions=frozenset({"pdf"})))


def test_invalid_plugin_registration_is_reported_without_poisoning_valid_claims(
    extractor_registry, monkeypatch
):
    from types import SimpleNamespace

    from repowise.core.ingestion.document_extractors import document_extractor_claims

    _register("valid", FakeExtractor())
    target = SimpleNamespace(
        register=lambda: _register("bad", FakeExtractor(extensions=frozenset({"pdf"})))
    )
    monkeypatch.setattr(plugins.importlib, "import_module", lambda _name: target)
    record = plugins.PluginRecord("bad-plugin", "env", "bad_plugin:register")
    plugins._load(record)
    assert record.status == "failed"
    assert "invalid extension" in record.error
    assert document_extractor_claims() == {".xyz": "valid"}


def test_content_shebang_keeps_claimed_extension_on_code_path(tmp_path, extractor_registry):
    extractor = FakeExtractor()
    _register("test-extractor", extractor)
    script = tmp_path / "script.xyz"
    script.write_bytes(b"#!/usr/bin/env python3\nVALUE = 1\n")

    assert not is_extracted_document(script)
    assert is_candidate_source_path("script.xyz", tmp_path)
    parsed = ChangeDetector(tmp_path).parse_bytes(script.read_bytes(), "script.xyz")

    assert parsed is not None
    assert parsed.file_info.language == "python"
    assert parsed.file_info.extractor_name is None
    assert extractor.calls == 0


def test_supplied_shebang_bytes_never_read_live_source(tmp_path, extractor_registry, monkeypatch):
    from repowise.core.ingestion.traverser import _detect_language

    _register("test-extractor", FakeExtractor())
    script = tmp_path / "script.xyz"
    script.write_bytes(b"#!/bin/sh\n")
    monkeypatch.chdir(tmp_path.parent)
    assert _detect_language(script, b"#!/usr/bin/env python3\nVALUE = 1\n") == "python"
    assert _detect_language(script, b"") == "unknown"
    parsed = ChangeDetector(tmp_path).parse_bytes(
        b"#!/usr/bin/env python3\nVALUE = 1\n", "script.xyz"
    )
    assert parsed.file_info.language == "python"


@pytest.mark.asyncio
async def test_parse_cache_skips_extraction_until_source_or_version_changes(
    tmp_path, extractor_registry, monkeypatch
):
    calls_file = tmp_path / "extractor-calls.txt"
    _register_spawn_extractor(monkeypatch, calls_file)
    path = tmp_path / "docs" / "spec.xyz"
    path.parent.mkdir()
    path.write_bytes(b"source-v1")

    first, _file_infos, _structure, source_map, *_ = await _ingest(tmp_path)
    assert _extractor_calls(calls_file) == ["spec.xyz"]
    extracted = next(parsed for parsed in first if parsed.file_info.path == "docs/spec.xyz")
    expected = b"# Extracted document\n\nsource-v1\n"
    assert extracted.file_info.language == "markdown"
    assert source_map["docs/spec.xyz"] == expected
    assert not path.with_suffix(".md").exists()

    await _ingest(tmp_path)
    assert _extractor_calls(calls_file) == ["spec.xyz"]

    path.write_bytes(b"source-v2")
    await _ingest(tmp_path)
    assert _extractor_calls(calls_file) == ["spec.xyz", "spec.xyz"]

    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_VERSION", "2")
    document_extractors.register("spawn-test-extractor", SpawnExtractor())
    await _ingest(tmp_path)
    assert _extractor_calls(calls_file) == ["spec.xyz", "spec.xyz", "spec.xyz"]


@pytest.mark.asyncio
async def test_reparse_uses_cached_markdown_in_its_source_map(
    tmp_path, extractor_registry, monkeypatch
):
    calls_file = tmp_path / "extractor-calls.txt"
    _register_spawn_extractor(monkeypatch, calls_file)
    (tmp_path / "spec.xyz").write_bytes(b"source")
    await _ingest(tmp_path)

    from repowise.core.pipeline.reparse import reparse_repo

    parsed, source_map, _structure = reparse_repo(tmp_path, [])

    assert [item.file_info.path for item in parsed] == ["spec.xyz"]
    assert source_map["spec.xyz"] == b"# Extracted document\n\nsource\n"
    assert _extractor_calls(calls_file) == ["spec.xyz"]


@pytest.mark.asyncio
async def test_reparse_leaves_the_parse_cache_file_untouched(tmp_path, extractor_registry):
    (tmp_path / "app.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    await _ingest(tmp_path)
    cache_file = tmp_path / ".repowise" / "parse_cache.pkl"
    before = cache_file.read_bytes()
    (tmp_path / "app.py").write_text("def two():\n    return 2\n", encoding="utf-8")

    from repowise.core.pipeline.reparse import reparse_repo

    parsed, _source_map, _structure = reparse_repo(tmp_path, [])

    assert [s.name for item in parsed for s in item.symbols] == ["two"]
    assert cache_file.read_bytes() == before


def test_reparse_extracts_without_writing_the_document_cache(
    tmp_path, extractor_registry, monkeypatch
):
    calls_file = tmp_path / "extractor-calls.txt"
    _register_spawn_extractor(monkeypatch, calls_file)
    (tmp_path / "spec.xyz").write_bytes(b"source")

    from repowise.core.pipeline.reparse import reparse_repo

    _parsed, source_map, _structure = reparse_repo(tmp_path, [])

    assert source_map["spec.xyz"] == b"# Extracted document\n\nsource\n"
    assert _extractor_calls(calls_file) == ["spec.xyz"]
    document_cache = tmp_path / ".repowise" / "document-cache"
    assert not document_cache.exists() or not any(document_cache.iterdir())
    assert not (tmp_path / ".repowise" / "parse_cache.pkl").exists()


def test_extractor_failure_names_plugin_and_path(tmp_path, extractor_registry, monkeypatch):
    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt", fail=True)
    path = tmp_path / "spec.xyz"
    path.write_bytes(b"\x00binary source")
    file_info = next(iter(FileTraverser(tmp_path).traverse()))

    with pytest.raises(DocumentExtractionError, match=r"spawn-test-extractor.*spec\.xyz"):
        ingestion_phase._split_cached(tmp_path, [(file_info, path.read_bytes())], None)


@pytest.mark.asyncio
async def test_extracted_markdown_can_exceed_source_size_limit(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.ingestion.traverser import _DEFAULT_MAX_FILE_SIZE_BYTES
    from repowise.server.mcp_server._helpers import read_repo_file_text

    calls = tmp_path / "calls.txt"
    _register_spawn_extractor(monkeypatch, calls)
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_PADDING", str(_DEFAULT_MAX_FILE_SIZE_BYTES))
    (tmp_path / "spec.xyz").write_bytes(b"small")
    parsed, _infos, _structure, source_map, *_ = await _ingest(tmp_path)
    assert any(item.file_info.path == "spec.xyz" for item in parsed)
    assert len(source_map["spec.xyz"]) > _DEFAULT_MAX_FILE_SIZE_BYTES
    assert read_repo_file_text(tmp_path, "spec.xyz").encode() == source_map["spec.xyz"]
    await _ingest(tmp_path)
    assert _extractor_calls(calls) == ["spec.xyz"]


@pytest.mark.asyncio
async def test_shared_reader_cache_is_reused_and_corruption_reconverts(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.server.mcp_server._helpers import read_repo_file_text

    calls = tmp_path / "calls.txt"
    _register_spawn_extractor(monkeypatch, calls)
    (tmp_path / "spec.xyz").write_bytes(b"source")
    expected = "# Extracted document\n\nsource\n"
    # MCP can populate the same cache ingestion and historical blob reads use.
    assert read_repo_file_text(tmp_path, "spec.xyz") == expected
    await _ingest(tmp_path)
    (tmp_path / ".repowise" / "parse_cache.pkl").write_bytes(b"corrupt parse cache")
    parsed = ChangeDetector(tmp_path).parse_bytes(b"source", "spec.xyz")
    assert parsed.file_info.language == "markdown"
    assert _extractor_calls(calls) == ["spec.xyz"]
    [entry] = (tmp_path / ".repowise" / "document-cache").iterdir()
    entry.write_bytes(b"untrusted or corrupt cache")
    assert read_repo_file_text(tmp_path, "spec.xyz") == expected
    assert _extractor_calls(calls) == ["spec.xyz", "spec.xyz"]


def test_shared_reader_cache_keeps_paths_distinct_and_tolerates_write_failure(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core import cache_seal
    from repowise.core.ingestion import source_reader

    _register("path-aware", FakeExtractor())
    paths = [tmp_path / name for name in ("first.xyz", "second.xyz")]
    for path in paths:
        path.write_bytes(b"same bytes")
    calls = []

    def convert(requests):
        calls.extend(request[2].name for request in requests)
        return [request[2].name.encode() for request in requests]

    monkeypatch.setattr(source_reader, "extract_documents_bounded", convert)
    assert source_reader.read_source(tmp_path, paths[0].name) == b"first.xyz"
    assert source_reader.read_source(tmp_path, paths[1].name) == b"second.xyz"
    assert source_reader.read_source(tmp_path, paths[0].name) == b"first.xyz"
    assert calls == ["first.xyz", "second.xyz"]

    def unavailable(*args, **kwargs):
        raise OSError("read-only cache")

    monkeypatch.setattr(cache_seal, "dump_sealed_pickle", unavailable)
    paths[0].write_bytes(b"new bytes")
    assert source_reader.read_source(tmp_path, paths[0].name) == b"first.xyz"


def test_extractor_timeout_fails_instead_of_running_unprotected(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.ingestion.document_extractors import extract_documents_bounded
    from repowise.core.pipeline import parse_pool

    calls = tmp_path / "calls.txt"
    _register_spawn_extractor(monkeypatch, calls, hang=True)
    monkeypatch.setenv("REPOWISE_PARSE_TIMEOUT_S", "0.2")

    with pytest.raises(DocumentExtractionError, match="timed out"):
        extract_documents_bounded(
            [("spawn-test-extractor", "1", tmp_path / "spec.xyz", "spec.xyz", b"small")]
        )
    assert _extractor_calls(calls) == ["spec.xyz"]
    assert parse_pool.parse_timeout_s() == 0.2


def test_unavailable_extractor_pool_does_not_run_plugin_in_parent(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.ingestion.document_extractors import extract_documents_bounded
    from repowise.core.pipeline import parse_pool

    calls = tmp_path / "calls.txt"
    _register_spawn_extractor(monkeypatch, calls)

    def unavailable(_workers):
        raise RuntimeError("spawn unavailable")

    monkeypatch.setattr(parse_pool, "_new_pool", unavailable)
    with pytest.raises(DocumentExtractionError, match="could not run"):
        extract_documents_bounded(
            [("spawn-test-extractor", "1", tmp_path / "spec.xyz", "spec.xyz", b"small")]
        )
    assert not calls.exists()


def test_crashed_extractor_worker_fails_with_original_path(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.ingestion.document_extractors import extract_documents_bounded

    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt")
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_CRASH", "1")
    with pytest.raises(DocumentExtractionError, match=r"spawn-test-extractor.*spec\.xyz.*crashed"):
        extract_documents_bounded(
            [("spawn-test-extractor", "1", tmp_path / "spec.xyz", "spec.xyz", b"source")]
        )


@pytest.mark.parametrize("changed_after_discovery", [False, True])
def test_worker_must_resolve_the_fingerprinted_extractor_version(
    tmp_path, extractor_registry, monkeypatch, changed_after_discovery
):
    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt")
    if not changed_after_discovery:
        document_extractors._impls["spawn-test-extractor"].version = "parent-only-version"
    path = tmp_path / "spec.xyz"
    path.write_bytes(b"source")
    info = next(FileTraverser(tmp_path).traverse())
    if changed_after_discovery:
        document_extractors._impls["spawn-test-extractor"].version = "new-version"
        monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_VERSION", "new-version")
    with pytest.raises(DocumentExtractionError, match="version changed"):
        ingestion_phase._split_cached(tmp_path, [(info, path.read_bytes())], None)


def test_parent_only_registration_fails_explicitly(tmp_path, extractor_registry, monkeypatch):
    monkeypatch.delenv("REPOWISE_PLUGINS", raising=False)
    _register("parent-only", FakeExtractor())
    path = tmp_path / "spec.xyz"
    path.write_bytes(b"source")
    info = next(FileTraverser(tmp_path).traverse())
    with pytest.raises(DocumentExtractionError, match=r"parent-only.*spec\.xyz.*not discoverable"):
        ingestion_phase._split_cached(tmp_path, [(info, path.read_bytes())], None)


def test_exclusions_and_size_limit_run_before_extraction(tmp_path, extractor_registry):
    extractor = FakeExtractor()
    _register("test-extractor", extractor)
    path = tmp_path / "spec.xyz"
    path.write_bytes(b"\x00binary source")

    assert list(FileTraverser(tmp_path, extra_exclude_patterns=["*.xyz"]).traverse()) == []
    assert list(FileTraverser(tmp_path, max_file_size_kb=0).traverse()) == []
    assert extractor.calls == 0


def test_plugins_command_shows_effective_extension_owner(extractor_registry):
    _register("test-extractor", FakeExtractor())

    result = CliRunner().invoke(plugins_command, ["--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["document_extractors"] == {".xyz": "test-extractor"}


def test_change_detector_uses_extracted_markdown_and_original_path(
    tmp_path, extractor_registry, monkeypatch
):
    calls_file = tmp_path / "calls.txt"
    _register_spawn_extractor(monkeypatch, calls_file)
    path = tmp_path / "spec.xyz"
    path.write_bytes(b"\x00binary source")

    parsed = ChangeDetector(tmp_path).parse_bytes(path.read_bytes(), "spec.xyz")

    assert parsed is not None
    assert parsed.file_info.path == "spec.xyz"
    assert parsed.file_info.language == "markdown"
    assert parsed.content_hash != hashlib.sha256(b"# Extracted document\n\n\x00binary source\n").hexdigest()
    assert _extractor_calls(calls_file) == ["spec.xyz"]


def test_working_tree_deletion_parses_historical_document_blob(
    tmp_path, extractor_registry, monkeypatch
):
    calls_file = tmp_path / "extractor-calls.txt"
    _register_spawn_extractor(monkeypatch, calls_file)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    document = tmp_path / "spec.xyz"
    document.write_bytes(b"historical document")
    subprocess.run(["git", "-C", str(tmp_path), "add", "spec.xyz"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "add document"], check=True)
    document.unlink()

    changes = ChangeDetector(tmp_path).get_working_tree_changes()

    assert len(changes) == 1
    assert changes[0].status == "deleted"
    assert changes[0].old_parsed is not None
    assert changes[0].old_parsed.file_info.language == "markdown"
    assert _extractor_calls(calls_file) == ["spec.xyz"]


def test_stale_extracted_document_deleted_when_traversal_excludes_it(tmp_path, extractor_registry):
    _register("test-extractor", FakeExtractor())
    (tmp_path / "spec.xyz").write_bytes(b"document")
    (tmp_path / ".repowiseIgnore").write_text("spec.xyz\n", encoding="utf-8")

    changes = ChangeDetector(tmp_path).stale_working_tree_diffs(["spec.xyz"], set())

    assert len(changes) == 1
    assert changes[0].status == "deleted"


@pytest.mark.parametrize(
    ("path", "ignore_file", "patterns", "source"),
    [
        ("ignored.xyz", ".repowiseIgnore", None, b"source"),
        ("nested/ignored.xyz", "nested/.repowiseIgnore", None, b"source"),
        ("ignored.xyz", None, ["*.xyz"], b"source"),
        ("large.xyz", None, None, b"x" * (501 * 1024)),
    ],
    ids=["repowiseignore", "nested-repowiseignore", "exclude-pattern", "oversized"],
)
def test_diff_document_eligibility_checks_supplied_bytes_before_conversion(
    tmp_path, extractor_registry, path, ignore_file, patterns, source
):
    _register("must-not-run", FakeExtractor(failure=AssertionError("not eligible")))
    if ignore_file:
        ignore = tmp_path / ignore_file
        ignore.parent.mkdir(parents=True, exist_ok=True)
        ignore.write_text("ignored.xyz\n", encoding="utf-8")
    detector = ChangeDetector(tmp_path, exclude_patterns=patterns)
    # No source exists on disk: the same boundary must handle historical blobs.
    assert detector.parse_bytes(source, path) is None


def test_working_tree_extraction_failures_are_not_reported_as_no_changes(
    tmp_path, extractor_registry, monkeypatch
):
    from tests.unit.cli.test_update_e2e import _git, _make_git_repo

    repo = _make_git_repo(tmp_path)
    (repo / "spec.xyz").write_bytes(b"old")
    _git(repo, "add", "spec.xyz")
    _git(repo, "commit", "-m", "add document")
    (repo / "spec.xyz").write_bytes(b"changed")
    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt", fail=True)
    with pytest.raises(DocumentExtractionError, match="spawn-test-extractor"):
        ChangeDetector(repo).get_working_tree_changes()


@pytest.mark.parametrize("document_cache_state", ["warm", "missing", "corrupt"])
@pytest.mark.asyncio
async def test_cached_document_diff_needs_no_conversion(
    tmp_path, extractor_registry, monkeypatch, document_cache_state
):
    from repowise.core.ingestion.parse_cache import ParseCache

    calls = tmp_path / "calls.txt"
    _register_spawn_extractor(monkeypatch, calls)
    path = tmp_path / "spec.xyz"
    path.write_bytes(b"source")
    await _ingest(tmp_path)
    [entry] = (tmp_path / ".repowise" / "document-cache").iterdir()
    if document_cache_state == "missing":
        entry.unlink()
    elif document_cache_state == "corrupt":
        entry.write_bytes(b"corrupt")
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_FAIL", "1")
    loads = []
    original_load = ParseCache.load

    def track_load(cache):
        loads.append(cache)
        original_load(cache)

    monkeypatch.setattr(ParseCache, "load", track_load)
    detector = ChangeDetector(tmp_path)
    assert detector.parse_bytes(b"VALUE = 1", "main.py") is not None
    assert loads == []
    parsed = detector.parse_bytes(path.read_bytes(), "spec.xyz")
    assert parsed.file_info.language == "markdown"
    again = detector.parse_bytes(path.read_bytes(), "spec.xyz")
    assert again.content_hash == parsed.content_hash
    assert again is not parsed
    assert len(loads) == 1
    assert _extractor_calls(calls) == ["spec.xyz"]


@pytest.mark.parametrize("workspace", [False, True])
def test_document_index_update_and_deletion_persist_for_repo_and_workspace(
    tmp_path, extractor_registry, monkeypatch, workspace
):
    from repowise.cli.helpers import save_state
    from repowise.cli.main import cli
    from repowise.core.update_lock import release_update_lock
    from tests.unit.cli.test_update_e2e import _db_snapshot, _git, _index_full, _make_git_repo
    from tests.unit.cli.test_workspace_docs_pointer import (
        CURRENT_GIT_HISTORY,
        CURRENT_STORE_FORMAT,
        _make_workspace,
    )

    repo = _make_workspace(tmp_path) if workspace else _make_git_repo(tmp_path)
    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt")
    document = repo / "spec.xyz"
    document.write_bytes(b"initial")
    _git(repo, "add", "spec.xyz")
    _git(repo, "commit", "-m", "add document")
    _index_full(repo)
    assert any("spec.xyz" in node for node in asyncio.run(_db_snapshot(repo))["graph_node_ids"])
    save_state(
        repo,
        {
            "last_sync_commit": _git(repo, "rev-parse", "HEAD"),
            "docs_enabled": False,
            **CURRENT_GIT_HISTORY,
            **CURRENT_STORE_FORMAT,
        },
    )
    monkeypatch.chdir(repo.parent)
    args = ["update", "--workspace"] if workspace else ["update", str(repo), "--no-workspace"]

    def update():
        try:
            result = CliRunner().invoke(cli, [*args, "--working-tree"])
            assert result.exit_code == 0, result.output
            assert "already running" not in result.output
            return result
        finally:
            # CliRunner does not exit the process and run the CLI's atexit cleanup.
            release_update_lock(repo)

    for committed in (True, False):
        document.write_bytes(b"committed edit" if committed else b"working-tree edit")
        if committed:
            _git(repo, "add", "spec.xyz")
            _git(repo, "commit", "-m", "edit document")
        update()
        from repowise.core.ingestion.document_extractors import source_fingerprint
        from repowise.core.ingestion.parse_cache import ParseCache

        info = next(fi for fi in FileTraverser(repo).traverse() if fi.path == "spec.xyz")
        cache = ParseCache(repo / ".repowise")
        cache.load()
        assert cache.get(info, source_fingerprint(info, document.read_bytes())) is not None
        assert any("spec.xyz" in node for node in asyncio.run(_db_snapshot(repo))["graph_node_ids"])
    document.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-m", "delete document")
    result = update()
    assert not any(
        "spec.xyz" in node for node in asyncio.run(_db_snapshot(repo))["graph_node_ids"]
    ), result.output
    if workspace:
        return
    transient = repo / "transient.xyz"
    transient.write_bytes(b"untracked")
    update()
    assert any(
        "transient.xyz" in node for node in asyncio.run(_db_snapshot(repo))["graph_node_ids"]
    )
    transient.unlink()
    result = update()
    assert not any(
        "transient.xyz" in node for node in asyncio.run(_db_snapshot(repo))["graph_node_ids"]
    ), result.output


def test_failed_document_update_does_not_publish_index_state_or_cache(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.cli.helpers import save_state
    from repowise.cli.main import cli
    from tests.unit.cli.test_update_e2e import _db_snapshot, _git, _index_full, _make_git_repo
    from tests.unit.cli.test_workspace_docs_pointer import CURRENT_GIT_HISTORY, CURRENT_STORE_FORMAT

    repo = _make_git_repo(tmp_path)
    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt")
    (repo / "spec.xyz").write_bytes(b"initial")
    _git(repo, "add", "spec.xyz")
    _git(repo, "commit", "-m", "add document")
    _index_full(repo)
    save_state(
        repo,
        {
            "last_sync_commit": _git(repo, "rev-parse", "HEAD"),
            "docs_enabled": False,
            **CURRENT_GIT_HISTORY,
            **CURRENT_STORE_FORMAT,
        },
    )
    before = asyncio.run(_db_snapshot(repo))
    store = repo / ".repowise"
    artefacts = [store / name for name in ("state.json", "knowledge-graph.json", "parse_cache.pkl")]
    persisted = [path.read_bytes() for path in artefacts]
    (repo / "spec.xyz").write_bytes(b"failing edit")
    (repo / "c.py").write_text("def gamma():\n    return 3\n")
    monkeypatch.setenv("REPOWISE_TEST_EXTRACTOR_FAIL", "1")
    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", "--working-tree"])
    assert result.exit_code != 0
    assert "spawn-test-extractor" in str(result.exception) + result.output
    assert asyncio.run(_db_snapshot(repo)) == before
    assert [path.read_bytes() for path in artefacts] == persisted


def test_workspace_change_detection_applies_merged_excludes_before_conversion(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.workspace.update.repo import _changed_file_diffs
    from tests.unit.cli.test_update_e2e import _git, _make_git_repo

    repo = _make_git_repo(tmp_path)
    (repo / "spec.xyz").write_bytes(b"old")
    _git(repo, "add", "spec.xyz")
    _git(repo, "commit", "-m", "add document")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "spec.xyz").write_bytes(b"excluded edit")
    calls = tmp_path / "calls.txt"
    _register_spawn_extractor(monkeypatch, calls, fail=True)
    diffs, paths = _changed_file_diffs(repo, {}, base, True, ["*.xyz"])
    assert paths == ["spec.xyz"]
    assert len(diffs) == 1
    assert diffs[0].old_parsed is diffs[0].new_parsed is None
    assert not calls.exists()


def test_default_document_policy_reads_current_repository_excludes(tmp_path, extractor_registry):
    _register("must-not-run", FakeExtractor())
    settings = tmp_path / ".repowise"
    settings.mkdir()
    (settings / "config.yaml").write_text("exclude_patterns:\n  - '*.xyz'\n")
    assert ChangeDetector(tmp_path).parse_bytes(b"historical", "spec.xyz") is None


@pytest.mark.asyncio
async def test_workspace_extraction_failure_does_not_fall_back_to_reindex(
    tmp_path, extractor_registry, monkeypatch
):
    from repowise.core.workspace.update.repo import _try_incremental_update
    from tests.unit.cli.test_update_e2e import _git, _make_git_repo
    from tests.unit.cli.test_workspace_docs_pointer import CURRENT_GIT_HISTORY

    repo = _make_git_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "spec.xyz").write_bytes(b"failing addition")
    _git(repo, "add", "spec.xyz")
    _git(repo, "commit", "-m", "add document")
    _register_spawn_extractor(monkeypatch, tmp_path / "calls.txt", fail=True)
    with pytest.raises(DocumentExtractionError, match="spawn-test-extractor"):
        await _try_incremental_update(repo, CURRENT_GIT_HISTORY, base, None, False)
