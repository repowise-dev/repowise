"""``.repowise/policies.yaml``: change rules read, validated and evaluated.

A rule has the shape of a Kyverno rule (``match``, ``exclude``, then
conditions under ``when``) and an action from an Allstar-style ladder:
``warn``, ``require_owner_review``, ``require_tests`` or ``fail``. A rule
either tests conditions over a change's evidence, or wraps one of the existing
CI gates (``gate: security|coverage|doc_drift``), which runs exactly as its
own command does.

Evaluation reads a plain evidence mapping, ``{schema_version, change_id,
revspec, generated_at, items: [{kind, label, status, basis, value, details,
source}]}``, and only these detail fields:

* ``components_touched``: rows ``{name, criticality, tags, owner, files,
  bus_factor}``;
* ``provenance``: ``value`` is the change's label (``agent``,
  ``human_attested`` or ``unknown``), or a mapping holding it as ``label``;
  rows ``{sha, label, tier}``;
* ``reaching_tests``: one row per changed source file, ``{path, tests}``; ``tests``
  is ``[]`` only for a known absence and ``null`` when nothing knows;
* ``contracts``: rows ``{path, breaking}``;
* ``decisions_crossed``: rows ``{id, title, paths}``.

An item whose status is ``unknown`` or whose basis is ``unresolved`` answers
nothing, so a condition that needs it is unknown and the rule does not fire:
a missed policy hit is preferred to a false one. ``reaching_tests`` is the
exception: it is read per row unless its basis is ``unresolved``, since a
partly known item still answers for the files it knows. There are no risk-score
conditions; change risk is not calibrated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

POLICIES_FILE = ".repowise/policies.yaml"
MAX_POLICY_BYTES = 256_000

ACTIONS: tuple[str, ...] = ("warn", "require_owner_review", "require_tests", "fail")
GATES: tuple[str, ...] = ("security", "coverage", "doc_drift")
ATTRIBUTION_STATES: tuple[str, ...] = ("agent", "human_attested", "unknown")
CONDITIONS: tuple[str, ...] = (
    "attribution",
    "agent_tier",
    "reaching_tests",
    "contract_break",
    "decision_crossed",
    "bus_factor_below",
)
_SELECTOR_KEYS = ("names", "tags", "criticality")
_RULE_KEYS = ("name", "description", "match", "exclude", "when", "gate", "with", "action", "message")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

#: A rule's outcome. ``unknown``: evidence it needs was not available.
TRIGGERED, PASSED, NOT_APPLICABLE, UNKNOWN = "triggered", "passed", "not_applicable", "unknown"


class PolicyError(ValueError):
    """The policy file is invalid; ``problems`` lists every reason."""

    def __init__(self, source: str, problems: list[str]) -> None:
        super().__init__(f"{source}: " + "; ".join(problems))
        self.source = source
        self.problems = problems


@dataclass(frozen=True)
class Selector:
    """Components whose name, tags and criticality all match (each list is any-of)."""

    names: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    criticality: tuple[str, ...] = ()

    def matches(self, component: Mapping[str, Any]) -> bool:
        tags = {str(t).lower() for t in component.get("tags") or ()}
        crit = str(component.get("criticality") or "").lower()
        return (
            (not self.names or str(component.get("name", "")).lower() in self.names)
            and (not self.tags or bool(tags & set(self.tags)))
            and (not self.criticality or crit in self.criticality)
        )


@dataclass(frozen=True)
class Rule:
    name: str
    action: str
    description: str | None = None
    message: str | None = None
    match: Selector | None = None
    exclude: Selector | None = None
    when: Mapping[str, Any] = field(default_factory=dict)
    gate: str | None = None
    gate_args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Policy:
    rules: tuple[Rule, ...]
    source: str = POLICIES_FILE


# -- reading ------------------------------------------------------------------


def load_policy(repo_root: Path | str, path: Path | str | None = None) -> Policy | None:
    """The policy at *path* (default ``.repowise/policies.yaml``), or ``None`` when absent.

    Raises :class:`PolicyError` listing every problem in a file that exists.
    """
    root = Path(repo_root)
    file = Path(path) if path else root / POLICIES_FILE
    if not file.is_absolute():
        file = root / file
    if not file.is_file():
        if path:
            raise PolicyError(str(path), ["file not found"])
        return None
    source = file.relative_to(root).as_posix() if file.is_relative_to(root) else str(file)
    try:
        if file.stat().st_size > MAX_POLICY_BYTES:
            raise PolicyError(source, [f"larger than {MAX_POLICY_BYTES} bytes"])
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(source, [f"cannot read: {exc}"]) from exc
    return parse_policy(text, source)


def parse_policy(text: str, source: str = POLICIES_FILE) -> Policy:
    """Validate *text*; raises :class:`PolicyError` with every problem found."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(source, [f"invalid YAML: {exc}"]) from exc
    problems: list[str] = []
    if not isinstance(doc, dict):
        raise PolicyError(source, ["the file must be a mapping with a 'rules' list"])
    for key in sorted(set(doc) - {"version", "rules"}):
        problems.append(f"unknown top-level key {key!r} (expected version, rules)")
    if doc.get("version", 1) != 1:
        problems.append(f"version {doc.get('version')!r} is not supported (expected 1)")
    raw_rules = doc.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        problems.append("'rules' must be a non-empty list")
        raise PolicyError(source, problems)
    rules: list[Rule] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_rules):
        rule = _parse_rule(raw, f"rules[{i}]", problems)
        if rule is None:
            continue
        if rule.name in seen:
            problems.append(f"rules[{i}]: duplicate rule name {rule.name!r}")
        seen.add(rule.name)
        rules.append(rule)
    if problems:
        raise PolicyError(source, problems)
    return Policy(tuple(rules), source)


