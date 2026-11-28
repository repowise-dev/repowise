"""Unit tests for FastEmbedEmbedder.

fastembed is an optional extra that CI does not install, so every test puts a
stub module in its place: no model download, no ONNX runtime.
"""

from __future__ import annotations

import math
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

_MODULE = "repowise.core.providers.embedding.fastembed"


def _install() -> str:
    from repowise.core.providers.embedding.registry import install_spec

    return install_spec("fastembed")


@pytest.fixture(autouse=True)
def _fresh_module():
    """Each test imports the embedder against its own stub; none outlives it."""
    sys.modules.pop(_MODULE, None)
    yield
    sys.modules.pop(_MODULE, None)


class _FakeTextEmbedding:
    instances: ClassVar[list[_FakeTextEmbedding]] = []

    def __init__(
        self,
        model_name: str,
        cache_dir: str | None = None,
        threads: Any = None,
        local_files_only: bool = False,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.local_files_only = local_files_only
        self.calls: list[tuple[str, list[str]]] = []
        self.instances.append(self)

    def query_embed(self, texts: list[str]):
        self.calls.append(("query", list(texts)))
        return iter([3.0, 4.0, 0.0] for _ in texts)

    def passage_embed(self, texts: list[str]):
        self.calls.append(("passage", list(texts)))
        return iter([0.0, 2.0, 0.0] for _ in texts)


@pytest.fixture
def fake_fastembed(monkeypatch: pytest.MonkeyPatch) -> type[_FakeTextEmbedding]:
    _FakeTextEmbedding.instances = []
    module = types.ModuleType("fastembed")
    module.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return _FakeTextEmbedding


def test_registry_builds_it_and_names_the_default_width(fake_fastembed) -> None:
    from repowise.core.providers.embedding.registry import get_embedder, list_embedders

    embedder = get_embedder("fastembed")

    assert "fastembed" in list_embedders()
    assert type(embedder).__name__ == "FastEmbedEmbedder"
    assert embedder.dimensions == 384
    # The width of a known model needs no model load.
    assert fake_fastembed.instances == []


async def test_queries_and_documents_take_their_own_side(
    fake_fastembed, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    from repowise.core.providers.embedding.registry import get_embedder

    embedder = get_embedder("fastembed")

    query = await embedder.embed(["where is auth"], kind="query")
    docs = await embedder.embed(["page one", "page two"])

    (model,) = fake_fastembed.instances  # loaded once, on first use
    assert model.model_name == "BAAI/bge-small-en-v1.5"
    assert model.cache_dir == str(tmp_path)
    assert model.calls == [("query", ["where is auth"]), ("passage", ["page one", "page two"])]
    assert query == [[0.6, 0.8, 0.0]]
    assert docs == [[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]]
    assert all(math.isclose(sum(x * x for x in v), 1.0) for v in query + docs)
    assert await embedder.embed([]) == []


def test_an_unlisted_model_is_asked_for_its_width(fake_fastembed) -> None:
    from repowise.core.providers.embedding.registry import get_embedder

    embedder = get_embedder("fastembed", model="example/tiny-embed")

    assert embedder.dimensions == 3
    assert [m.model_name for m in fake_fastembed.instances] == ["example/tiny-embed"]


def test_a_missing_extra_names_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", None)  # import fastembed -> ImportError
    from repowise.core.providers.embedding.registry import get_embedder

    with pytest.raises(ImportError) as raised:
        get_embedder("fastembed")
    assert f"pip install {_install()}" in str(raised.value)


def test_the_extra_is_named_where_it_installs_fastembed() -> None:
    # The extra holds fastembed on 3.11 and 3.12 only; newer Pythons install it directly.
    expected = '"repowise[local-embed]"' if sys.version_info < (3, 13) else "fastembed"
    assert _install() == expected


def test_doctor_warns_when_the_repo_embedder_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from repowise.cli.commands.doctor_cmd._checks import providers

    monkeypatch.setitem(sys.modules, "fastembed", None)
    monkeypatch.setattr("repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "fastembed")

    row = providers._embedder_package_check(tmp_path)

    assert row is not None
    assert (row.id, row.level) == ("embedder-package", "warn")
    assert f"pip install {_install()}" in row.detail

    monkeypatch.setattr("repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "mock")
    assert providers._embedder_package_check(tmp_path) is None


def test_it_is_free_like_the_other_local_embedders() -> None:
    from repowise.cli.providers.embedders import is_free_embedder, template_run_embedder
    from repowise.core.cost_estimator.pricing import lookup_embedding_cost

    assert is_free_embedder("fastembed")
    assert lookup_embedding_cost("fastembed") == 0.0
    # A keyless run keeps it even when nobody named it on this run.
    assert template_run_embedder("fastembed", embedder_was_requested=False) == "fastembed"


def test_doctor_prints_the_install_line_with_its_brackets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from rich.console import Console

    from repowise.cli.commands.doctor_cmd import _render
    from repowise.cli.commands.doctor_cmd._types import _check

    out = Console(record=True, width=200)
    monkeypatch.setattr(_render, "console", out)
    monkeypatch.setattr(_render, "_advise_claude_md_stamp", lambda *_a: None)
    ctx = SimpleNamespace(repo_path=tmp_path, coord_drift_pct=None, coord_drift_color="", state={})
    row = _check(
        "Embedder",
        True,
        'Install it with: pip install "repowise[local-embed]"',
        check_id="embedder-package",
        warn=True,
    )

    _render.render_checks(ctx, [row], all_ok=True)

    assert 'pip install "repowise[local-embed]"' in out.export_text()


class _OfflineTextEmbedding(_FakeTextEmbedding):
    """A model that is not cached, on a machine that cannot download it."""

    downloads: ClassVar[list[int]] = []
    EMBEDDINGS_REGISTRY: ClassVar[list[type]] = []

    def __init__(self, model_name: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(model_name, *args, **kwargs)
        raise ValueError(f"Could not load model {model_name} from any source.")

    @classmethod
    def _get_model_description(cls, model_name: str) -> str:
        return model_name

    @classmethod
    def download_model(cls, model: str, cache_dir: str, retries: int = 3) -> None:
        cls.downloads.append(retries)
        raise ValueError(f"Could not load model {model} from any source.")


@pytest.fixture
def offline_fastembed(fake_fastembed, monkeypatch: pytest.MonkeyPatch):
    _OfflineTextEmbedding.downloads = []
    _OfflineTextEmbedding.EMBEDDINGS_REGISTRY = [_OfflineTextEmbedding]
    monkeypatch.setattr(sys.modules["fastembed"], "TextEmbedding", _OfflineTextEmbedding)
    return _OfflineTextEmbedding


@pytest.fixture
def err_out(monkeypatch: pytest.MonkeyPatch):
    from rich.console import Console

    from repowise.cli.providers import embedders

    out = Console(record=True, width=400)
    monkeypatch.setattr("repowise.cli.helpers.err_console", out)
    monkeypatch.setattr(embedders, "_REPORTED_LOAD_FAILURES", set())
    return out


async def test_a_cached_model_opens_without_the_network(fake_fastembed) -> None:
    from repowise.core.providers.embedding.registry import get_embedder

    await get_embedder("fastembed").embed(["page"])

    (model,) = fake_fastembed.instances
    assert model.local_files_only


async def test_a_model_that_cannot_load_is_tried_once_per_process(offline_fastembed) -> None:
    from repowise.core.providers.embedding.registry import get_embedder

    for embedder in (get_embedder("fastembed"), get_embedder("fastembed")):
        for _ in range(2):
            with pytest.raises(RuntimeError, match=r"could not load BAAI/bge-small-en-v1\.5"):
                await embedder.embed(["page"])

    # One cache look and one download, with one try: fastembed's own three
    # tries sleep 39 s before an offline run could fall back.
    assert offline_fastembed.downloads == [1]
    assert [m.local_files_only for m in offline_fastembed.instances] == [True]


def test_a_build_that_cannot_load_the_model_goes_keyless_in_one_line(
    offline_fastembed, err_out
) -> None:
    from repowise.cli.providers.embedders import build_embedder
    from repowise.core.providers.embedding.base import KeylessEmbedder

    first = build_embedder("fastembed")
    second = build_embedder("fastembed")  # init builds one for the store, one for generation

    assert isinstance(first, KeylessEmbedder) and isinstance(second, KeylessEmbedder)
    assert offline_fastembed.downloads == [1]
    lines = err_out.export_text().strip().splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith("Semantic search is off for this run: fastembed could not load")
    assert "repowise reindex --embedder fastembed" in lines[0]


def test_a_build_loads_the_model_it_returns(fake_fastembed, err_out) -> None:
    from repowise.cli.providers.embedders import build_embedder

    embedder = build_embedder("fastembed")

    assert type(embedder).__name__ == "FastEmbedEmbedder"
    assert len(fake_fastembed.instances) == 1
    assert err_out.export_text() == ""


def test_the_fallback_warning_keeps_the_install_line_brackets(
    monkeypatch: pytest.MonkeyPatch, err_out
) -> None:
    from repowise.cli.providers.embedders import build_embedder

    monkeypatch.setitem(sys.modules, "fastembed", None)

    build_embedder("fastembed")

    assert f"pip install {_install()}" in err_out.export_text()


@pytest.mark.parametrize("cause", ["missing package", "offline"])
def test_reindex_with_an_unavailable_embedder_exits_1_without_aborting(
    cause: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, err_out, request
) -> None:
    import click
    from rich.console import Console

    from repowise.cli.commands import reindex_cmd

    out = Console(record=True, width=400)
    monkeypatch.setattr(reindex_cmd, "console", out)
    if cause == "missing package":
        monkeypatch.setitem(sys.modules, "fastembed", None)  # Python 3.13+: the extra is empty
    else:
        request.getfixturevalue("offline_fastembed")

    with pytest.raises(click.exceptions.Exit) as raised:
        reindex_cmd._embedder_for(tmp_path, "fastembed")

    assert raised.value.exit_code == 1
    said = err_out.export_text()
    assert (f"pip install {_install()}" if cause == "missing package" else "could not load") in said
    # It stops: no claim that this run carries on with keyless vectors.
    assert "Falling back" not in said and "off for this run" not in said
    assert "Nothing was re-embedded" in out.export_text()


def test_a_run_that_fell_back_does_not_report_semantic_search() -> None:
    from types import SimpleNamespace

    from repowise.cli.providers.vector_store import semantic_search_status
    from repowise.core.providers.embedding.base import KeylessEmbedder

    # init names fastembed, but the model never loaded: the store embedded keyless.
    fell_back = SimpleNamespace(_embedder=KeylessEmbedder())
    real = SimpleNamespace(_embedder=object())

    assert semantic_search_status("fastembed", 0, fell_back) == "unavailable"
    assert semantic_search_status("fastembed", 0, real) == "available"
    assert semantic_search_status("fastembed", 0) == "available"


def test_the_mcp_server_names_the_same_install_line() -> None:
    from repowise.server.mcp_server._server import _EMBEDDER_REMEDIATION

    assert f"pip install {_install()}" in _EMBEDDER_REMEDIATION["fastembed"]


class _FirstRunTextEmbedding(_FakeTextEmbedding):
    """Online, nothing cached yet: the cache look fails until a download lands."""

    downloaded: ClassVar[list[int]] = []
    EMBEDDINGS_REGISTRY: ClassVar[list[type]] = []

    def __init__(self, model_name: str, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("local_files_only") and not self.downloaded:
            raise ValueError("Could not find model in cache_dir")
        super().__init__(model_name, *args, **kwargs)

    @classmethod
    def _get_model_description(cls, model_name: str) -> str:
        return model_name

    @classmethod
    def download_model(cls, model: str, cache_dir: str, retries: int = 3) -> None:
        cls.downloaded.append(retries)


async def test_a_first_run_downloads_once_and_shares_the_model(
    fake_fastembed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FirstRunTextEmbedding.downloaded = []
    _FirstRunTextEmbedding.EMBEDDINGS_REGISTRY = [_FirstRunTextEmbedding]
    monkeypatch.setattr(sys.modules["fastembed"], "TextEmbedding", _FirstRunTextEmbedding)
    from repowise.core.providers.embedding.registry import get_embedder

    for embedder in (get_embedder("fastembed"), get_embedder("fastembed")):
        await embedder.embed(["page"])

    assert _FirstRunTextEmbedding.downloaded == [1]
    # Opened from the cache it just filled, once for both embedders.
    assert [m.local_files_only for m in fake_fastembed.instances] == [True]


async def test_without_fastembeds_model_classes_it_downloads_the_usual_way(
    fake_fastembed, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoRegistry(_FakeTextEmbedding):
        def __init__(self, model_name: str, *args: Any, **kwargs: Any) -> None:
            if kwargs.get("local_files_only"):
                raise ValueError("Could not find model in cache_dir")
            super().__init__(model_name, *args, **kwargs)

    monkeypatch.setattr(sys.modules["fastembed"], "TextEmbedding", _NoRegistry)
    from repowise.core.providers.embedding.registry import get_embedder

    await get_embedder("fastembed").embed(["page"])

    assert [m.local_files_only for m in fake_fastembed.instances] == [False]
