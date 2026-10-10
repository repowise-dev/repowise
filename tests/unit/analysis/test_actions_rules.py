"""Next-action rules over synthetic facts: when each fires, when it stays quiet,
and what the engine does with the result. No store; the loader has its own
tests.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from repowise.core.analysis.actions import ActionStateRecord, RepoFacts, compose_actions
from repowise.core.analysis.actions.context import build_context
from repowise.core.analysis.actions.engine import KEEP_PER_HORIZON, rank_actions
from repowise.core.analysis.actions.facts import (
    CoverageState,
    DeadFacts,
    DecisionFacts,
    DriftFacts,
    FileFacts,
    LeadFinding,
    RecentFinding,
    SecretFacts,
)
from repowise.core.analysis.actions.model import SEVERITY_VALUE, TIER_RANK, Action
from repowise.core.analysis.actions.rules import code, hygiene, signal

ANCHOR = datetime(2026, 9, 28, 12, 0)
NOW = ANCHOR + timedelta(days=1)


def _file(path: str, **kw) -> FileFacts:
    base = dict(
        path=path,
        is_test=False,
        score=5.0,
        max_ccn=10,
        nloc=200,
        line_coverage_pct=None,
        commits_90d=10,
        last_commit_at=ANCHOR - timedelta(days=30),
        bug_magnet=True,
        fix_commits_90d=5,
        bus_factor=1,
        owner_key=None,
        owner_name=None,
        owner_pct=None,
        dependents=None,
        tests_reaching=0,
    )
    base.update(kw)
    return FileFacts(**base)


def _facts(files: list[FileFacts] = (), **kw) -> RepoFacts:
    kw.setdefault("fix_commits_90d", sum(f.fix_commits_90d for f in files))
    return RepoFacts(anchor=kw.pop("anchor", ANCHOR), files={f.path: f for f in files}, **kw)


def _run(rule, facts: RepoFacts, **ctx_overrides):
    ctx = build_context(facts)
    if ctx_overrides:
        ctx = replace(ctx, **ctx_overrides)
    return rule(facts, ctx)


def _text(a: Action) -> str:
    return " ".join([a.title, a.impact, a.done_when, *(w.value for w in a.why)]).lower()


# ---------------------------------------------------------------------------
# fragile_file
# ---------------------------------------------------------------------------


def test_fragile_without_coverage_says_add_tests_and_basis_unknown() -> None:
    out = _run(code.fragile_file, _facts([_file("src/a.py")]))
    (a,) = out.actions
    assert a.title.startswith("Add tests around `src/a.py`")
    cov = next(w for w in a.why if w.label == "line coverage")
    assert cov.basis == "unknown"
    assert a.confidence == "medium"
    # Unknown is not zero: nothing may call it untested.
    assert "untested" not in _text(a)


def test_fragile_below_80_says_raise_coverage() -> None:
    (a,) = _run(code.fragile_file, _facts([_file("src/a.py", line_coverage_pct=62.0)])).actions
    assert a.title == "Raise test coverage on `src/a.py` from 62%"
    assert next(w for w in a.why if w.label == "line coverage").basis == "measured"


def test_fragile_covered_with_lead_says_simplify() -> None:
    lead = LeadFinding("complex_method", "high", "build", 12, "ccn 20")
    f = _file("src/a.py", line_coverage_pct=91.0, lead=lead)
    (a,) = _run(code.fragile_file, _facts([f])).actions
    assert a.title.startswith("Simplify `build` in `src/a.py`")
    assert a.target_symbol == "build"
    assert a.marker == "complex_method"


def test_fragile_covered_without_lead_is_silent() -> None:
    f = _file("src/a.py", line_coverage_pct=91.0)
    assert _run(code.fragile_file, _facts([f])).actions == ()


def test_fragile_skips_declaration_only_files() -> None:
    f = _file("src/types.ts", max_ccn=1)
    out = _run(code.fragile_file, _facts([f]))
    assert out.status == "evaluated"
    assert out.actions == ()


def test_fragile_respects_repo_fix_threshold() -> None:
    # Two files with fixes: the 95th percentile is the larger count, so the
    # file with 3 fixes sits below the bar the repository sets.
    files = [_file("src/hot.py", fix_commits_90d=10), _file("src/warm.py", fix_commits_90d=3)]
    out = _run(code.fragile_file, _facts(files))
    assert [a.target_path for a in out.actions] == ["src/hot.py"]


def test_fragile_not_applicable_without_fixes() -> None:
    out = _run(code.fragile_file, _facts([_file("src/a.py", fix_commits_90d=0)]))
    assert out.status == "not_applicable"


def test_fragile_week_horizon_only_for_top_five_touched_this_week() -> None:
    this_week = ANCHOR - timedelta(days=1)
    files = [_file(f"src/f{n}.py", fix_commits_90d=n, last_commit_at=this_week) for n in range(3, 10)]
    files.append(_file("src/quiet.py", fix_commits_90d=20, last_commit_at=ANCHOR - timedelta(days=30)))
    out = _run(code.fragile_file, _facts(files), busy_threshold=5, fix_threshold=3)
    by_path = {a.target_path: a.horizons for a in out.actions}
    assert {p for p, h in by_path.items() if "week" in h} == {f"src/f{n}.py" for n in range(5, 10)}
    assert by_path["src/f3.py"] == ("quarter",)
    assert by_path["src/f4.py"] == ("quarter",)
    assert by_path["src/quiet.py"] == ("quarter",)


# ---------------------------------------------------------------------------
# fresh_regressions
# ---------------------------------------------------------------------------


def _recent(path: str, symbol: str | None = "fn", severity: str = "high", sha: str = "c1") -> RecentFinding:
    return RecentFinding(
        sha=sha,
        subject="feat: thing",
        committed_at=ANCHOR - timedelta(days=1),
        file_path=path,
        symbol=symbol,
        biomarker="complex_method",
        severity=severity,
        change_kind="introduced",
        line=10,
    )


def test_regressions_up_to_three_files_are_per_file() -> None:
    found = (_recent("src/a.py", "build"), _recent("src/b.py", "x"), _recent("src/b.py", "y"))
    out = _run(code.fresh_regressions, _facts(recent_findings=found))
    assert {a.target_path for a in out.actions} == {"src/a.py", "src/b.py"}
    a = next(a for a in out.actions if a.target_path == "src/a.py")
    assert a.tier == "act_now"
    assert a.horizons == ("week",)
    assert a.target_kind == "symbol"
    assert a.title == "Simplify `build` in `src/a.py` while the change is fresh"


def test_regressions_over_three_files_roll_up() -> None:
    found = (
        *(_recent(f"src/f{i}.py") for i in range(4)),
        _recent("src/worst.py", severity="critical"),
    )
    out = _run(code.fresh_regressions, _facts(recent_findings=found))
    (a,) = out.actions
    assert a.target_kind == "repo"
    assert a.severity == "critical"
    assert a.includes[0] == "src/worst.py"
    assert set(a.includes) == {"src/worst.py", *(f"src/f{i}.py" for i in range(4))}


def test_regressions_unavailable_store() -> None:
    out = _run(code.fresh_regressions, _facts(unavailable={"commit_health": "missing"}))
    assert out.status == "unavailable"
    assert out.reason == "missing"


# ---------------------------------------------------------------------------
# fix_concentration
# ---------------------------------------------------------------------------


def _concentration_facts() -> RepoFacts:
    """10 of 25 fixes in 4 of 100 files under `pkg/cli/update/` (lift 10); its
    ancestor `pkg/cli/` also clears the bar (lift 5.2) but is broader."""
    update_shas = [
        {"u0", "u1", "u2", "u3"},
        {"u3", "u4", "u5", "u6"},
        {"u6", "u7", "u8", "u9"},
        {"u0", "u2", "u5", "u9"},
    ]
    files: list[FileFacts] = []
    fix_shas: dict[str, frozenset[str]] = {}
    for i, shas in enumerate(update_shas):
        p = f"pkg/cli/update/a{i}.py"
        files.append(_file(p, fix_commits_90d=len(shas)))
        fix_shas[p] = frozenset(shas)
    for i in range(6):
        p = f"pkg/cli/misc/b{i}.py"
        fixes = 3 if i < 2 else 0
        files.append(_file(p, fix_commits_90d=fixes, bug_magnet=False))
        if fixes:
            fix_shas[p] = frozenset({"m0", "m1", "m2"})
    for i in range(90):
        p = f"lib/l{i}.py"
        fixes = 1 if i < 12 else 0
        files.append(_file(p, fix_commits_90d=fixes, commits_90d=1, bug_magnet=False))
        if fixes:
            fix_shas[p] = frozenset({f"l{i}"})
    return _facts(files, fix_shas_by_file=fix_shas, fix_commits_90d=25)


def test_concentration_needs_twenty_fixes() -> None:
    out = _run(code.fix_concentration, _facts([_file("src/a.py")], fix_commits_90d=19))
    assert out.status == "not_applicable"


def test_concentration_picks_the_narrow_high_lift_folder() -> None:
    out = _run(code.fix_concentration, _concentration_facts())
    (a,) = out.actions
    assert a.target_path == "pkg/cli/update"
    assert a.target_kind == "folder"
    assert set(a.includes) == {f"pkg/cli/update/a{i}.py" for i in range(4)}


def test_engine_drops_fragile_rows_a_folder_absorbed() -> None:
    facts = _concentration_facts()
    # Sanity: on their own, the folder's files are fragile.
    fragile = _run(code.fragile_file, facts).actions
    assert {a.target_path for a in fragile} >= {f"pkg/cli/update/a{i}.py" for i in range(4)}

    view = compose_actions(facts, now=NOW)
    quarter = view["horizons"]["quarter"]["actions"]
    assert not any(
        a["rule"] == "fragile_file" and a["target"]["path"].startswith("pkg/cli/update/")
        for a in quarter
    )
    folder = next(a for a in quarter if a["rule"] == "fix_concentration")
    assert len(folder["includes"]) == 4


# ---------------------------------------------------------------------------
# fix_first
# ---------------------------------------------------------------------------


def _fix_items():
    """The shared Fix-first fixture's queue: one now, one next, one later."""
    from repowise.core.analysis.health.fix_first import build_fix_first
    from tests.unit.health import fix_first_rows as rows

    return build_fix_first(
        metrics=rows.METRICS,
        findings=rows.FINDINGS,
        refactoring=rows.REFACTORING,
        performance=rows.PERFORMANCE,
        plans=rows.PLANS,
        limit=3,
    ).items