def _parse_rule(raw: Any, at: str, problems: list[str]) -> Rule | None:
    if not isinstance(raw, dict):
        problems.append(f"{at}: a rule must be a mapping")
        return None
    start = len(problems)
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        problems.append(
            f"{at}: 'name' is required: 1 to 63 of a-z, 0-9, _ and -, starting with a letter or digit"
        )
        name = None
    else:
        at = f"{at} ({name})"
    for key in sorted(set(raw) - set(_RULE_KEYS)):
        problems.append(f"{at}: unknown key {key!r}")
    gate = raw.get("gate")
    action = raw.get("action", "fail" if gate is not None else None)
    if action not in ACTIONS:
        problems.append(f"{at}: 'action' must be one of {', '.join(ACTIONS)}")
    for key in ("description", "message"):
        if key in raw and not isinstance(raw[key], str):
            problems.append(f"{at}: {key!r} must be a string")

    if gate is not None:
        _check_gate(raw, gate, at, problems)
        if len(problems) > start:
            return None
        return Rule(
            name=str(name),
            action=action,
            description=raw.get("description"),
            message=raw.get("message"),
            gate=gate,
            gate_args=dict(raw.get("with") or {}),
        )

    if "with" in raw:
        problems.append(f"{at}: 'with' only goes with 'gate'")
    match = _parse_selector(raw.get("match"), f"{at}.match", problems)
    exclude = _parse_selector(raw.get("exclude"), f"{at}.exclude", problems)
    when = _parse_when(raw.get("when"), f"{at}.when", problems)
    if len(problems) > start:
        return None
    return Rule(
        name=str(name),
        action=action,
        description=raw.get("description"),
        message=raw.get("message"),
        match=match,
        exclude=exclude,
        when=when,
    )


def _check_gate(raw: dict, gate: Any, at: str, problems: list[str]) -> None:
    if gate not in GATES:
        problems.append(f"{at}: 'gate' must be one of {', '.join(GATES)}")
    for key in ("match", "exclude", "when"):
        if key in raw:
            problems.append(f"{at}: {key!r} does not go with 'gate'; a gate judges the whole change")
    args = raw.get("with")
    if args is None:
        return
    if not isinstance(args, dict):
        problems.append(f"{at}.with: must be a mapping of the gate's options")
        return
    for key, value in args.items():
        ok = isinstance(value, str | int | float | bool) or (
            isinstance(value, list) and all(isinstance(v, str | int | float) for v in value)
        )
        if not isinstance(key, str) or not ok:
            problems.append(f"{at}.with.{key}: must be a string, number, boolean or list of them")


