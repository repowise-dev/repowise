"""The generate summary accounts for every planned page and prints the cost."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from repowise.cli.commands.generate_cmd import command
from repowise.cli.commands.generate_cmd.engine import GenerateOutcome
from repowise.core.generation.models import STUB_FALLBACK_ERROR


def _page(pid: str, **meta: object) -> SimpleNamespace:
    return SimpleNamespace(page_id=pid, metadata=dict(meta))


def _render(outcome: GenerateOutcome) -> str:
    rec = Console(record=True, width=300)
    with patch.object(command, "console", rec):
        command._report_outcome(outcome, 12.0)
    return rec.export_text()


def _outcome(**kw: object) -> GenerateOutcome:
    planned = {f"module_page:m{i}" for i in range(10)}
    base = dict(
        generated_pages=[
            _page("module_page:m0"),
            _page("module_page:m1"),
            _page("module_page:m2", reused_from_prior_run=True),
            _page("module_page:m3", **{STUB_FALLBACK_ERROR: "boom"}),
        ],
        total_pages=40,
        marked_stale=0,
        remaining_template_pages=1,
        plan=SimpleNamespace(generate_ids=planned),
        swept_page_ids=["module_page:m4", "module_page:m5", "module_page:m6", "module_page:gone"],
        cost_usd=0.4321,
        tokens=12345,
        stale_page_ids={"module_page:m7", "module_page:other"},
    )
    base.update(kw)
    return GenerateOutcome(**base)  # type: ignore[arg-type]


def test_account_for_plan_puts_every_planned_page_in_one_bucket() -> None:
    o = _outcome()
    counts = command._account_for_plan(o.plan.generate_ids, o.generated_pages, o.swept_page_ids)
    assert counts == {
        "written": 2,
        "unchanged": 1,
        "failed": 1,
        "retired": 3,
        "not_produced": 3,
    }
    assert sum(counts.values()) == len(o.plan.generate_ids)


def test_report_names_each_bucket_the_cost_and_next_steps() -> None:
    out = _render(_outcome())
    assert "Generated 3 of 10 planned pages (1 unchanged, no model call) in 12.0s." in out
    assert (
        "Not generated: 3 retired (merged into pages this run wrote), "
        "3 not produced (the code no longer yields them), 1 failed (kept as a stub)."
    ) in out
    assert "Cost: $0.432 (12,345 tokens)." in out
    assert "1 page(s) still unwritten. Run repowise generate --unwritten" in out
    # m7 was planned and not produced, so only the other stale page is retryable.
    assert "1 page(s) still stale. Run repowise generate --stale" in out
    assert "1 stale page(s) were not produced this run" in out


def test_clean_run_prints_no_leftover_lines() -> None:
    out = _render(
        _outcome(
            generated_pages=[_page(f"module_page:m{i}") for i in range(10)],
            swept_page_ids=[],
            remaining_template_pages=0,
            stale_page_ids=set(),
            cost_usd=0.0,
            tokens=0,
        )
    )
    assert "Generated 10 of 10 planned pages in 12.0s." in out
    assert "Not generated" not in out
    assert "Cost: $0.000 (0 tokens)." in out
    assert "Every concept page is now written." in out
    assert "stale" not in out
