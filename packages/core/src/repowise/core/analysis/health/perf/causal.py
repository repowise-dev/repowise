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
from itertools import pairwise
from typing import Any, Literal

from repowise.core.test_paths import is_test_related_path

from .facts import ObservationFacts, detail_map, is_performance, observation_facts

PERFORMANCE_MODEL_VERSION = 3
"""Version of the identity, grouping, actionability, and ranking semantics.

From version 2 the version is the id prefix rather than a hash input, so a
stale id is recognisable without a lookup and :func:`model_state` can answer
from the string alone. Version 1 ids carry no digit and remain readable as
version 1. Moving this constant means bumping ``HEALTH_ANALYZER_VERSION`` with
it, which forces the rescore that restamps every stored finding.

Version 3 keys a cause by its intervention, not by the sink it reaches: one
loop reaching three sinks was three opportunities under version 2. It also
classes schema migrations as ``tooling`` in :func:`execution_context`, a kernel
input.
"""

ExecutionContext = Literal["production", "tooling", "test", "unknown"]

# Where the edit lands: the function holding the loop, a helper every caller
# reaches the sink through, or top-level script code.
InterventionKind = Literal["function", "shared_helper", "module"]

MODULE_SCOPE = "__module__"
"""The symbol name for top-level code, the same synthetic node the graph uses."""

CausalKey = tuple[Any, ...]

_ID_PREFIX = "perf"
_ID_PATTERN = re.compile(rf"^{_ID_PREFIX}(\d*)_[0-9a-f]{{20}}$")

_TOOLING_PARTS = frozenset(
    {
        ".github",
        "benchmarks",
        "build",
        "devtools",
        # Schema migrations run once per deploy, not per request: Django and
        # Flask-Migrate ``migrations/``, EF Core ``Migrations/``.
        "migrations",
        "scripts",
        "tooling",
        "tools",
    }
)

_TOOLING_DIR_PAIRS = frozenset({("db", "migrate"), ("alembic", "versions")})
"""Adjacent directories that mark migrations where neither name does alone.

Rails keeps them in ``db/migrate``. Alembic keeps them in ``versions/`` under
its script directory; a bare ``versions/`` is too common (API versions) to
class on its own. Ceiling: an Alembic script directory with another name is
recognised only by its sibling ``env.py``, which a path-only classifier cannot
see.
"""

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


def code_context(file_path: str) -> ExecutionContext:
    """Whether this code ships, without :func:`execution_context`'s CLI rule.

    A CLI is product code an edit can improve, so code-shape surfaces read
    this; only performance treats a CLI loop as off the request path.
    """
    normalized = file_path.replace("\\", "/")
    if not normalized:
        return "unknown"
    if is_test_related_path(file_path):
        return "test"
    segments = normalized.lower().split("/")
    parts = set(segments)
    if parts & _TOOLING_PARTS:
        return "tooling"
    if any(pair in _TOOLING_DIR_PAIRS for pair in pairwise(segments[:-1])):
        return "tooling"
    if parts & _UNCLASSIFIABLE_PARTS or "/" not in normalized:
        return "unknown"
    return "production"


def execution_context(file_path: str) -> ExecutionContext:
    """Where this code runs. An identity input, so it is classified once.

    ``unknown`` is a positive answer, not a gap: a path with no directory, or
    one under a directory whose execution role is genuinely ambiguous, carries
    no evidence either way. A CLI loop is not a request path, so CLI code is
    ``tooling`` here.
    """
    context = code_context(file_path)
    normalized = file_path.replace("\\", "/").lower()
    if context in ("production", "unknown") and "/cli/" in f"/{normalized}/":
        return "tooling"
    return context


def cost_shape(marker: str) -> str:
    """Compatibility family for observations that may share one intervention."""
    if marker in {"io_in_loop", "nested_loop_with_io"}:
        return "repeated_io"
    return marker


def shared_helper(facts: ObservationFacts) -> str | None:
    """The helper the repetition passes through, when the loop is not the edit.

    On ``loop owner -> helper -> ... -> sink`` every caller reaches the sink
    through the sink's immediate caller, so that helper is the one place a
    batched form settles them all. A two-node path has no helper: the loop
    owner calls the sink-holding function itself, so the loop is the edit.
    """
    if facts.cross_function and facts.path_depth >= 3:
        return facts.meaningful_predecessor
    return None


def causal_key(facts: ObservationFacts) -> CausalKey:
    """The v3 identity kernel: one cause per intervention and cost family.

    The unit is the place a person edits. A loop is named by the function that
    holds it, so the sinks it reaches, the call sites inside it, and the
    co-signals of one cost family (``io_in_loop`` with ``nested_loop_with_io``)
    are members of one cause, not one cause each. A shared helper is named by
    itself, so unrelated callers that reach a sink through it stay one cause,
    and unrelated callers of a generic sink stay apart because each loop owner
    is its own intervention.

    Boundary stays in: a loop doing database and filesystem work holds two
    different changes. The loop's own line does not: two loops in one function
    are one edit site, and their lines stay in the evidence. That is a ceiling,
    not a claim that they are one loop: findings record the sink's line and not
    the loop's, so splitting per loop would need the walk to carry the loop
    line into ``details`` first.

    Everything outside these tuples is display or derived data and stays out:
    prose, lines, sinks, storage ids, rank factors, confidence, reachability,
    and provenance.
    """
    context = execution_context(facts.file_path)
    family = cost_shape(facts.marker)
    helper = shared_helper(facts)
    if helper is not None:
        return ("shared_helper", context, family, facts.boundary_kind, helper)
    return (
        "function",
        context,
        family,
        facts.boundary_kind,
        facts.file_path,
        facts.function_name or None,
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


def key_intervention_kind(key: CausalKey) -> InterventionKind:
    if key[0] == "shared_helper":
        return "shared_helper"
    return "function" if key[5] else "module"


def key_intervention_symbol(key: CausalKey) -> str:
    """The place the whole group shares, and therefore the place to edit.

    Every group has one: top-level script code is named for its module.
    """
    if key[0] == "shared_helper":
        return key[4]
    return f"{key[4]}::{key[5] or MODULE_SCOPE}"


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
    "MODULE_SCOPE",
    "PERFORMANCE_MODEL_VERSION",
    "CausalKey",
    "ExecutionContext",
    "InterventionKind",
    "causal_key",
    "cost_shape",
    "execution_context",
    "group_observations",
    "key_boundary",
    "key_context",
    "key_intervention_kind",
    "key_intervention_symbol",
    "link_performance_findings",
    "model_state",
    "opportunity_id_for_finding",
    "opportunity_id_model_version",
    "shared_helper",
    "shared_path_suffix",
    "stable_id",
]