def _parse_selector(raw: Any, at: str, problems: list[str]) -> Selector | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"components"}:
        problems.append(f"{at}: must be a mapping with one key, 'components'")
        return None
    comps = raw["components"]
    if not isinstance(comps, dict) or not comps:
        problems.append(f"{at}.components: must name at least one of {', '.join(_SELECTOR_KEYS)}")
        return None
    out: dict[str, tuple[str, ...]] = {}
    for key, value in comps.items():
        if key not in _SELECTOR_KEYS:
            problems.append(f"{at}.components: unknown key {key!r} (expected {', '.join(_SELECTOR_KEYS)})")
            continue
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            problems.append(f"{at}.components.{key}: must be a string or a non-empty list of strings")
            continue
        out[key] = tuple(v.lower() for v in values)
    return Selector(**out)


def _parse_when(raw: Any, at: str, problems: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        problems.append(f"{at}: a rule needs at least one condition ({', '.join(CONDITIONS)}) or a 'gate'")
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in CONDITIONS:
            hint = " (risk is not calibrated, so there is no risk condition)" if "risk" in str(key) else ""
            problems.append(f"{at}: unknown condition {key!r}{hint}")
        elif key == "attribution":
            states = [value] if isinstance(value, str) else value
            if not isinstance(states, list) or not states or any(s not in ATTRIBUTION_STATES for s in states):
                problems.append(f"{at}.attribution: must be one or a list of {', '.join(ATTRIBUTION_STATES)}")
            else:
                out[key] = tuple(states)
        elif key == "agent_tier":
            tiers = [value] if isinstance(value, int) else value
            if not isinstance(tiers, list) or not tiers or any(t not in (1, 2, 3) for t in tiers):
                problems.append(f"{at}.agent_tier: must be one or a list of 1, 2, 3")
            else:
                out[key] = tuple(tiers)
        elif key == "reaching_tests":
            if value not in ("missing", "present"):
                problems.append(f"{at}.reaching_tests: must be 'missing' or 'present'")
            else:
                out[key] = value
        elif key in ("contract_break", "decision_crossed"):
            if not isinstance(value, bool):
                problems.append(f"{at}.{key}: must be true or false")
            else:
                out[key] = value
        elif key == "bus_factor_below":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(f"{at}.bus_factor_below: must be a whole number of at least 1")
            else:
                out[key] = value
    return out


# -- evaluating ---------------------------------------------------------------


#: ``(path, line or None, the finding's own fingerprint or None)``.
Location = tuple[str, int | None, str | None]


@dataclass(frozen=True)
class GateRun:
    """What a wrapped gate command returned: its exit code and a one-line summary."""

    exit_code: int
    summary: str
    #: One per finding the gate failed on.
    locations: tuple[Location, ...] = ()


@dataclass
class RuleResult:
    rule: Rule
    outcome: str
    message: str
    reasons: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    #: Evidence kinds the rule read, with the basis each had.
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def fails(self) -> bool:
        return self.outcome == TRIGGERED and self.rule.action == "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule.name,
            "action": self.rule.action,
            "gate": self.rule.gate,
            "outcome": self.outcome,
            "message": self.message,
            "reasons": self.reasons,
            "components": self.components,
            "owners": self.owners,
            "locations": [
                {"path": p, "line": ln, **({"fingerprint": fp} if fp else {})}
                for p, ln, fp in self.locations
            ],
            "evidence": self.evidence,
        }


def evaluate(
    policy: Policy,
    evidence: Mapping[str, Any] | None,
    gate_runs: Mapping[str, GateRun] | None = None,
) -> list[RuleResult]:
    """One result per rule, in file order. *gate_runs* is keyed by rule name."""
    items = {i.get("kind"): i for i in (evidence or {}).get("items") or () if isinstance(i, Mapping)}
    gate_runs = gate_runs or {}
    return [
        _gate_result(rule, gate_runs.get(rule.name)) if rule.gate else _rule_result(rule, items)
        for rule in policy.rules
    ]