def test_fix_first_emits_now_and_next_and_leaves_later() -> None:
    items = _fix_items()
    # The growing, reachable database loop leads; it needs judgment.
    assert [i.tier for i in items] == ["next", "now", "later"]
    actions = _run(code.fix_first, _facts(fix_first=items)).actions
    assert [(a.tier, a.title) for a in actions] == [
        ("plan", items[0].title),
        ("act_now", items[1].title),
    ]
    # Both are due, so both are this week's as well as this quarter's.
    assert actions[1].horizons == actions[0].horizons == ("week", "quarter")
    assert [a.value for a in actions] == [i.value for i in items[:2]]
    assert actions[0].surface == "performance"
    assert actions[1].commands[0].mcp == f'get_health(fix_id="{items[1].id}")'
    assert len({a.action_id for a in actions}) == 2


def test_fix_first_unavailable_is_named() -> None:
    outcome = _run(code.fix_first, _facts(unavailable={"fix_first": "Not here."}))
    assert (outcome.status, outcome.reason) == ("unavailable", "Not here.")


def test_fragile_file_does_not_repeat_a_file_fix_first_names() -> None:
    fragile = _file("src/core.py", fix_commits_90d=9, commits_90d=20)
    ctx = dict(busy_threshold=5, fix_threshold=3)
    assert _run(code.fragile_file, _facts([fragile]), **ctx).actions
    named = _facts([fragile], fix_first=_fix_items())
    assert _run(code.fragile_file, named, **ctx).actions == ()


