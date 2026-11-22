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


@pytest.fixture(autouse=True)
def _fresh_module():
    """Each test imports the embedder against its own stub; none outlives it."""
    sys.modules.pop(_MODULE, None)
    yield
    sys.modules.pop(_MODULE, None)


class _FakeTextEmbedding:
    instances: ClassVar[list[_FakeTextEmbedding]] = []

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: Any = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
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

    with pytest.raises(ImportError, match=r'pip install "repowise\[local-embed\]"'):
        get_embedder("fastembed")


def test_doctor_warns_when_the_repo_embedder_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from repowise.cli.commands.doctor_cmd._checks import providers

    monkeypatch.setitem(sys.modules, "fastembed", None)
    monkeypatch.setattr("repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "fastembed")

    row = providers._embedder_package_check(tmp_path)

    assert row is not None
    assert (row.id, row.level) == ("embedder-package", "warn")
    assert 'pip install "repowise[local-embed]"' in row.detail

    monkeypatch.setattr("repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "mock")
    assert providers._embedder_package_check(tmp_path) is None


def test_it_is_free_like_the_other_local_embedders() -> None:
    from repowise.cli.providers.embedders import is_free_embedder, template_run_embedder
    from repowise.core.cost_estimator.pricing import lookup_embedding_cost

    assert is_free_embedder("fastembed")
    assert lookup_embedding_cost("fastembed") == 0.0
    # A keyless run keeps it even when nobody named it on this run.
    assert template_run_embedder("fastembed", embedder_was_requested=False) == "fastembed"
