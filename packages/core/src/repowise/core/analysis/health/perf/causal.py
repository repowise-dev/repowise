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
import re
from collections import defaultdict
from typing import Any, Literal

from repowise.core.test_paths import is_test_related_path

from .facts import ObservationFacts, detail_map, is_performance, observation_facts

PERFORMANCE_MODEL_VERSION = 2
"""Version of the identity, grouping, actionability, and ranking semantics.

From version 2 the version is the id prefix rather than a hash input, so a
stale id is recognisable without a lookup and :func:`model_state` can answer
from the string alone. Version 1 ids carry no digit and remain readable as
version 1. Moving this constant means bumping ``HEALTH_ANALYZER_VERSION`` with
it, which forces the rescore that restamps every stored finding.
"""

ExecutionContext = Literal["production", "tooling", "test", "unknown"]

CausalKey = tuple[Any, ...]

_ID_PREFIX = "perf"
_ID_PATTERN = re.compile(rf"^{_ID_PREFIX}(\d*)_[0-9a-f]{{20}}$")

_TOOLING_PARTS = frozenset(
    {".github", "benchmarks", "build", "devtools", "scripts", "tooling", "tools"}
)

_UNCLASSIFIABLE_PARTS = frozenset(
    {
        "demo",
        "demos",
        "doc",
        "docs",
        "example",
        "examples",
        "sample",
        "samples",
        "third_party",
        "thirdparty",
        "vendor",
    }
)
"""Directories that do not say whether their code ships.

Reporting these as production would assert exposure the tree does not support,
which is the one thing the fallback must not do.
"""


def execution_context(file_path: str) -> ExecutionContext:
    """Where this code runs. An identity input, so it is classified once.

    ``unknown`` is a positive answer, not a gap: a path with no directory, or
    one under a directory whose execution role is genuinely ambiguous, carries
    no evidence either way.
    """
    normalized = file_path.replace("\\", "/")
    if not normalized:
        return "unknown"
    if is_test_related_path(file_path):
        return "test"
    parts = {part.lower() for part in normalized.split("/")}
    if parts & _TOOLING_PARTS or "/cli/" in f"/{normalized.lower()}/":
        return "tooling"
    if parts & _UNCLASSIFIABLE_PARTS or "/" not in normalized:
        return "unknown"
    return "production"


def cost_shape(marker: str) -> str:
    """Compatibility family for observations that may share one intervention."""
    if marker in {"io_in_loop", "nested_loop_with_io"}:
        return "repeated_io"
    return marker


def causal_key(facts: ObservationFacts) -> CausalKey:
    """The v2 identity kernel.

    A cross-function cause is named by the pair the intervention lives in:
    the sink that pays the cost and the caller that repeats it. Naming it by
    the sink alone merged every workflow that happened to reach a shared
    infrastructure helper, so a session opener reached from many unrelated
    callers read as one cause. Requiring the caller to match splits those, and
    leaves a genuinely shared helper merged however many callers reach it,
    because they all reach the sink through that helper.

    A same-function cause has no call path and is named by its own location.

    Everything outside these tuples is display or derived data and stays out:
    prose, line ends, storage ids, rank factors, confidence, reachability, and
    provenance.
    """
    context = execution_context(facts.file_path)
    predecessor = facts.meaningful_predecessor
    if facts.cross_function and predecessor is not None:
        return (
            "cross-function",
            context,
            cost_shape(facts.marker),
            facts.boundary_kind,
            predecessor,
            facts.terminal_sink,
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
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{_ID_PREFIX}{PERFORMANCE_MODEL_VERSION}_{digest}"


def key_context(key: CausalKey) -> ExecutionContext:
    """Both kernel branches carry the execution context in the same slot."""
    return key[1]


def key_boundary(key: CausalKey) -> str | None:
    """Both kernel branches carry the boundary kind in the same slot."""
    return key[3]


def key_is_cross_function(key: CausalKey) -> bool:
    return key[0] == "cross-function"


def key_intervention_symbol(key: CausalKey) -> str | None:
    """The caller the whole group shares, and therefore the place to edit."""
    return key[4] if key_is_cross_function(key) else None


def key_terminal_sink(key: CausalKey) -> str | None:
    """The sink the whole group shares. Read off the key, never off a member."""
    return key[5] if key_is_cross_function(key) else None


def opportunity_id_model_version(opportunity_id: str) -> int | None:
    """Which model minted this id, or nothing if it was not minted here.

    Version 1 ids carry no digit; from version 2 the prefix names the model.
    """
    match = _ID_PATTERN.match(opportunity_id)
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def model_state(opportunity_id: str) -> dict[str, Any]:
    """Whether an id can still be resolved, and what to do when it cannot.

    Ids are not translated across models. Grouping decides membership, so a
    v1 id can name observations that v2 splits several ways, and an alias would
    have to invent which split the caller meant. Reporting the mismatch and the
    refresh that fixes it is the only honest answer.
    """
    version = opportunity_id_model_version(opportunity_id)
    if version == PERFORMANCE_MODEL_VERSION:
        state = "current"
    elif version is None:
        state = "unrecognized"
    else:
        state = "stale_model"
    return {
        "state": state,
        "opportunity_id": opportunity_id,
        "requested_model_version": version,
        "performance_model_version": PERFORMANCE_MODEL_VERSION,
        "refresh_required": state == "stale_model",
    }


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
    "key_intervention_symbol",
    "key_is_cross_function",
    "key_terminal_sink",
    "link_performance_findings",
    "model_state",
    "opportunity_id_for_finding",
    "opportunity_id_model_version",
    "shared_path_suffix",
    "stable_id",
]