# ---------------------------------------------------------------------------
# live_secret
# ---------------------------------------------------------------------------


def test_secret_in_code_is_act_now_critical() -> None:
    s = SecretFacts("src/config.py", "hardcoded_secret", 4, 'API_KEY = "sk_live_abc"')
    (a,) = _run(hygiene.live_secret, _facts(secrets=(s,))).actions
    assert (a.tier, a.severity) == ("act_now", "critical")
    assert a.title == "Rotate the hard-coded secret in `src/config.py`"


def test_public_env_secret_is_plan() -> None:
    s = SecretFacts("web/env.ts", "public_env_secret", 2, "NEXT_PUBLIC_SECRET = process.env.X")
    (a,) = _run(hygiene.live_secret, _facts(secrets=(s,))).actions
    assert (a.tier, a.severity) == ("plan", "high")


def test_secret_ignores_docs_comments_and_quotes() -> None:
    quiet = (
        SecretFacts("README.md", "hardcoded_secret", 3, 'API_KEY = "abc"'),
        SecretFacts("docs/setup.py", "hardcoded_secret", 3, 'API_KEY = "abc"'),
        SecretFacts("src/a.py", "hardcoded_password", 3, '# password = "hunter2"'),
        SecretFacts("src/b.py", "hardcoded_password", 3, '"password": "example"'),
        SecretFacts("src/c.py", "os_system", 3, "os.system(cmd)"),
    )
    assert _run(hygiene.live_secret, _facts(secrets=quiet)).actions == ()


