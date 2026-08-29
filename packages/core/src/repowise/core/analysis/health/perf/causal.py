"""Causal grouping and the versioned identity kernel.

:func:`causal_key` decides what makes two observations the same cause, and its
output is what gets hashed into the ``opportunity_id``. Grouping and identity
are deliberately the same computation: an id that could disagree with its own
group would be a join key nobody could trust.

That id is persisted into every raw finding and every performance plan, and
readers join on exact string equality, so the kernel's inputs are a contract.
Adding a fact must not change them; changing them means bumping
:data:`PERFORMANCE_MODEL_VERSION`.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Literal

from repowise.core.test_paths import is_test_related_path

from .facts import ObservationFacts, detail_map, is_performance, observation_facts

PERFORMANCE_MODEL_VERSION = 1
"""Version of the identity, grouping, actionability, and ranking semantics.

Version 1 predates this constant, so it is deliberately not an input to
:func:`stable_id`: embedding it would rewrite every persisted ``opportunity_id``
and orphan every stored plan link for no semantic change. A later version may
embed it, and must bump ``HEALTH_ANALYZER_VERSION`` with it.
"""

ExecutionContext = Literal["production", "tooling", "test"]

CausalKey = tuple[Any, ...]

_TOOLING_PARTS = frozenset(
    {".github", "benchmarks", "build", "devtools", "scripts", "tooling", "tools"}
)


def execution_context(file_path: str) -> ExecutionContext:
    """Where this code runs. An identity input, so it is classified once."""
    if is_test_related_path(file_path):
        return "test"
    parts = {part.lower() for part in file_path.replace("\\", "/").split("/")}
    if parts & _TOOLING_PARTS or "/cli/" in f"/{file_path.lower().replace(chr(92), '/')}/":
        return "tooling"
    return "production"


def cost_shape(marker: str) -> str:
    """Compatibility family for observations that may share one intervention."""
    if marker in {"io_in_loop", "nested_loop_with_io"}:
        return "repeated_io"
    return marker


def causal_key(facts: ObservationFacts) -> CausalKey:
    """The v1 identity kernel.

    The two branches are deliberately asymmetric. A cross-function cause is
    named by its terminal sink, so a new caller adds evidence without renaming
    the cause; a same-function one has no shared sink and is named by its own
    location.

    Everything outside these tuples is display or derived data and stays out:
    prose, line ends, storage ids, rank factors, confidence, reachability, and
    provenance.
    """
    context = execution_context(facts.file_path)
    if facts.cross_function and facts.path:
        return (
            "cross-function",
            context,
            cost_shape(facts.marker),
            facts.boundary_kind,
            facts.path[-1],
        )
    return (
        "local",
        context,
        facts.marker,
        facts.boundary_kind,
        facts.file_path,
        facts.function_name,
        facts.line_start,
    )


def stable_id(key: CausalKey) -> str:
    payload = json.dumps(key, separators=(",", ":"), ensure_ascii=True)
    return "perf_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def key_context(key: CausalKey) -> ExecutionContext:
    """Both kernel branches carry the execution context in the same slot."""
    return key[1]


def key_boundary(key: CausalKey) -> str | None:
    """Both kernel branches carry the boundary kind in the same slot."""
    return key[3]


def key_is_cross_function(key: CausalKey) -> bool:
    return key[0] == "cross-function"


def opportunity_id_for_finding(row: Any) -> str:
    return stable_id(causal_key(observation_facts(row)))


def link_performance_findings(findings: list[Any]) -> None:
    """Attach the causal id to analyzer findings before they are persisted.

    Runs before opportunities are built, so a finding carries the id its
    opportunity will publish. Mutates in place: the caller persists these same
    objects.
    """
    for finding in findings:
        if not is_performance(finding):
            continue
        details = detail_map(finding)
        details["opportunity_id"] = opportunity_id_for_finding(finding)


def group_observations(rows: list[Any]) -> dict[CausalKey, list[ObservationFacts]]:
    """Fold raw rows into causal groups, each in deterministic evidence order."""
    groups: dict[CausalKey, list[ObservationFacts]] = defaultdict(list)
    for row in rows:
        if is_performance(row):
            facts = observation_facts(row)
            groups[causal_key(facts)].append(facts)
    for members in groups.values():
        members.sort(key=lambda facts: facts.sort_key)
    return groups


def shared_path_suffix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    """The longest call path every observation in a group ends with.

    Its length is the evidence that one edit could address all of them: a
    suffix of one node is a shared destination, not a shared cause.
    """
    if not paths:
        return ()
    common: list[str] = []
    for nodes in zip(*(reversed(path) for path in paths), strict=False):
        if len(set(nodes)) != 1:
            break
        common.append(nodes[0])
    return tuple(reversed(common))


__all__ = [
    "PERFORMANCE_MODEL_VERSION",
    "CausalKey",
    "ExecutionContext",
    "causal_key",
    "cost_shape",
    "execution_context",
    "group_observations",
    "key_boundary",
    "key_context",
    "key_is_cross_function",
    "link_performance_findings",
    "opportunity_id_for_finding",
    "shared_path_suffix",
    "stable_id",
]
