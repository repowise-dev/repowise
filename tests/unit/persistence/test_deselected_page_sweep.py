"""`repowise update` retires template pages the selection no longer picks."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from repowise.core.generation.models import GenerationConfig, compute_page_id
from repowise.core.generation.selection.selector import file_page_eligible
from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import sweep_deselected_pages
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


@dataclass
class _FileInfo:
    path: str
    language: str = "python"
    size_bytes: int = 5_000
    is_test: bool = False
    is_api_contract: bool = False
    is_entry_point: bool = False


@dataclass
class _Symbol:
    name: str
    kind: str = "function"
    visibility: str = "public"


@dataclass
class _Parsed:
    file_info: _FileInfo
    symbols: list[_Symbol] = field(default_factory=list)


class _Builder:
    def __init__(self, pagerank: dict[str, float]) -> None:
        self._pagerank = pagerank

    def pagerank(self) -> dict[str, float]:
        return self._pagerank


# Twenty production files with one distinct public function each, ranked by
# PageRank: the default 10% keeps two spotlights, the margin protects three.
_RANKED = [f"src/f{i:02d}.py" for i in range(20)]


def _parsed() -> list[_Parsed]:
    files = [_Parsed(_FileInfo(p), [_Symbol(f"fn{i}")]) for i, p in enumerate(_RANKED)]
    files.append(_Parsed(_FileInfo("tests/test_a.py", is_test=True), [_Symbol("test_a")]))
    files.append(_Parsed(_FileInfo("src/shim.py")))
    return files


def _builder() -> _Builder:
    return _Builder({p: 1.0 - i / 100 for i, p in enumerate(_RANKED)})


@pytest.fixture
async def repo_id(async_session) -> str:
    return (await insert_repo(async_session)).id


async def _add(async_session, repo_id: str, page_type: str, target: str, **kw) -> str:
    from repowise.core.persistence.crud import upsert_page

    page_id = compute_page_id(page_type, target)
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo_id,
            page_id=page_id,
            page_type=page_type,
            target_path=target,
            provider_name=kw.get("provider_name", "template"),
        ),
    )
    await async_session.flush()
    return page_id


async def _ids(async_session) -> set[str]:
    return {r.id for r in (await async_session.execute(Page.__table__.select())).fetchall()}


async def _sweep(async_session, repo_id: str) -> list[str]:
    return await sweep_deselected_pages(
        async_session, repo_id, _parsed(), _builder(), GenerationConfig()
    )


@pytest.mark.asyncio
class TestDeselectedFilePages:
    async def test_test_file_and_shim_pages_are_retired(self, async_session, repo_id) -> None:
        test_page = await _add(async_session, repo_id, "file_page", "tests/test_a.py")
        shim = await _add(async_session, repo_id, "file_page", "src/shim.py")
        kept = await _add(async_session, repo_id, "file_page", "src/f10.py")

        swept = await _sweep(async_session, repo_id)

        assert sorted(swept) == sorted([test_page, shim])
        assert await _ids(async_session) == {kept}

    async def test_unparsed_file_keeps_its_page(self, async_session, repo_id) -> None:
        page = await _add(async_session, repo_id, "file_page", "tests/excluded_test.py")
        assert await _sweep(async_session, repo_id) == []
        assert await _ids(async_session) == {page}

    async def test_this_runs_hotspot_flag_keeps_a_symbol_less_page(
        self, async_session, repo_id
    ) -> None:
        shim = await _add(async_session, repo_id, "file_page", "src/shim.py")
        swept = await sweep_deselected_pages(
            async_session,
            repo_id,
            _parsed(),
            _builder(),
            GenerationConfig(),
            {"src/shim.py": {"is_hotspot": True}},
        )
        assert swept == []
        assert await _ids(async_session) == {shim}

    async def test_model_written_page_is_kept(self, async_session, repo_id) -> None:
        page = await _add(
            async_session, repo_id, "file_page", "tests/test_a.py", provider_name="openai"
        )
        assert await _sweep(async_session, repo_id) == []
        assert await _ids(async_session) == {page}


@pytest.mark.asyncio
class TestDeselectedSpotlights:
    async def test_far_outside_the_cut_is_retired(self, async_session, repo_id) -> None:
        top = await _add(async_session, repo_id, "symbol_spotlight", "src/f00.py::fn0")
        # Rank 3: past the 10% cut, inside the margin, so it is kept.
        boundary = await _add(async_session, repo_id, "symbol_spotlight", "src/f02.py::fn2")
        out = await _add(async_session, repo_id, "symbol_spotlight", "src/f05.py::fn5")
        test_sym = await _add(
            async_session, repo_id, "symbol_spotlight", "tests/test_a.py::test_a"
        )

        swept = await _sweep(async_session, repo_id)

        assert sorted(swept) == sorted([out, test_sym])
        assert await _ids(async_session) == {top, boundary}

    async def test_near_clone_loser_is_retired(self, async_session, repo_id) -> None:
        # Three same-shaped files in one directory: generation keeps the top one.
        clones = [_Parsed(_FileInfo(f"gen/c{i}.py"), [_Symbol("run")]) for i in range(3)]
        builder = _Builder({"gen/c0.py": 1.0, "gen/c1.py": 0.9, "gen/c2.py": 0.8})
        kept = await _add(async_session, repo_id, "symbol_spotlight", "gen/c0.py::run")
        loser = await _add(async_session, repo_id, "symbol_spotlight", "gen/c1.py::run")

        swept = await sweep_deselected_pages(
            async_session, repo_id, clones, builder, GenerationConfig(top_symbol_percentile=1.0)
        )

        assert swept == [loser]
        assert await _ids(async_session) == {kept}

    async def test_unparsed_file_keeps_its_spotlight(self, async_session, repo_id) -> None:
        page = await _add(async_session, repo_id, "symbol_spotlight", "gone.py::f")
        assert await _sweep(async_session, repo_id) == []
        assert await _ids(async_session) == {page}

    async def test_missing_graph_is_a_no_op(self, async_session, repo_id) -> None:
        page = await _add(async_session, repo_id, "symbol_spotlight", "src/f05.py::fn5")
        swept = await sweep_deselected_pages(
            async_session, repo_id, _parsed(), None, GenerationConfig()
        )
        assert swept == []
        assert await _ids(async_session) == {page}


def test_file_page_eligible_matches_the_selection_floors() -> None:
    code = _Parsed(_FileInfo("src/a.py"), [_Symbol("f")])
    shim = _Parsed(_FileInfo("src/b.py"))
    test = _Parsed(_FileInfo("tests/test_c.py", is_test=True), [_Symbol("t")])
    init = _Parsed(_FileInfo("src/pkg/__init__.py"), [_Symbol("x"), _Symbol("y")])

    assert file_page_eligible(code, is_hotspot=False)
    assert not file_page_eligible(shim, is_hotspot=False)
    # A hotspot earns a page even with no symbols, as selection scores it.
    assert file_page_eligible(shim, is_hotspot=True)
    assert not file_page_eligible(test, is_hotspot=False)
    assert not file_page_eligible(init, is_hotspot=False)