# ---------------------------------------------------------------------------
# broken_doc_refs
# ---------------------------------------------------------------------------


def _drift(**kw) -> DriftFacts:
    base = dict(
        document="docs/guide.md",
        kind="path",
        target="src/old.py",
        raw="src/old.py",
        line=3,
        reason="gone",
        confidence=0.9,
        target_known=True,
    )
    base.update(kw)
    return DriftFacts(**base)


def test_doc_path_ref_needs_a_known_target() -> None:
    assert _run(hygiene.broken_doc_refs, _facts(drift=(_drift(target_known=False),))).actions == ()
    (a,) = _run(hygiene.broken_doc_refs, _facts(drift=(_drift(),))).actions
    assert a.title == "Fix 1 broken reference in `docs/guide.md`"


def test_doc_anchor_ignores_site_root_links() -> None:
    site = _drift(kind="anchor", raw="/#install", target="docs/guide.md#install")
    local = _drift(kind="anchor", raw="#usage", target="docs/guide.md#usage")
    (a,) = _run(hygiene.broken_doc_refs, _facts(drift=(site, local))).actions
    assert a.why[1].value == "docs/guide.md#usage"


def test_doc_low_confidence_ignored() -> None:
    assert _run(hygiene.broken_doc_refs, _facts(drift=(_drift(confidence=0.5),))).actions == ()


# ---------------------------------------------------------------------------
# dead_code_batch
# ---------------------------------------------------------------------------


def test_dead_code_below_floor_is_silent() -> None:
    dead = (DeadFacts("d1", "src/a.py", "f", 10), DeadFacts("d2", "src/a.py", "g", 10))
    out = _run(hygiene.dead_code_batch, _facts(dead=dead))
    assert out.status == "evaluated"
    assert out.actions == ()


def test_dead_code_excludes_standalone_dirs() -> None:
    standalone = tuple(
        DeadFacts(f"s{i}", path, "f", 50)
        for i, path in enumerate(("docs_src/tut.py", "examples/demo.py", "scripts/run.py"))
    )
    assert _run(hygiene.dead_code_batch, _facts(dead=standalone)).actions == ()
    real = tuple(DeadFacts(f"d{i}", "src/a.py", f"f{i}", 5) for i in range(3))
    (a,) = _run(hygiene.dead_code_batch, _facts(dead=standalone + real)).actions
    assert a.evidence_total == 3
    assert a.title == "Delete 3 unused symbols and files (15 lines)"


# ---------------------------------------------------------------------------
# knowledge_loss, stale_decision
# ---------------------------------------------------------------------------


def _owned() -> FileFacts:
    return _file("src/a.py", owner_key="k", owner_name="Ada", owner_pct=0.9)


def test_knowledge_loss_needs_a_team() -> None:
    facts = _facts(
        [_owned()],
        active_authors_90d=2,
        author_last_commit={"k": ANCHOR - timedelta(days=100)},
    )
    assert _run(hygiene.knowledge_loss, facts).status == "not_applicable"


def test_knowledge_loss_fires_for_a_quiet_owner() -> None:
    facts = _facts(
        [_owned()],
        active_authors_90d=3,
        author_last_commit={"k": ANCHOR - timedelta(days=100)},
    )
    (a,) = _run(hygiene.knowledge_loss, facts).actions
    assert a.title == "Share what Ada knew about `src/a.py`"


def test_stale_decision_needs_accepted_decisions() -> None:
    d = (DecisionFacts("dec1", "Use SQLite"),)
    assert _run(hygiene.stale_decision, _facts(stale_decisions=d)).status == "not_applicable"
    (a,) = _run(hygiene.stale_decision, _facts(stale_decisions=d, accepted_decisions=1)).actions
    assert a.tier == "plan"


