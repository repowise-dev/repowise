"""Level 1 builds a spotlight from the symbol selection ranked (issue #6025)."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from repowise.core.generation.page_generator.levels import build_level01_coros
from repowise.core.generation.selection import Selection


@dataclass
class _Symbol:
    name: str
    id: str
    signature: str


@dataclass
class _Parsed:
    file_info: Any
    symbols: list[_Symbol] = field(default_factory=list)


class _Gen:
    def __init__(self) -> None:
        self.spotlit: list[_Symbol] = []

    def generate_symbol_spotlight(self, sym: _Symbol, pf: _Parsed, graph: Any) -> str:
        self.spotlit.append(sym)
        return "coro"


def _run(selection: Selection) -> tuple[Any, _Gen]:
    method = _Symbol("get_current_user", "app/auth.py::Deps::get_current_user", "(self, token)")
    function = _Symbol("get_current_user", "app/auth.py::get_current_user", "(token)")
    pf = _Parsed(
        file_info=SimpleNamespace(path="app/auth.py", is_api_contract=False),
        symbols=[method, function],
    )
    gen = _Gen()
    run = SimpleNamespace(
        gen=gen,
        parsed_files=[pf],
        sel_api_paths=set(),
        source_map={},
        selection=selection,
        graph=None,
        _emit=lambda page_id: True,
    )
    return run, gen


def test_the_ranked_function_is_spotlit_not_the_method_declared_before_it():
    selection = Selection(
        symbol_spotlights=[("app/auth.py", "get_current_user")],
        spotlight_symbol_ids={("app/auth.py", "get_current_user"): "app/auth.py::get_current_user"},
    )
    run, gen = _run(selection)

    coros = build_level01_coros(run)

    assert [page_id for page_id, _ in coros] == ["symbol_spotlight:app/auth.py::get_current_user"]
    assert [s.signature for s in gen.spotlit] == ["(token)"]


def test_a_selection_without_ids_still_takes_the_first_same_named_symbol():
    run, gen = _run(Selection(symbol_spotlights=[("app/auth.py", "get_current_user")]))

    build_level01_coros(run)

    assert [s.signature for s in gen.spotlit] == ["(self, token)"]
