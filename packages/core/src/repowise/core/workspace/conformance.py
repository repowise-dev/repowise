"""Architecture conformance — check declared dependency rules + find cycles.

A team declares, in ``.repowise-workspace.yaml``, which services are *allowed* to
depend on which others (``frontend !-> db``, ``* !-> legacy-payments``). This
module checks those rules against the structural edges of the system graph and
reports every violating dependency, and — together with
:mod:`repowise.core.workspace.cycles` — assembles the workspace's architecture
governance report (violations + dependency cycles).

**Rule vocabulary.** A rule's ``source`` / ``target`` are *matchers* resolved
against service nodes by a small registry (the D10 / PR #505 plugin shape — a new
matcher form is a decorated resolver, never an ``if/elif``):

* ``"*"`` — any service.
* ``"tag:<name>"`` — every service whose repo carries that tag (tags are declared
  on each ``RepoEntry`` and inherited by its service nodes).
* any other string — a glob matched against the node id, repo alias, and display
  name, so ``"frontend"`` matches a repo and ``"api::*"`` a specific service.

A rule with ``allow=False`` (the default) is a *deny* rule: a structural
dependency from a matching source to a matching target is a violation. A rule
with ``allow=True`` is an *exception* that whitelists an otherwise-denied edge
(deny ``* !-> db`` but allow ``migrations -> db``).

**Structural edges only.** Conformance is about real dependencies; behavioral
co-change edges are never evaluated.

Pure and I/O-free except the thin ``save`` / ``load`` helpers and the
``run_conformance_check`` orchestrator at the bottom.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from repowise.core.workspace.config import (
    WORKSPACE_DATA_DIR,
    ConformanceRule,
    WorkspaceConfig,
    ensure_workspace_data_dir,
)
from repowise.core.workspace.cycles import DependencyCycle, detect_cycles
from repowise.core.workspace.system_graph import SystemGraph, SystemNode

_log = logging.getLogger("repowise.workspace.conformance")

# ---------------------------------------------------------------------------
# Constants (single source of truth)
# ---------------------------------------------------------------------------

CONFORMANCE_FILENAME = "conformance.json"

#: A rule violation always reads as an error; cycles read as a warning by default
#: (a cycle is a smell, a denied dependency is an explicit policy breach).
SEVERITY_VIOLATION = "violation"


# ---------------------------------------------------------------------------
# Matcher resolver registry (plugin shape — add a form = add a resolver)
# ---------------------------------------------------------------------------

#: A compiled matcher: does this node match, given the repo→tags map?
MatcherPredicate = Callable[[SystemNode, dict[str, list[str]]], bool]

#: A resolver inspects a raw matcher string and returns a predicate if it owns
#: that form, else ``None`` so the next resolver gets a turn.
MatcherResolver = Callable[[str], "MatcherPredicate | None"]

_MATCHER_RESOLVERS: list[MatcherResolver] = []


def matcher_resolver(fn: MatcherResolver) -> MatcherResolver:
    """Register a matcher-string resolver. First to claim a form wins."""
    _MATCHER_RESOLVERS.append(fn)
    return fn


@matcher_resolver
def _wildcard_matcher(raw: str) -> MatcherPredicate | None:
    """``"*"`` matches every service."""
    if raw.strip() == "*":
        return lambda node, tags_by_repo: True
    return None


@matcher_resolver
def _tag_matcher(raw: str) -> MatcherPredicate | None:
    """``"tag:<name>"`` matches services whose repo carries that tag."""
    prefix = "tag:"
    if not raw.startswith(prefix):
        return None
    tag = raw[len(prefix) :].strip()

    def _match(node: SystemNode, tags_by_repo: dict[str, list[str]]) -> bool:
        return tag in tags_by_repo.get(node.repo, [])

    return _match


@matcher_resolver
def _glob_matcher(raw: str) -> MatcherPredicate | None:
    """Fallback: glob the node id, repo alias, and display name.

    Always claims the string, so it must stay registered last. A bare alias
    (``"frontend"``) matches the repo and its nodes; a pattern (``"api::*"``)
    targets a specific service.
    """
    pattern = raw.strip()

    def _match(node: SystemNode, tags_by_repo: dict[str, list[str]]) -> bool:
        return (
            fnmatchcase(node.id, pattern)
            or fnmatchcase(node.repo, pattern)
            or fnmatchcase(node.name, pattern)
        )

    return _match


def compile_matcher(raw: str) -> MatcherPredicate:
    """Compile a matcher string to a predicate via the resolver registry."""
    for resolver in _MATCHER_RESOLVERS:
        predicate = resolver(raw)
        if predicate is not None:
            return predicate
    # Unreachable while the glob fallback is registered; defensive only.
    raise ValueError(f"No matcher resolver claimed: {raw!r}")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ConformanceViolation:
    """A structural dependency that a deny rule forbids."""

    rule_source: str  # the rule's source matcher
    rule_target: str  # the rule's target matcher
    rule_description: str
    source: str  # offending node id (depends on target)
    source_name: str
    target: str  # node id depended upon
    target_name: str
    edge_id: str
    edge_kind: str
    severity: str = SEVERITY_VIOLATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_source": self.rule_source,
            "rule_target": self.rule_target,
            "rule_description": self.rule_description,
            "source": self.source,
            "source_name": self.source_name,
            "target": self.target,
            "target_name": self.target_name,
            "edge_id": self.edge_id,
            "edge_kind": self.edge_kind,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConformanceViolation:
        return cls(
            rule_source=data.get("rule_source", ""),
            rule_target=data.get("rule_target", ""),
            rule_description=data.get("rule_description", ""),
            source=data.get("source", ""),
            source_name=data.get("source_name", ""),
            target=data.get("target", ""),
            target_name=data.get("target_name", ""),
            edge_id=data.get("edge_id", ""),
            edge_kind=data.get("edge_kind", ""),
            severity=data.get("severity", SEVERITY_VIOLATION),
        )


@dataclass
class ConformanceReport:
    """The workspace's architecture governance result: violations + cycles."""

    version: int = 1
    generated_at: str = ""
    rules_evaluated: int = 0
    violations: list[ConformanceViolation] = field(default_factory=list)
    cycles: list[DependencyCycle] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.violations or self.cycles)

    @property
    def violating_repos(self) -> list[str]:
        repos: set[str] = set()
        for v in self.violations:
            repos.add(v.source.split("::", 1)[0])
            repos.add(v.target.split("::", 1)[0])
        return sorted(repos)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "rules_evaluated": self.rules_evaluated,
            "violations": [v.to_dict() for v in self.violations],
            "cycles": [c.to_dict() for c in self.cycles],
            "violation_count": len(self.violations),
            "cycle_count": len(self.cycles),
            "violating_repos": self.violating_repos,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConformanceReport:
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            rules_evaluated=data.get("rules_evaluated", 0),
            violations=[ConformanceViolation.from_dict(v) for v in data.get("violations", [])],
            cycles=[DependencyCycle.from_dict(c) for c in data.get("cycles", [])],
        )