# ---------------------------------------------------------------------------
# improve_signal rules
# ---------------------------------------------------------------------------


def _many_files(n: int = 25) -> list[FileFacts]:
    return [_file(f"src/f{i}.py", fix_commits_90d=0, bug_magnet=False) for i in range(n)]


def test_coverage_missing_is_improve_signal() -> None:
    (a,) = _run(signal.coverage_missing, _facts(_many_files())).actions
    assert a.tier == "improve_signal"
    assert _run(signal.coverage_missing, _facts(_many_files(5))).status == "not_applicable"


def test_coverage_stale_is_improve_signal() -> None:
    cov = CoverageState("stale", ANCHOR - timedelta(days=30), 40)
    (a,) = _run(signal.coverage_stale, _facts(coverage=cov)).actions
    assert a.tier == "improve_signal"
    recent = CoverageState("stale", ANCHOR - timedelta(days=3), 40)
    assert _run(signal.coverage_stale, _facts(coverage=recent)).actions == ()


def test_decisions_unreviewed_is_improve_signal() -> None:
    (a,) = _run(signal.decisions_unreviewed, _facts(proposed_decisions=6)).actions
    assert a.tier == "improve_signal"
    assert (
        _run(signal.decisions_unreviewed, _facts(proposed_decisions=6, accepted_decisions=1)).actions
        == ()
    )


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def _docs(n: int) -> tuple[DriftFacts, ...]:
    return tuple(_drift(document=f"docs/d{i}.md", target=f"src/gone{i}.py") for i in range(n))


def _mixed() -> RepoFacts:
    return _facts(
        _many_files(),
        secrets=(SecretFacts("src/config.py", "hardcoded_secret", 4, 'KEY = "sk_live_a1b2c3"'),),
        drift=_docs(4),
        dead=tuple(DeadFacts(f"d{i}", "src/a.py", f"f{i}", 20) for i in range(3)),
    )


def test_engine_orders_by_tier() -> None:
    quarter = compose_actions(_mixed(), now=NOW)["horizons"]["quarter"]["actions"]
    ranks = [TIER_RANK[a["tier"]] for a in quarter]
    assert ranks == sorted(ranks)
    assert quarter[0]["rule"] == "live_secret"
    assert quarter[-1]["tier"] == "improve_signal"


def test_engine_caps_each_rule_in_the_tier_head() -> None:
    quarter = compose_actions(_mixed(), now=NOW)["horizons"]["quarter"]["actions"]
    plan = [a["rule"] for a in quarter if a["tier"] == "plan"]
    assert plan == [
        "broken_doc_refs",
        "broken_doc_refs",
        "dead_code_batch",
        "broken_doc_refs",
        "broken_doc_refs",
    ]


def test_engine_reports_totals_and_every_rule() -> None:
    view = compose_actions(_mixed(), now=NOW)
    quarter = view["horizons"]["quarter"]
    assert quarter["total"] == len(quarter["actions"]) == 7
    assert quarter["hidden"] == 0
    assert quarter["by_tier"] == {"act_now": 1, "plan": 5, "improve_signal": 1}
    assert len(view["rules"]) == 12
    # The week sees the secret and the coverage prompt, not the quarter's plan work.
    assert view["horizons"]["week"]["total"] == 2


def test_engine_keeps_the_full_total_past_the_cap() -> None:
    view = compose_actions(_facts(drift=_docs(KEEP_PER_HORIZON + 5)), now=NOW)
    quarter = view["horizons"]["quarter"]
    assert len(quarter["actions"]) == KEEP_PER_HORIZON
    assert quarter["total"] == KEEP_PER_HORIZON + 5


def _ruled(*actions: Action) -> dict:
    return {
        "anchor": None, "week_start": None, "context": {}, "rules": [],
        "actions": [(a.weight, a.as_dict()) for a in actions],
    }


def _act(rule: str, tier: str, path: str, **kw) -> Action:
    base = dict(
        rule=rule, tier=tier, horizons=("week", "quarter"), severity="high", title=path,
        impact="", why=(), target_kind="file", target_path=path, surface="file",
        effort="M", confidence="medium", done_when="",
    )
    return Action(**{**base, **kw})


