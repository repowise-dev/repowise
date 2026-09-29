"""Next-action rules over synthetic facts: when each fires, when it stays quiet,
and what the engine does with the result. No store; the loader has its own
tests.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from repowise.core.analysis.actions import ActionStateRecord, RepoFacts, compose_actions
from repowise.core.analysis.actions.context import build_context
from repowise.core.analysis.actions.engine import KEEP_PER_HORIZON
from repowise.core.analysis.actions.facts import (
    CoverageState,
    DeadFacts,
    DecisionFacts,
    DriftFacts,
    FileFacts,
    LeadFinding,
    PerfFacts,
    RecentFinding,
    SecretFacts,
)
from repowise.core.analysis.actions.model import TIER_RANK, Action
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
# hot_path_perf
# ---------------------------------------------------------------------------


def _perf(**kw) -> PerfFacts:
    base = dict(
        opportunity_id="op1",
        biomarker="n_plus_one",
        boundary="db",
        file_path="src/repo.py",
        symbol="src/repo.py::Repo.load",
        call_sites=1,
        files=1,
        actionability="plan_ready",
        exposure="entry_reachable",
        loop_magnitude="grows_with_data",
        effort="S",
    )
    base.update(kw)
    return PerfFacts(**base)


def test_perf_fires_when_reachable_and_growing() -> None:
    (a,) = _run(code.hot_path_perf, _facts(perf=(_perf(),))).actions
    assert a.title == "Move the database call in `Repo.load` out of its loop"
    assert a.target_symbol == "src/repo.py::Repo.load"
    assert a.confidence == "high"


def test_perf_fires_on_three_call_sites_without_growth() -> None:
    (a,) = _run(
        code.hot_path_perf,
        _facts(perf=(_perf(loop_magnitude=None, call_sites=3, actionability="advisory"),)),
    ).actions
    assert a.title == "Batch the database calls loops make through `Repo.load`"
    assert a.confidence == "medium"


def test_perf_gates() -> None:
    quiet = (
        _perf(loop_magnitude=None, call_sites=2),
        _perf(exposure="internal"),
        _perf(exposure=None),
        _perf(actionability="investigate"),
    )
    for p in quiet:
        assert _run(code.hot_path_perf, _facts(perf=(p,))).actions == (), p


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
    assert a.title == "Delete 3 unused symbols (15 lines)"


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
        secrets=(SecretFacts("src/config.py", "hardcoded_secret", 4, 'KEY = "x"'),),
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
