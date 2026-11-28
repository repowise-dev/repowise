"""A resolver that raises on one file costs that file's edges, never the whole build."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder, type_ref_resolution
from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.heritage_resolver import HeritageResolver
from repowise.core.ingestion.models import CallSite
from repowise.core.ingestion.resolution_containment import STRICT_ENV

_FILES = {
    "util.py": "def helper():\n    pass\n\n\nclass Base:\n    pass\n",
    "good.py": "from util import helper, Base\n\n\nclass G(Base):\n    pass\n\n\n"
    "def go():\n    helper()\n",
    "bad.py": "from util import helper, Base\n\n\nclass B(Base):\n    pass\n\n\n"
    "def bad():\n    helper()\n",
}


class _Progress:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def on_phase_start(self, phase: str, total: int | None) -> None: ...

    def on_item_done(self, phase: str) -> None: ...

    def on_phase_done(self, phase: str) -> None: ...

    def on_message(self, level: str, text: str) -> None:
        if level == "warning":
            self.warnings.append(text)


def _builder(repo: Path) -> GraphBuilder:
    for name, body in _FILES.items():
        (repo / name).write_text(body)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder


def _edges(graph, source: str, edge_type: str) -> set[str]:
    return {
        target
        for _, target, data in graph.out_edges(source, data=True)
        if data.get("edge_type") == edge_type
    }


def _raise_for_bad(monkeypatch: pytest.MonkeyPatch, owner: type, name: str) -> None:
    original = getattr(owner, name)

    def resolve(self, path, *args, **kwargs):
        if path == "bad.py":
            raise KeyError("missing symbol")
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(owner, name, resolve)


@pytest.fixture
def lenient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(STRICT_ENV, raising=False)


@pytest.mark.usefixtures("lenient")
def test_call_failure_skips_only_that_file(tmp_path: Path, monkeypatch) -> None:
    _raise_for_bad(monkeypatch, CallResolver, "resolve_file")
    builder = _builder(tmp_path)
    progress = _Progress()
    graph = builder.build(progress)

    assert _edges(graph, "good.py::go", "calls") == {"util.py::helper"}
    assert _edges(graph, "bad.py::bad", "calls") == set()
    # The other passes still ran for the failing file.
    assert "util.py" in _edges(graph, "bad.py", "imports")
    assert _edges(graph, "bad.py::B", "extends") == {"util.py::Base"}
    assert ("calls", "bad.py", "KeyError") in builder.resolve_failures
    assert builder.external_call_sites is not None
    assert "bad.py" not in builder.external_call_sites
    # Unrecorded, so the next update cannot seed from the gap.
    assert "bad.py" not in builder.import_resolutions
    assert "good.py" in builder.import_resolutions
    [warning] = progress.warnings
    assert "of 1 file(s)" in warning and "bad.py (calls: KeyError)" in warning


@pytest.mark.usefixtures("lenient")
def test_call_failure_marks_package_calls_incomplete(tmp_path: Path, monkeypatch) -> None:
    original = CallResolver.resolve_file

    def append_then_raise(self, path, calls, external=None):
        if path == "bad.py":
            external.append(object())
            raise KeyError("missing symbol")
        return original(self, path, calls, external)

    monkeypatch.setattr(CallResolver, "resolve_file", append_then_raise)
    builder = _builder(tmp_path)
    builder.build()
    assert "bad.py" not in builder.external_call_sites
    assert builder.external_calls_complete is False


@pytest.mark.usefixtures("lenient")
def test_reference_failure_writes_neither_batch(tmp_path: Path, monkeypatch) -> None:
    original = CallResolver.resolve_file

    def raise_on_qualified(self, path, sites, *args):
        if path == "bad.py" and any(site.receiver_name for site in sites):
            raise KeyError("missing symbol")
        return original(self, path, sites, *args)

    monkeypatch.setattr(CallResolver, "resolve_file", raise_on_qualified)
    builder = _builder(tmp_path)
    for path, caller in (("good.py", "good.py::go"), ("bad.py", "bad.py::bad")):
        builder._parsed_files[path].references = [
            CallSite("Base", None, caller, 9, None, edge_type="references"),
            CallSite("helper", "util", caller, 9, None, edge_type="references"),
        ]
    graph = builder.build()

    assert "util.py::Base" in _edges(graph, "good.py::go", "references")
    assert _edges(graph, "bad.py::bad", "references") == set()
    assert builder.resolve_failures == [("references", "bad.py", "KeyError")]


@pytest.mark.usefixtures("lenient")
def test_heritage_failure_skips_only_that_file(tmp_path: Path, monkeypatch) -> None:
    _raise_for_bad(monkeypatch, HeritageResolver, "resolve_file")
    builder = _builder(tmp_path)
    graph = builder.build()

    assert _edges(graph, "good.py::G", "extends") == {"util.py::Base"}
    assert _edges(graph, "bad.py::B", "extends") == set()
    assert _edges(graph, "bad.py::bad", "calls") == {"util.py::helper"}
    assert builder.resolve_failures == [("heritage", "bad.py", "KeyError")]


@pytest.mark.usefixtures("lenient")
def test_import_failure_leaves_no_half_resolved_imports(tmp_path: Path, monkeypatch) -> None:
    _raise_for_bad(monkeypatch, GraphBuilder, "_resolve_file_imports")
    builder = _builder(tmp_path)
    graph = builder.build()

    assert "util.py" in _edges(graph, "good.py", "imports")
    assert _edges(graph, "bad.py", "imports") == set()
    # Missing from the resolution record, so the next patched update rebuilds in full.
    assert "bad.py" not in builder.import_resolutions
    assert "good.py" in builder.import_resolutions
    bad_imports = builder._parsed_files["bad.py"].imports
    assert all(imp.resolved_file is None for imp in bad_imports)
    assert all(b.source_file is None for imp in bad_imports for b in imp.bindings)
    assert builder.resolve_failures == [("imports", "bad.py", "KeyError")]


@pytest.mark.usefixtures("lenient")
def test_type_ref_failure_rolls_back_the_files_new_edges(tmp_path: Path, monkeypatch) -> None:
    def half_then_raise(parsed, ctx, graph, defined_names):
        src = parsed.file_info.path
        other = "good.py" if src == "bad.py" else "bad.py"
        graph.add_edge(src, other, edge_type="type_use")
        if src == "bad.py":
            raise KeyError("missing type")
        return 1

    monkeypatch.setitem(type_ref_resolution._STRATEGIES, "python", half_then_raise)
    builder = _builder(tmp_path)
    graph = builder.build()

    assert graph.has_edge("good.py", "bad.py")
    assert not graph.has_edge("bad.py", "good.py")
    assert "util.py" in _edges(graph, "bad.py", "imports")
    assert ("type_refs", "bad.py", "KeyError") in builder.resolve_failures


@pytest.mark.parametrize("value", ["1", "TRUE"])
def test_strict_mode_reraises(tmp_path: Path, monkeypatch, value: str) -> None:
    monkeypatch.setenv(STRICT_ENV, value)
    _raise_for_bad(monkeypatch, CallResolver, "resolve_file")
    with pytest.raises(KeyError):
        _builder(tmp_path).build()


@pytest.mark.usefixtures("lenient")
def test_a_clean_build_reports_nothing(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    progress = _Progress()
    builder.build(progress)
    assert builder.resolve_failures == []
    assert progress.warnings == []


async def test_update_records_skipped_files_as_degraded(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from repowise.core.pipeline.incremental import persist_index

    async def no_write(ctx) -> None:
        return None

    monkeypatch.setattr(persist_index, "_write", no_write)
    builder = SimpleNamespace(resolve_failures=[("calls", "bad.py", "KeyError")])
    degraded: list[str] = []
    await persist_index.persist_incremental_index(
        tmp_path, builder, {}, None, None, [], degraded=degraded
    )
    [line] = degraded
    assert "1 file(s)" in line and "bad.py (calls: KeyError)" in line