def test_engine_ranks_a_tier_by_value_confidence_and_effort() -> None:
    # Rule order alone put the folder first; per unit of effort the Fix first
    # item (value 4, M) and a one-line doc fix (low, S, high) are worth more.
    ruled = _ruled(
        _act("fix_concentration", "plan", "src/", effort="L"),
        _act("fix_first", "plan", "src/big.py", value=4),
        _act("broken_doc_refs", "plan", "docs/a.md", severity="low", effort="S",
             confidence="high"),
    )
    quarter = rank_actions(ruled, now=NOW)["horizons"]["quarter"]["actions"]
    assert [a["rule"] for a in quarter] == ["fix_first", "broken_doc_refs", "fix_concentration"]
    assert [a["value"] for a in quarter] == [4, 1, 3]


def test_the_fix_first_lead_leads_its_tier_and_never_crosses_one() -> None:
    secrets = [_act("live_secret", "act_now", f"src/s{i}.py", effort="S") for i in range(3)]
    fragile = _act("fragile_file", "plan", "src/hot.py", confidence="high")  # priority 1.5
    lead = _act("fix_first", "plan", "src/big.py", value=4, weight=3.0, priority=1.0)
    second = _act("fix_first", "plan", "src/other.py", value=4, weight=2.0, priority=1.0)
    view = rank_actions(_ruled(*secrets, fragile, second, lead), now=NOW)
    for horizon in ("week", "quarter"):
        actions = view["horizons"][horizon]["actions"]
        # Every act_now row stays above it; the lead opens the plan block.
        assert [a["tier"] for a in actions[:3]] == ["act_now"] * 3
        assert [a["target"]["path"] for a in actions[3:]] == [
            "src/big.py", "src/hot.py", "src/other.py",
        ]


def test_fix_first_actions_keep_the_queue_order() -> None:
    items = _fix_items()
    actions = _run(code.fix_first, _facts(fix_first=items)).actions
    priorities = [a.effective_priority for a in actions]
    # A later item never outranks an earlier one, whatever its effort.
    assert priorities == sorted(priorities, reverse=True)
    assert [a["priority"] for a in (x.as_dict() for x in actions)] == [
        round(p, 4) for p in priorities
    ]


def test_an_older_fix_first_snapshot_ranks_on_severity() -> None:
    from repowise.core.analysis.health.fix_first import FixFirstQueue
    from repowise.core.persistence.read_snapshots import decode_or_none

    stored = FixFirstQueue(items=_fix_items()).as_dict()
    for item in stored["items"]:
        del item["value"]
    old = decode_or_none(FixFirstQueue, stored)
    assert old is not None and all(i.value is None for i in old.items)
    actions = _run(code.fix_first, _facts(fix_first=old.items)).actions
    assert [a.effective_value for a in actions] == [
        SEVERITY_VALUE[a.severity] for a in actions
    ]


def test_a_stored_action_without_a_value_ranks_on_its_severity() -> None:
    old = _act("stale_decision", "plan", "docs/d.md", severity="critical").as_dict()
    del old["value"], old["priority"]
    new = _act("fix_concentration", "plan", "src/").as_dict()
    ruled = {**_ruled(), "actions": [(0.0, new), (0.0, old)]}
    quarter = rank_actions(ruled, now=NOW)["horizons"]["quarter"]["actions"]
    assert [a["rule"] for a in quarter] == ["stale_decision", "fix_concentration"]


def _secret_action(view) -> dict:
    return next(a for a in view["horizons"]["quarter"]["actions"] if a["rule"] == "live_secret")


def test_dismissal_holds_only_while_the_fingerprint_matches() -> None:
    facts = _mixed()
    action = _secret_action(compose_actions(facts, now=NOW))
    same = {action["id"]: ActionStateRecord("dismissed", action["fingerprint"])}
    view = compose_actions(facts, same, now=NOW)
    assert not any(a["id"] == action["id"] for a in view["horizons"]["quarter"]["actions"])
    assert view["horizons"]["quarter"]["hidden"] == 1
    assert view["horizons"]["quarter"]["total"] == 6

    changed = {action["id"]: ActionStateRecord("dismissed", "stale-fingerprint")}
    view = compose_actions(facts, changed, now=NOW)
    assert _secret_action(view)["id"] == action["id"]
    assert view["horizons"]["quarter"]["hidden"] == 0


