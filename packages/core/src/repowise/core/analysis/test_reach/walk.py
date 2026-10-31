# packages/core/src/repowise/core/analysis/test_reach/walk.py
"""Walks que responden “¿qué tests alcanzan este fichero?”.

``call_walk`` e ``import_walk`` conservan exactamente su firma y contrato
originales (``Walk[dict[str, set[str]]]``); ``registration_walk`` es una
función nueva, puramente aditiva, que se apoya en el resultado de los
walks anteriores vía ``prior``.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from .maps import (
    DEFAULT_CALL_DEPTH,
    Need,
    Walk,
    WalkMaps,
)


# ---------------------------------------------------------------------------
# Walk de llamadas — firma original, sin cambios (P1)
# ---------------------------------------------------------------------------
def call_walk(
    seeds: list[str],
    test_files: Collection[str],
    max_depth: int = DEFAULT_CALL_DEPTH,
) -> Walk[dict[str, set[str]]]:
    """Tests que llegan a *seeds* siguiendo aristas de ejecución."""
    reached: dict[str, set[str]] = {seed: set() for seed in seeds}
    frontier = list(seeds)
    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        pairs: list[tuple[str, str]] = yield Need("callers", tuple(level))
        for caller, callee in pairs:
            if callee in reached:
                reached[callee].add(caller)
                if caller in test_files and caller not in frontier:
                    frontier.append(caller)
    return reached


# ---------------------------------------------------------------------------
# Walk de importaciones — firma original, sin cambios (P1)
# ---------------------------------------------------------------------------
def import_walk(
    seeds: list[str],
    test_files: Collection[str],
    max_depth: int = DEFAULT_CALL_DEPTH,
) -> Walk[dict[str, set[str]]]:
    """Tests que llegan a *seeds* siguiendo aristas de importación."""
    reached: dict[str, set[str]] = {seed: set() for seed in seeds}
    frontier = list(seeds)
    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        pairs: list[tuple[str, str]] = yield Need("importers", tuple(level))
        for dependent, dependency in pairs:
            if dependency in reached:
                reached[dependency].add(dependent)
                if dependent in test_files and dependent not in frontier:
                    frontier.append(dependent)
    return reached


# ---------------------------------------------------------------------------
# Walk de registros — función NUEVA, aditiva, no altera contratos existentes
# ---------------------------------------------------------------------------
def registration_walk(
    seeds: list[str],
    test_files: Collection[str],
    max_depth: int = DEFAULT_CALL_DEPTH,
    *,
    prior: Mapping[str, Collection[str]] | None = None,
) -> Walk[dict[str, set[str]]]:
    """Tests que llegan a *seeds* y, desde ahí, a los módulos que registran.

    *prior* transporta los tests ya asignados a cada fichero por
    ``call_walk``/``import_walk`` (``{fichero: {tests}}``). Sin él, el
    walk funciona igual pero los ficheros registrados heredan el conjunto
    de tests de su registrador (posiblemente vacío).

    Los módulos registrados se marcan **en el mismo nivel** que el
    registrador: misma profundidad, mismos tests.
    """
    reached: dict[str, set[str]] = {seed: set() for seed in seeds}
    if prior:
        for node, tests in prior.items():
            reached.setdefault(node, set()).update(tests)

    frontier = list(seeds)
    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        pairs: list[tuple[str, str]] = yield Need("registrations", tuple(level))
        for registrar, registered in pairs:
            if registrar not in reached:
                continue
            reached.setdefault(registered, set())
            reached[registered].update(reached[registrar])
            if registered not in frontier:
                frontier.append(registered)
    return reached


# ---------------------------------------------------------------------------
# Orquestador de reachability
# ---------------------------------------------------------------------------
def reach_states(
    graph_maps: WalkMaps,
    changed_files: list[str],
) -> dict[str, set[str]]:
    """Devuelve ``{fichero_alcanzado: {tests}}`` combinando los tres walks."""
    seeds = changed_files
    test_files = graph_maps.test_files

    # 1) Walk de llamadas.
    call_reach = _drive(call_walk(seeds, test_files), graph_maps)

    # 2) Walk de importaciones.
    import_reach = _drive(import_walk(seeds, test_files), graph_maps)

    # 3) Walk de registros: sembramos con lo alcanzado y sus tests.
    prior: dict[str, set[str]] = {}
    for reach in (call_reach, import_reach):
        for node, tests in reach.items():
            prior.setdefault(node, set()).update(tests)
    reg_seeds = list(prior)

    reg_reach = _drive(
        registration_walk(reg_seeds, test_files, prior=prior),
        graph_maps,
    )

    # Unión de resultados.
    all_reached: dict[str, set[str]] = {}
    for reach in (call_reach, import_reach, reg_reach):
        for node, tests in reach.items():
            all_reached.setdefault(node, set()).update(tests)
    return all_reached


def _drive(walk: Walk[dict[str, set[str]]], maps: WalkMaps) -> dict[str, set[str]]:
    """Ejecuta un walk hasta su resultado final."""
    try:
        need: Any = next(walk)
        while True:
            need = walk.send(maps.answer(need))
    except StopIteration as done:
        return done.value
