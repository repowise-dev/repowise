# packages/core/src/repowise/core/analysis/test_reach/walk.py
"""Walks que responden “¿qué tests alcanzan este fichero?”.

Incluye el walk de registros, que propaga el alcance a los módulos
registrados (FastAPI, CLI, MCP, dialectos) en el mismo nivel.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import Generator

from .maps import (
    DEFAULT_CALL_DEPTH,
    Need,
    Walk,
    WalkMaps,
)


# ---------------------------------------------------------------------------
# Walk de llamadas (existente)
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
# Walk de importaciones (existente)
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
# Walk de registros (nuevo)
# ---------------------------------------------------------------------------
def registration_walk(
    seeds: list[str],
    test_files: Collection[str],
    max_depth: int = DEFAULT_CALL_DEPTH,
) -> Walk[dict[str, set[str]]]:
    """Tests que llegan a *seeds* y, desde ahí, a los módulos que registran.

    Para cada módulo alcanzado se pide ``Need("registrations", (module,))``
    y se propaga el alcance a los módulos registrados **en el mismo nivel**
    (misma profundidad).
    """
    reached: dict[str, set[str]] = {seed: set() for seed in seeds}
    frontier = list(seeds)

    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        # Pedimos las registraciones de los módulos del nivel actual.
        pairs: list[tuple[str, str]] = yield Need("registrations", tuple(level))

        for registrar, registered in pairs:
            if registrar not in reached:
                continue
            # El módulo registrado se considera alcanzado en el mismo nivel.
            reached.setdefault(registered, set())
            # Los tests que alcanzan el registrador también alcanzan lo registrado.
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
    call_gen = call_walk(seeds, test_files)
    call_reach = _drive(call_gen, graph_maps)

    # 2) Walk de importaciones.
    import_gen = import_walk(seeds, test_files)
    import_reach = _drive(import_gen, graph_maps)

    # 3) Walk de registros (semillas = todo lo alcanzado por los dos anteriores).
    reg_seeds = list(set(call_reach) | set(import_reach))
    reg_gen = registration_walk(reg_seeds, test_files)
    reg_reach = _drive(reg_gen, graph_maps)

    # Unión de resultados.
    all_reached: dict[str, set[str]] = {}
    for reach in (call_reach, import_reach, reg_reach):
        for node, tests in reach.items():
            all_reached.setdefault(node, set()).update(tests)
    return all_reached


def _drive(walk: Walk[dict[str, set[str]]], maps: WalkMaps) -> dict[str, set[str]]:
    """Ejecuta un walk hasta su resultado final."""
    try:
        need = next(walk)
        while True:
            need = walk.send(maps.answer(need))
    except StopIteration as done:
        return done.value