# ---------------------------------------------------------------------------
# Checking (pure)
# ---------------------------------------------------------------------------


@dataclass
class _CompiledRule:
    """A rule with its source/target matchers precompiled."""

    rule: ConformanceRule
    source_pred: MatcherPredicate
    target_pred: MatcherPredicate

    def matches(self, src: SystemNode, tgt: SystemNode, tags_by_repo: dict[str, list[str]]) -> bool:
        return self.source_pred(src, tags_by_repo) and self.target_pred(tgt, tags_by_repo)


def _compile(rule: ConformanceRule) -> _CompiledRule:
    return _CompiledRule(
        rule=rule,
        source_pred=compile_matcher(rule.source),
        target_pred=compile_matcher(rule.target),
    )


def check_conformance(
    graph: SystemGraph,
    rules: list[ConformanceRule],
    tags_by_repo: dict[str, list[str]] | None = None,
) -> list[ConformanceViolation]:
    """Return the structural dependencies that violate the deny rules.

    For every structural edge ``source -> target``, a deny rule whose matchers
    cover both ends raises a violation, unless an ``allow`` rule also covers the
    edge (an explicit exception). Pure and O(edges x rules); the graph is
    service-granular so that stays small.
    """
    tags_by_repo = tags_by_repo or {}
    deny = [_compile(r) for r in rules if not r.allow]
    allow = [_compile(r) for r in rules if r.allow]
    if not deny:
        return []

    nodes_by_id = {n.id: n for n in graph.nodes}
    violations: list[ConformanceViolation] = []

    for edge in graph.edges:
        if not edge.structural:
            continue
        src = nodes_by_id.get(edge.source)
        tgt = nodes_by_id.get(edge.target)
        if src is None or tgt is None:
            continue
        for compiled in deny:
            if not compiled.matches(src, tgt, tags_by_repo):
                continue
            # A matching allow rule whitelists this specific edge.
            if any(a.matches(src, tgt, tags_by_repo) for a in allow):
                break
            violations.append(
                ConformanceViolation(
                    rule_source=compiled.rule.source,
                    rule_target=compiled.rule.target,
                    rule_description=compiled.rule.description,
                    source=src.id,
                    source_name=src.name,
                    target=tgt.id,
                    target_name=tgt.name,
                    edge_id=edge.id,
                    edge_kind=edge.kind,
                )
            )
            break  # one violation per edge — the first deny rule that fires

    violations.sort(key=lambda v: (v.source, v.target, v.edge_kind))
    return violations