def _gate_result(rule: Rule, run: GateRun | None) -> RuleResult:
    if run is None or run.exit_code not in (0, 1):
        why = run.summary if run else "the gate did not run"
        return RuleResult(rule, UNKNOWN, f"The {rule.gate} gate could not be evaluated: {why}")
    if run.exit_code == 0:
        return RuleResult(rule, PASSED, f"The {rule.gate} gate passed. {run.summary}".strip())
    return RuleResult(
        rule,
        TRIGGERED,
        rule.message or f"The {rule.gate} gate failed. {run.summary}".strip(),
        locations=list(run.locations),
    )


def _known(item: Mapping[str, Any] | None) -> bool:
    return item is not None and item.get("status") != "unknown" and item.get("basis") != "unresolved"


def _rows(item: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    return [r for r in (item or {}).get("details") or () if isinstance(r, Mapping)]


def _rule_result(rule: Rule, items: Mapping[str, Mapping[str, Any]]) -> RuleResult:
    comps_item = items.get("components_touched")
    touched = _rows(comps_item) if _known(comps_item) else []
    used: dict[str, str] = {}
    if rule.match or rule.exclude:
        if not _known(comps_item):
            return RuleResult(
                rule, UNKNOWN, "Components touched are not known, so the match cannot be checked.",
                evidence={"components_touched": _basis(comps_item)},
            )
        used["components_touched"] = _basis(comps_item)
        matched = [
            c for c in touched
            if (rule.match is None or rule.match.matches(c))
            and not (rule.exclude and rule.exclude.matches(c))
        ]
        if not matched:
            return RuleResult(rule, NOT_APPLICABLE, "No touched component matches this rule.", evidence=used)
        scope: set[str] | None = {str(f) for c in matched for f in c.get("files") or ()}
    else:
        matched = touched
        scope = None  # the whole change

    reasons: list[str] = []
    unknown: list[str] = []
    locations: list[Location] = []
    for cond, want in rule.when.items():
        verdict, why, where, kind = _CHECKS[cond](want, items, scope, matched)
        if kind:
            used[kind] = _basis(items.get(kind))
        if verdict is None:
            unknown.append(why)
        elif not verdict:
            return RuleResult(rule, PASSED, f"Not met: {why}.", evidence=used)
        else:
            reasons.append(why)
            locations += where
    names = [str(c.get("name")) for c in matched]
    owners = sorted({str(c["owner"]) for c in matched if c.get("owner")})
    if unknown:
        return RuleResult(
            rule, UNKNOWN, "Not evaluated: " + "; ".join(unknown) + ".",
            reasons=reasons, components=names, evidence=used,
        )
    if not locations and scope:
        locations = [(p, None, None) for p in sorted(scope)[:1]]
    return RuleResult(
        rule,
        TRIGGERED,
        rule.message or _default_message(rule, reasons, names, owners),
        reasons=reasons,
        components=names,
        owners=owners,
        locations=_dedupe(locations),
        evidence=used,
    )


def _default_message(rule: Rule, reasons: list[str], names: list[str], owners: list[str]) -> str:
    where = f" in {', '.join(names)}" if names else ""
    need = {
        "require_owner_review": " Needs review by "
        + (", ".join(owners) if owners else "the component owner")
        + ".",
        "require_tests": " Needs a test that reaches the changed code.",
    }.get(rule.action, "")
    return f"{rule.name}{where}: {'; '.join(reasons)}.{need}"


def _basis(item: Mapping[str, Any] | None) -> str:
    return str(item.get("basis") or "unresolved") if _known(item) else "unresolved"


def _dedupe(locations: Iterable[Location]) -> list[Location]:
    return list(dict.fromkeys(locations))


# Each check returns (verdict: True/False/None for unknown, reason, locations, evidence kind).
Check = tuple[bool | None, str, list[Location], str]


def _provenance_label(item: Mapping[str, Any]) -> str | None:
    value = item.get("value")
    return value.get("label") if isinstance(value, Mapping) else value


def _attribution(want, items, scope, matched) -> Check:
    item = items.get("provenance")
    label = _provenance_label(item) if _known(item) else None
    if label not in ATTRIBUTION_STATES:
        return None, "attribution is not known", [], "provenance"
    return label in want, f"attribution is {label}", [], "provenance"


def _agent_tier(want, items, scope, matched) -> Check:
    item = items.get("provenance")
    if not _known(item):
        return None, "attribution is not known", [], "provenance"
    tiers = sorted({r.get("tier") for r in _rows(item) if r.get("label") == "agent" and r.get("tier")})
    hit = [t for t in tiers if t in want]
    return bool(hit), f"agent tier {', '.join(map(str, hit or tiers)) or 'none'}", [], "provenance"


def _reaching_tests(want, items, scope, matched) -> Check:
    item = items.get("reaching_tests")
    # Rows carry each file's answer, so a partly known item (``unknown`` with
    # a real basis) still decides the files in scope it knows about.
    if item is None or item.get("basis") == "unresolved":
        return None, "reaching tests are not known", [], "reaching_tests"
    rows = [r for r in _rows(item) if r.get("path") and (scope is None or r["path"] in scope)]
    # ``tests: []`` is a known absence; a missing or null list is not known.
    untested = [str(r["path"]) for r in rows if r.get("tests") == []]
    unknown = [r for r in rows if not isinstance(r.get("tests"), list)]
    if untested:
        why = f"{len(untested)} changed file(s) with no reaching test"
        return want == "missing", why, [(p, None, None) for p in untested], "reaching_tests"
    if not rows:
        return False, "no changed source file in scope", [], "reaching_tests"
    if unknown:
        return None, "reaching tests are not known for every changed file", [], "reaching_tests"
    return want == "present", "every changed file has a reaching test", [], "reaching_tests"


def _contract_break(want, items, scope, matched) -> Check:
    item = items.get("contracts")
    if not _known(item):
        return None, "contract changes are not known", [], "contracts"
    broken = [
        str(r["path"]) for r in _rows(item)
        if r.get("breaking") and r.get("path") and (scope is None or r["path"] in scope)
    ]
    found = bool(broken)
    why = f"{len(broken)} breaking contract change(s)" if found else "no breaking contract change"
    return found == want, why, [(p, None, None) for p in broken], "contracts"


def _decision_crossed(want, items, scope, matched) -> Check:
    item = items.get("decisions_crossed")
    if not _known(item):
        return None, "decisions crossed are not known", [], "decisions_crossed"
    hits: list[str] = []
    where: list[Location] = []
    for r in _rows(item):
        paths = [str(p) for p in r.get("paths") or ()]
        inside = paths if scope is None else [p for p in paths if p in scope]
        if inside or (not paths and scope is None):
            hits.append(str(r.get("title") or r.get("id")))
            where += [(p, None, None) for p in inside]
    found = bool(hits)
    why = f"crosses decision(s): {', '.join(hits)}" if found else "crosses no governing decision"
    return found == want, why, where, "decisions_crossed"


def _bus_factor_below(want, items, scope, matched) -> Check:
    known = [c for c in matched if isinstance(c.get("bus_factor"), int)]
    if not matched or len(known) < len(matched):
        return None, "bus factor is not known for every component", [], "components_touched"
    low = [f"{c['name']} ({c['bus_factor']})" for c in known if c["bus_factor"] < want]
    why = f"bus factor below {want}: {', '.join(low)}" if low else f"bus factor at least {want}"
    return bool(low), why, [], "components_touched"


_CHECKS = {
    "attribution": _attribution,
    "agent_tier": _agent_tier,
    "reaching_tests": _reaching_tests,
    "contract_break": _contract_break,
    "decision_crossed": _decision_crossed,
    "bus_factor_below": _bus_factor_below,
}


def exit_code(results: Iterable[RuleResult]) -> int:
    """1 when a ``fail`` rule fired; else 2 when a ``fail`` gate could not run; else 0."""
    results = list(results)
    if any(r.fails for r in results):
        return 1
    if any(r.rule.gate and r.rule.action == "fail" and r.outcome == UNKNOWN for r in results):
        return 2
    return 0