def test_snooze_hides_until_it_expires() -> None:
    facts = _mixed()
    action = _secret_action(compose_actions(facts, now=NOW))
    later = {action["id"]: ActionStateRecord("snoozed", "", NOW + timedelta(days=3))}
    view = compose_actions(facts, later, now=NOW)
    assert view["horizons"]["quarter"]["hidden"] == 1
    expired = {action["id"]: ActionStateRecord("snoozed", "", NOW - timedelta(days=1))}
    assert _secret_action(compose_actions(facts, expired, now=NOW))


def test_action_id_is_stable_for_rule_and_target() -> None:
    (a,) = _run(hygiene.broken_doc_refs, _facts(drift=(_drift(),))).actions
    (b,) = _run(
        hygiene.broken_doc_refs, _facts(drift=(_drift(target="src/other.py"), _drift()))
    ).actions
    assert a.action_id == b.action_id
    assert a.title != b.title
    (c,) = _run(hygiene.broken_doc_refs, _facts(drift=(_drift(document="docs/x.md"),))).actions
    assert c.action_id != a.action_id
    assert a.action_id.startswith("act_")


# ---------------------------------------------------------------------------
# Review regressions
# ---------------------------------------------------------------------------


def test_week_rollup_does_not_hide_a_fragile_file_from_the_quarter() -> None:
    fragile = _file("src/f0.py", fix_commits_90d=9, commits_90d=20)
    found = tuple(_recent(f"src/f{i}.py") for i in range(4))
    view = compose_actions(_facts([fragile], recent_findings=found), now=ANCHOR)
    quarter = [a["target"]["path"] for a in view["horizons"]["quarter"]["actions"]]
    assert "src/f0.py" in quarter


def test_fragile_id_survives_a_change_of_lead_function() -> None:
    lead = LeadFinding("complex_method", "high", "a", 1, "")
    before = _file("src/x.py", fix_commits_90d=9, commits_90d=20, line_coverage_pct=90.0, lead=lead)
    after = replace(before, lead=replace(lead, function="b"))
    ctx = dict(busy_threshold=5, fix_threshold=3)
    (a,) = _run(code.fragile_file, _facts([before]), **ctx).actions
    (b,) = _run(code.fragile_file, _facts([after]), **ctx).actions
    assert a.action_id == b.action_id


def test_secret_ignores_placeholder_examples_in_code() -> None:
    examples = (
        SecretFacts("src/embed.py", "hardcoded_secret", 15, 'embedder = Embedder(api_key="AIza...")'),
        SecretFacts("src/p.py", "hardcoded_secret", 12, 'p = get("openai", api_key="sk-...", model="gpt")'),
        SecretFacts("src/q.py", "hardcoded_secret", 3, 'token = "your-token-here"'),
    )
    assert _run(hygiene.live_secret, _facts(secrets=examples)).actions == ()
    real = SecretFacts("src/r.py", "hardcoded_secret", 3, 'API_KEY = "sk-live-a****"')
    assert len(_run(hygiene.live_secret, _facts(secrets=(real,))).actions) == 1


def test_secret_ignores_a_dummy_word_value() -> None:
    dummy = SecretFacts("src/ollama.py", "hardcoded_secret", 3, 'OpenAI(api_key="ollama", base_url=u)')
    assert _run(hygiene.live_secret, _facts(secrets=(dummy,))).actions == ()


def test_rollup_carries_evidence_starting_with_the_worst_file_and_commands() -> None:
    found = (
        *(_recent(f"src/f{i}.py", sha=f"s{i}") for i in range(4)),
        _recent("src/worst.py", severity="critical", sha="w1"),
    )
    (a,) = _run(code.fresh_regressions, _facts(recent_findings=found)).actions
    assert a.details[0].path == "src/worst.py" == a.includes[0]
    assert a.details_total == 5
    mcp = [c.mcp for c in a.commands]
    assert any(m and m.startswith('get_health(targets=["src/worst.py"') for m in mcp)
    assert 'get_change_risk(revspec="w1")' in mcp


# ---------------------------------------------------------------------------
# Self-checks: "add tests" needs no test reaching the file; short history
# ---------------------------------------------------------------------------

_LEAD = LeadFinding("complex_method", "high", "runEmbeddedAttempt", 40, "CCN 90")


