"""A refactoring plan, or a composed opportunity of plans, handed to an agent.

Both read the plan through its recipe (:func:`build_recipe`), so the steps an
agent is told to make are the ones plan detail, the CLI and code generation
read. Takes the wire dicts the REST and MCP detail calls already serve.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from repowise.core.analysis.health.refactoring.recipe import build_recipe, label, verify_check

from .preamble import preamble
from .sections import bullet_list, closing_sections, join_sections, repo_suffix

_CONSTRAINTS = (
    "**Confirm before changing.** The plan comes from stored static analysis; the code may have moved since it ran.",
    "**Say when it is wrong.** If the plan does not fit the real code, stop and explain why instead of forcing it.",
)

_PLAN_EXPECTED = (
    "1. The refactored code, with each step applied.",
    "2. What you named or introduced, and why.",
    "3. The Verify run, before and after, with its result.",
    "4. Any call sites or files you also had to change.",
)


def _loc(file: Any, span: Mapping[str, int] | None) -> str:
    if not span:
        return f"`{file}`"
    end = f"-{span['end']}" if span["end"] != span["start"] else ""
    return f"`{file}:{span['start']}{end}`"


def _symbol_lines(step: Mapping[str, Any]) -> list[str]:
    """What an Extract Method step adds: the texts to copy and the slots."""
    symbol = step.get("new_symbol") or {}
    call = step.get("call_site") or {}
    span = call.get("replace_span") or step.get("span") or {}
    out = []
    if symbol.get("signature_text"):
        out.append(f"New helper: `{symbol['signature_text']}`")
    if call.get("new_text"):
        lines = f"{span.get('start')}-{span.get('end')}"
        out.append(f"Replace lines {lines} with: `{call['new_text']}`")
    if symbol:
        out.append(f"Parameters (in): {', '.join(symbol.get('params') or []) or '(none)'}")
        out.append(f"Returns (out): {', '.join(symbol.get('returns') or []) or '(nothing)'}")
    out.extend(f"Note: {note}" for note in symbol.get("notes") or [])
    return out


def _step_lines(step: Mapping[str, Any]) -> list[str]:
    """The sub-bullets under one step."""
    out = _symbol_lines(step)
    if step.get("applicability"):
        out.append(f"{str(step['applicability']).capitalize()} step.")
    verify = step.get("verify") or {}
    if verify.get("commands"):
        out.append("Verify this step: " + "; ".join(f"`{c}`" for c in verify["commands"]))
    return out


def _step_entry(step: Mapping[str, Any], marker: str, pad: str) -> str:
    where = f" ({_loc(step['file'], step.get('span'))})" if step.get("file") else ""
    head = f"{pad}{marker} {step['text']}{where}"
    return "\n".join([head, *(f"{pad}   - {line}" for line in _step_lines(step))])


def _verify_lines(check: Mapping[str, Any], *, runnable: bool = True) -> list[str]:
    """The guarding tests, most direct first; with *runnable*, an instruction to
    run them and the command (a reader that cannot run anything gets neither)."""
    tests = check.get("tests") or []
    if not tests:
        return [check["text"]]
    reasons = check.get("reasons") or {}
    rows = [f"- `{t}`" + (f" ({reasons[t]})" if reasons.get(t) else "") for t in tests]
    total = check.get("tests_total") or len(tests)
    shown = [f"{len(tests)} of {total} guarding tests shown."] if total > len(tests) else []
    commands = (check.get("commands") or []) if runnable else []
    run = ["", "Run:", "", "```", *commands, "```"] if commands else []
    lead = (
        "Run these before and after the change, the most direct first:"
        if runnable
        else "The tests that guard this change, the most direct first:"
    )
    return [lead, "", *rows, *shown, *run]


def _verify_section(recipe: Mapping[str, Any], *, runnable: bool = True) -> str:
    post = recipe["postconditions"]
    check = next(p for p in post if p["kind"] == "verify")
    expect = [p["text"] for p in post if p["kind"] == "metric"]
    return join_sections(
        [
            "## Verify",
            "",
            "\n".join(_verify_lines(check, runnable=runnable)),
            "\n" + bullet_list(f"Expect: {text}" for text in expect) if expect else "",
            "",
        ]
    )


def _before_section(recipe: Mapping[str, Any]) -> str:
    pre = [p["text"] for p in recipe["preconditions"]]
    if not pre:
        return ""
    return join_sections(["## Before you edit", "", bullet_list(pre), ""])


def _does_not(recipe: Mapping[str, Any]) -> list[str]:
    out = []
    for item in recipe["does_not"]:
        reason = item.get("reason")
        tail = f" {reason[0].upper()}{reason[1:]}." if reason else ""
        out.append(f"**Do not {item['constraint']}.**{tail}")
    return out


#: The heading an advisory plan's steps sit under, unnumbered.
_ADVISORY_HEADING = {"break_cycle": "Advisory: one way to cut the cycle"}


def _advisory_heading(recipe: Mapping[str, Any]) -> str:
    return _ADVISORY_HEADING.get(recipe["kind"] or "", "Advisory: one way to do it")


def _steps_section(recipe: Mapping[str, Any]) -> list[str]:
    if recipe["advisory"]:
        body = "\n".join(_step_entry(s, "-", "") for s in recipe["steps"])
        return [f"## {_advisory_heading(recipe)}", "", body, ""]
    body = "\n".join(_step_entry(s, f"{s['n']}.", "") for s in recipe["steps"])
    return ["## Steps, in order", "", body, ""]


#: Blast-radius values a code-generation prompt carries: the reach a move or a
#: class split has to keep working. Lists are cut, so one plan cannot flood it.
_BLAST_LIST_CAP = 8


def _blast_value(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, list) and value:
        items = [
            f"{v['file_path']} ({v.get('commits')})" if isinstance(v, dict) and "file_path" in v
            else str(v)
            for v in value[:_BLAST_LIST_CAP]
        ]
        more = len(value) - len(items)
        return ", ".join(items) + (f", +{more} more" if more > 0 else "")
    return None


def _blast_section(detail: Mapping[str, Any]) -> str:
    blast = detail.get("blast_radius") or {}
    rows = [
        f"{key.replace('_', ' ')}: {text}"
        for key, value in sorted(blast.items())
        if (text := _blast_value(value)) is not None
    ]
    if not rows:
        return ""
    return join_sections(["## Blast radius", "", bullet_list(rows), ""])


def _look_closer(flavor: str, call: str) -> str:
    if flavor != "claude-code-mcp":
        return ""
    return join_sections(["## Look closer (Repowise MCP)", "", f"`{call}`", ""])


def _plan_sections(
    recipe: Mapping[str, Any], repo_name: str | None, *, runnable: bool = True
) -> list[str]:
    """The plan itself, from its heading to Verify: what every reader of it gets."""
    target = recipe["target"]
    symbol = f" (`{target['symbol'].rsplit('::', 1)[-1]}`)" if target["symbol"] else ""
    facts = [f"Target: {_loc(target['file'], target['span'])}{symbol}"]
    if recipe["id"]:
        facts.insert(0, f"Plan: `{recipe['id']}`")
    return [
        f"## {label(recipe['kind'])}{repo_suffix(repo_name)}",
        "",
        recipe["summary"],
        "",
        bullet_list(facts),
        "",
        _before_section(recipe),
        *_steps_section(recipe),
        _verify_section(recipe, runnable=runnable),
    ]


def render_plan(
    detail: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None
) -> str:
    """One plan (its detail dict) as the prompt an agent starts from."""
    recipe = build_recipe(detail)
    return join_sections(
        [
            preamble(flavor, "refactoring_plan"),
            "",
            *_plan_sections(recipe, repo_name),
            _look_closer(flavor, f'get_health(plan_id="{recipe["id"]}")'),
            *closing_sections([*_does_not(recipe), *_CONSTRAINTS], _PLAN_EXPECTED),
        ]
    )


def render_plan_spec(detail: Mapping[str, Any]) -> str:
    """The plan with no harness wording: its steps, checks, reach and what it
    must not do, for a caller that frames the request itself (code generation,
    which cannot run the tests, so they are listed, not prescribed)."""
    recipe = build_recipe(detail)
    return join_sections(
        [
            *_plan_sections(recipe, None, runnable=False),
            _blast_section(detail),
            "## Hard constraints",
            "",
            bullet_list(_does_not(recipe)),
        ]
    )


# -- a composed opportunity ----------------------------------------------------


def _primary_problem(addresses: Any) -> str:
    # Tri-state: ``None`` means no dominant finding was recorded, not "no".
    if addresses is True:
        return "These steps address the file's dominant diagnosed problem."
    if addresses is False:
        return (
            "These steps do NOT address the file's dominant diagnosed problem: they are real "
            "work, but not the biggest cost in this file. Say so if something else should "
            "come first."
        )
    return (
        "No dominant problem was recorded for this file, so whether these steps address it "
        "is unknown."
    )


def _opportunity_facts(o: Mapping[str, Any]) -> str:
    steps = o.get("step_count") or 0
    recover = o.get("recoverable_health") or 0
    cause = str(o.get("lead_biomarker") or "").replace("_", " ")
    return bullet_list(
        [
            f"Opportunity: `{o['opportunity_id']}`",
            f"File: `{o['file_path']}`",
            f"Leading cause: {cause}." if cause else None,
            f"{steps} step{'' if steps == 1 else 's'}: {o.get('mechanical_steps', 0)} "
            f"mechanical, {o.get('judgment_steps', 0)} judgment.",
            f"Recovers ~{recover:.2f} of health score if applied." if recover > 0 else None,
            f"Effort: {o.get('effort_bucket')} bucket. "
            f"Detector confidence: {o.get('confidence')}.",
            _primary_problem(o.get("addresses_primary_problem")),
        ]
    )


def _applicability(step: Mapping[str, Any]) -> str:
    app = step.get("applicability") or {}
    kind = "Mechanical" if app.get("classification") == "mechanical" else "Judgment"
    reasons = "; ".join(r.replace("_", " ") for r in app.get("reasons") or [])
    unknowns = ", ".join(u.replace("_", " ") for u in app.get("unknowns") or [])
    out = f"{kind}: {reasons}." if reasons else f"{kind}."
    return f"{out} Not established: {unknowns}." if unknowns else out


def _opportunity_step(step: Mapping[str, Any], n: int, plan: Mapping[str, Any] | None) -> str:
    start = step.get("line_start")
    span = {"start": start, "end": step.get("line_end") or start} if start else None
    symbol = f" (`{step['target_symbol']}`)" if step.get("target_symbol") else ""
    kind = label(step.get("refactoring_type"))
    lines = [
        f"{n}. **{kind}** at {_loc(step['file_path'], span)}{symbol}",
        f"   - Step id: `{step['plan_id']}`",
        f"   - {_applicability(step)}",
    ]
    if step.get("relocated_by"):
        lines.append(
            f"   - **Locate it again first.** Step `{step['relocated_by']}` moves this symbol, "
            "so the path and lines above are where it was, not where it will be when you get "
            "here."
        )
    if plan is None:
        lines.append(
            f"   - This step's plan was not in this payload; ask for it by id: `{step['plan_id']}`."
        )
    else:
        recipe = build_recipe(plan)
        if recipe["advisory"]:
            lines.append(f"   - {_advisory_heading(recipe)}:")
        lines.extend(_step_entry(s, "-", "   ") for s in recipe["steps"])
    return "\n".join(lines)


def _evidence_row(item: Mapping[str, Any]) -> str:
    summary = item.get("summary") or {}
    detail = "; ".join(
        f"{k.replace('_', ' ')}: {v}" for k, v in summary.items() if v not in (None, "")
    )
    symbol = f" `{item['target_symbol']}`" if item.get("target_symbol") else ""
    return f"- {label(item.get('refactoring_type'))}{symbol}{f': {detail}' if detail else ''}"


def _opportunity_evidence(o: Mapping[str, Any]) -> str:
    rows = [_evidence_row(item) for item in o.get("evidence") or []]
    if not rows:
        return ""
    if o.get("evidence_truncated"):
        rows.append(f"{o.get('evidence_emitted')} of {o.get('evidence_total')} shown.")
    return join_sections(
        [
            "## Evidence behind the diagnosis",
            "",
            "Supporting observations, not extra work.",
            "",
            "\n".join(rows),
            "",
        ]
    )


def _profile_subjects(o: Mapping[str, Any], profile_id: Any) -> list[str]:
    """What the steps a validation profile checks change, for its heading."""
    return sorted(
        {
            s.get("target_symbol") or s["file_path"]
            for s in o.get("steps") or []
            if s.get("validation_profile_id") == profile_id
        }
    )


def _opportunity_verify(o: Mapping[str, Any]) -> str:
    profiles = o.get("validation_profiles") or []
    blocks = []
    for profile in profiles:
        subjects = _profile_subjects(o, profile.get("id"))
        heading = (
            [f"### For {', '.join(f'`{s}`' for s in subjects)}", ""]
            if len(profiles) > 1 and subjects
            else []
        )
        blocks.append("\n".join([*heading, *_verify_lines(verify_check(profile))]))
    if not blocks:
        return ""
    return join_sections(["## Verify", "", "\n\n".join(blocks), ""])


def _opportunity_handoff(o: Mapping[str, Any], flavor: str) -> str:
    oid = o["opportunity_id"]
    if flavor == "claude-code-mcp":
        return join_sections(
            [
                "## Pull this record yourself",
                "",
                f'`get_health(opportunity_id="{oid}")`',
                "",
                "It returns this opportunity from the index: every ordered step with its plan, "
                "the validation profiles and the evidence. Call it before you start; the steps "
                "above are a snapshot.",
                "",
            ]
        )
    return join_sections(
        ["## This opportunity's id", "", f"`{oid}`", "", "Quote it when you report back.", ""]
    )


_OPPORTUNITY_CONSTRAINTS = (
    "**Apply the steps in the order given.** It is dependency-safe; reordering it is not.",
    "**Read a judgment step's code before applying it.** Its obligation is unproven.",
    "**Keep behaviour.** Run the tests (and type-checker / linter) before and after.",
    *_CONSTRAINTS,
)


def render_opportunity(
    detail: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None
) -> str:
    """One composed opportunity (its detail dict, plans included) as a prompt."""
    oid = detail["opportunity_id"]
    plans = {p.get("id"): p for p in detail.get("plans") or []}
    steps = detail.get("steps") or []
    relocated = any(s.get("relocated_by") for s in steps)
    expected = [
        "1. The refactored code, with each step above applied in order.",
        "2. Per step: whether you applied it, and for anything you skipped, the reason.",
        "3. The Verify run, before and after, with its result.",
        f"4. The opportunity id `{oid}`, so the work can be matched back to the record.",
    ]
    heading = f"{label(detail.get('lead_refactoring_type'))} in `{detail['file_path']}`"
    return join_sections(
        [
            preamble(flavor, "refactoring_opportunity"),
            "",
            f"## {heading}{repo_suffix(repo_name)}",
            "",
            _opportunity_facts(detail),
            "",
            "## Steps, in order",
            "",
            f"{detail.get('ordering_note') or ''}\n" if relocated else "",
            "\n\n".join(
                _opportunity_step(s, n, plans.get(s.get("plan_id"))) for n, s in enumerate(steps, 1)
            ),
            "",
            _opportunity_evidence(detail),
            _opportunity_verify(detail),
            *closing_sections(_OPPORTUNITY_CONSTRAINTS, expected),
            _opportunity_handoff(detail, flavor),
        ]
    )


__all__ = ["render_opportunity", "render_plan", "render_plan_spec"]