def build_conformance_report(
    graph: SystemGraph,
    rules: list[ConformanceRule],
    tags_by_repo: dict[str, list[str]] | None = None,
    *,
    generated_at: str = "",
) -> ConformanceReport:
    """Assemble the full governance report: rule violations + dependency cycles."""
    violations = check_conformance(graph, rules, tags_by_repo)
    cycles = detect_cycles(graph)
    return ConformanceReport(
        generated_at=generated_at,
        rules_evaluated=len(rules),
        violations=violations,
        cycles=cycles,
    )


def tags_by_repo_from_config(ws_config: WorkspaceConfig) -> dict[str, list[str]]:
    """Build the repo-alias → tags map service nodes inherit for matching."""
    return {entry.alias: list(entry.tags) for entry in ws_config.repos}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_conformance_report(report: ConformanceReport, workspace_root: Path) -> Path:
    """Write the report to ``.repowise-workspace/conformance.json``."""
    data_dir = ensure_workspace_data_dir(workspace_root)
    out_path = data_dir / CONFORMANCE_FILENAME
    out_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def load_conformance_report(workspace_root: Path) -> ConformanceReport | None:
    """Load the report, or ``None`` if missing/unparseable."""
    path = workspace_root / WORKSPACE_DATA_DIR / CONFORMANCE_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ConformanceReport.from_dict(data)
    except Exception:
        _log.warning("Failed to load conformance report from %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_conformance_check(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    graph: SystemGraph,
    *,
    generated_at: str = "",
) -> ConformanceReport:
    """Build and persist the conformance report from the latest system graph.

    Called from ``run_cross_repo_hooks`` after the system graph is built. Cycle
    detection runs even with no rules declared, so a workspace gets the cycle
    half of governance for free.
    """
    report = build_conformance_report(
        graph,
        ws_config.conformance.rules,
        tags_by_repo_from_config(ws_config),
        generated_at=generated_at,
    )
    out_path = save_conformance_report(report, workspace_root)
    _log.info(
        "Conformance check complete: %d violation(s), %d cycle(s) from %d rule(s) → %s",
        len(report.violations),
        len(report.cycles),
        report.rules_evaluated,
        out_path,
    )
    return report