def test_fragile_reached_by_tests_says_simplify_not_add_tests() -> None:
    """openclaw's attempt.ts: no coverage report, but tests reach it in the graph."""
    path = "src/agents/embedded-agent-runner/run/attempt.ts"
    f = _file(path, lead=_LEAD, tests_reaching=36, tests_reaching_via="import-graph")
    (a,) = _run(code.fragile_file, _facts([f])).actions
    assert not a.title.startswith("Add tests")
    assert a.title == (
        f"Simplify `runEmbeddedAttempt` in `{path}`: tests reach it, and fixes keep landing"
    )
    reach = next(w for w in a.why if w.label == "tests that reach it")
    assert (reach.value, reach.basis) == ("36 test files, import graph", "inferred")
    assert a.confidence == "medium"
    assert a.target_symbol == "runEmbeddedAttempt"


def test_fragile_reached_by_tests_without_a_lead_is_silent() -> None:
    out = _run(code.fragile_file, _facts([_file("src/a.py", tests_reaching=2)]))
    assert out.actions == ()


def test_fragile_no_test_reaches_says_add_tests() -> None:
    (a,) = _run(code.fragile_file, _facts([_file("src/a.py", tests_reaching=0)])).actions
    assert a.title.startswith("Add tests around `src/a.py`")
    reach = next(w for w in a.why if w.label == "tests that reach it")
    assert (reach.value, reach.basis) == ("None in the code graph", "inferred")


def test_fragile_unknown_reach_is_not_zero() -> None:
    """A failed test-map walk claims neither "add tests" nor "tests reach it"."""
    lead = LeadFinding("complex_method", "high", "run", 10, "")
    unknown = _file("src/a.py", lead=lead, tests_reaching=None)
    facts = _facts([unknown], unavailable={"test_map": "Not in this index yet."})
    assert _run(code.fragile_file, facts).actions == ()
    (zero,) = _run(code.fragile_file, _facts([replace(unknown, tests_reaching=0)])).actions
    assert zero.title.startswith("Add tests around")


def test_an_anonymous_lead_function_is_left_out_of_the_title() -> None:
    lead = LeadFinding("complex_method", "high", "<anonymous@3167>", 3167, "")
    f = _file("src/gateway/chat.ts", lead=lead, tests_reaching=3)
    (a,) = _run(code.fragile_file, _facts([f])).actions
    assert a.title == "Simplify `src/gateway/chat.ts`: tests reach it, and fixes keep landing"
    assert a.target_symbol is None


def test_measured_coverage_wins_over_the_test_map() -> None:
    """Ours: update_cmd/command.py (68 changes, 27 fixes, 70%) keeps "raise coverage",
    and call_resolver.py keeps "simplify _csharp_type_id", whatever reaches them."""
    raise_path = "packages/cli/src/repowise/cli/commands/update_cmd/command.py"
    tested_path = "packages/core/src/repowise/core/ingestion/call_resolver.py"
    lead = LeadFinding("complex_method", "high", "_csharp_type_id", 10, "")
    files = [
        _file(raise_path, commits_90d=68, fix_commits_90d=27, line_coverage_pct=70.0,
              tests_reaching=40),
        _file(tested_path, commits_90d=68, fix_commits_90d=27, line_coverage_pct=91.0,
              lead=lead, tests_reaching=40),
    ]
    titles = {a.target_path: a.title for a in _run(code.fragile_file, _facts(files)).actions}
    assert titles[raise_path] == f"Raise test coverage on `{raise_path}` from 70%"
    assert titles[tested_path] == (
        f"Simplify `_csharp_type_id` in `{tested_path}`: it is tested, and fixes keep landing"
    )


def test_one_commit_history_stands_history_rules_down_and_flags_the_view() -> None:
    """hermes-dogfood: a one-commit copy has churn and fix counts that describe the copy."""
    files = [_file(f"src/m{i}/a{j}.py", commits_90d=30, fix_commits_90d=10)
             for i in range(2) for j in range(4)]
    facts = _facts(files, history_commits=1, active_authors_90d=5)
    for rule in (code.fragile_file, code.fix_concentration, hygiene.knowledge_loss):
        out = _run(rule, facts)
        assert (out.status, out.actions) == ("not_applicable", ())
        assert "too few commits" in out.reason
    view = compose_actions(facts, now=NOW)
    assert view["context"]["history_too_short"] is True


def test_history_at_the_busy_floor_or_uncounted_is_not_flagged() -> None:
    files = [_file("src/a.py")]
    for commits in (5, None):
        facts = _facts(files, history_commits=commits)
        assert build_context(facts).history_too_short is False
        assert _run(code.fragile_file, facts).status == "evaluated"
