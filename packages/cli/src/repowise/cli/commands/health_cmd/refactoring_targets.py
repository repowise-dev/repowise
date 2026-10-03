"""Refactoring-target aggregation and per-refactoring-type plan renderers.

Ranks findings per file by impact/effort, then prints the composed
opportunities with their concrete structured plans (Extract Class/Helper/
Method, Move Method, Break Cycle, Split File) for console, Markdown and JSON.

Two sources feed one set of renderers. The default reads what the index
stored, in the queue order MCP and the web UI serve. ``--recompute`` folds a
fresh in-process analysis instead, which is the only option outside an
indexed repository.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import click
from rich.table import Table

from repowise.cli.helpers import console
from repowise.core.analysis.health.models import primary_finding


def _effort_bucket(nloc: int) -> tuple[str, int]:
    if nloc <= 40:
        return "S", 1
    if nloc <= 150:
        return "M", 2
    if nloc <= 400:
        return "L", 3
    return "XL", 5


def _suggestion_to_dict(s: object) -> dict[str, Any]:
    """Serialize a ``RefactoringSuggestion`` dataclass to a plain dict."""
    import dataclasses

    if dataclasses.is_dataclass(s) and not isinstance(s, type):
        return dataclasses.asdict(s)
    return dict(cast(Mapping[str, Any], s))


def _suggestion_path(s: object) -> str:
    if isinstance(s, dict):
        return str(s.get("file_path") or "")
    return str(getattr(s, "file_path", "") or "")


def _render_refactoring_targets(
    metrics: list, findings: list, suggestions: list | None = None, *, fmt: str, limit: int = 20
) -> None:
    """Aggregate findings per file, rank by impact/effort, render.

    When the refactoring layer produced structured *suggestions* (e.g. an
    Extract Class split), the concrete plan is attached to each target's row
    (JSON/MD) and printed as a group tree below the table.
    """
    suggestions = suggestions or []
    targets = _build_targets(metrics, findings, suggestions, limit=limit)

    # Structured plans are displayed independently of the impact/effort file
    # table (a god class worth splitting may not top that churn-weighted list).
    # The order is the canonical recommendation rank (benefit and leverage,
    # discounted by cost and risk), so preserve it rather than re-sorting.
    if suggestions and not all(
        isinstance(s, dict) and "benefit" in s and "validation" in s for s in suggestions
    ):
        from repowise.core.analysis.health.refactoring.recommendations import (
            build_recommendations,
            serialize_recommendations,
        )

        ranked_plans = serialize_recommendations(build_recommendations(suggestions))[:limit]
    else:
        ranked_plans = [_suggestion_to_dict(s) for s in suggestions][:limit]

    # The composed unit. One opportunity per file, ranked, with its plans as
    # ordered steps - the same vocabulary the board, the drawer and the agent
    # contract speak. The type-grouped dump this replaced made type the
    # organizing principle, so a file needing a split and two extractions
    # appeared three times in three sections.
    opportunities, _leads = _compose(findings, suggestions)
    plan_by_id = _plans_by_public_id(suggestions, ranked_plans)
    claimed = {s.plan_id for o in opportunities for s in o.steps} | {
        e.plan_id for o in opportunities for e in o.evidence
    }
    unattached = [row for pid, row in plan_by_id.items() if pid not in claimed]
    rows = [_opportunity_row(o) for o in opportunities]

    _emit(
        targets,
        rows,
        plan_by_id,
        fmt=fmt,
        unattached=unattached,
        json_payload={
            "targets": targets,
            "refactoring_opportunities": rows,
            "refactoring_plans": ranked_plans,
        },
    )


def _render_stored_refactoring_targets(
    repo_path: Path,
    *,
    fmt: str,
    limit: int = 20,
    file_filter: str | None = None,
    module_filter: str | None = None,
) -> bool:
    """Render from what the index stored. False when nothing is stored.

    Reads the same service MCP and the web UI read, in its default
    (diversified) queue order, so the three surfaces list the same work in the
    same order. Nothing is parsed or analyzed: the opportunities, their plans
    and the file table all come from rows the last ``init`` or ``update``
    wrote.
    """
    from repowise.cli.helpers import repo_index_session, run_async
    from repowise.core.analysis.health.refactoring.serving import parse_query
    from repowise.core.analysis.health.scoring import ZERO_IMPACT_DIMENSIONS
    from repowise.core.persistence import crud
    from repowise.server.services.refactoring_health import RefactoringHealthService

    def keep(path: str) -> bool:
        if file_filter and path != file_filter:
            return False
        return not (module_filter and not path.startswith(module_filter))

    async def _read() -> dict[str, Any] | None:
        async with repo_index_session(Path(repo_path)) as opened:
            if opened is None:
                return None
            session, repo_id = opened
            service = RefactoringHealthService(session, repo_id, Path(repo_path).name)
            summary = await service.summary()
            if summary.get("status") != "available":
                return None
            query, _ignored = parse_query(
                file_paths=[file_filter] if file_filter else None,
                path_prefix=module_filter,
                limit=limit,
            )
            page = await service.page(query)
            details = [
                await service.detail(item["opportunity_id"], step_limit=200, evidence_limit=50)
                for item in page.items
            ]
            metrics = [
                m for m in await crud.get_health_metrics(session, repo_id) if keep(m.file_path)
            ]
            findings = [
                f
                for f in await crud.get_health_findings(
                    session, repo_id, exclude_dimensions=tuple(sorted(ZERO_IMPACT_DIMENSIONS))
                )
                if keep(f.file_path)
            ]
            return {
                "summary": summary,
                "total": page.total,
                "hidden": page.hidden,
                "details": details,
                "metrics": metrics,
                "findings": findings,
            }

    stored = run_async(_read())
    if stored is None:
        return False

    rows = [_stored_row(d) for d in stored["details"]]
    plans = [plan for d in stored["details"] for plan in d.get("plans") or []]
    plan_by_id = {plan["id"]: plan for plan in plans if plan.get("id")}
    targets = _build_targets(stored["metrics"], stored["findings"], plans, limit=limit)
    analyzed_commit = stored["summary"].get("analyzed_commit")
    if fmt == "table":
        commit = f" at {analyzed_commit[:7]}" if analyzed_commit else ""
        hidden = (stored["hidden"] or {}).get("total") or 0
        scope = (
            f"{stored['total']} worth doing ({hidden} more in the full inventory)"
            if stored["hidden"] is not None
            else f"{stored['total']} open opportunities"
        )
        console.print(
            f"[dim]Read from the index{commit}. Showing {len(rows)} of {scope}. "
            "Pass --recompute to analyze the working tree instead.[/dim]"
        )
    _emit(
        targets,
        rows,
        plan_by_id,
        fmt=fmt,
        # The queue lists what opportunities claim; unattached clones are not
        # in it, on any surface.
        unattached=[],
        json_payload={
            "source": "index",
            "analyzed_commit": analyzed_commit,
            "opportunities_total": stored["total"],
            "opportunities_hidden": stored["hidden"],
            "targets": targets,
            "refactoring_opportunities": rows,
            "refactoring_plans": plans,
        },
    )
    return True


def _build_targets(
    metrics: list, findings: list, suggestions: list, *, limit: int
) -> list[dict]:
    """Files ranked by impact over effort, each carrying its plans."""
    sugg_by_file: dict[str, list] = {}
    for s in suggestions:
        sugg_by_file.setdefault(_suggestion_path(s), []).append(s)

    by_file: dict[str, list] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)

    metric_by_path = {m.file_path: m for m in metrics}
    targets: list[dict] = []
    for path, fs in by_file.items():
        m = metric_by_path.get(path)
        nloc = m.nloc if m is not None else 0
        score = m.score if m is not None else 10.0
        primary = primary_finding(fs)
        # None means every finding here is advisory, which deducts nothing and
        # so names no work for a table ranked by impact over effort.
        if primary is None:
            continue
        total_impact = round(sum(x.health_impact for x in fs), 3)
        bucket, weight = _effort_bucket(nloc)
        file_sugg = sugg_by_file.get(path, [])
        targets.append(
            {
                "file_path": path,
                "score": round(score, 2),
                "nloc": nloc,
                "primary_biomarker": primary.biomarker_type,
                "primary_severity": str(primary.severity),
                "primary_reason": primary.reason,
                "total_impact": total_impact,
                "effort_bucket": bucket,
                "impact_per_effort": round(total_impact / weight, 3),
                "finding_count": len(fs),
                "plans": [_suggestion_to_dict(s) for s in file_sugg],
            }
        )
    targets.sort(key=lambda t: (-t["impact_per_effort"], -t["total_impact"]))
    return targets[:limit]


def _emit(
    targets: list[dict],
    opportunities: list[dict],
    plan_by_id: dict[str, dict],
    *,
    fmt: str,
    unattached: list[dict],
    json_payload: dict[str, Any],
) -> None:
    """Print one report, whichever source built it."""
    if fmt == "json":
        click.echo(json.dumps(json_payload, indent=2))
        return
    if fmt == "md":
        click.echo("# Refactoring targets\n")
        for t in targets:
            click.echo(
                f"- **{t['file_path']}** ({t['effort_bucket']}, "
                f"score {t['score']:.1f}/10, -{t['total_impact']:.2f}) "
                f"— {t['primary_biomarker']}: {t['primary_reason']}"
            )
        _render_opportunities_md(opportunities, plan_by_id)
        _render_unattached_md(unattached)
        return

    table = Table(title=f"Refactoring targets ({len(targets)})")
    table.add_column("File", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Impact", justify="right")
    table.add_column("Effort", justify="center")
    table.add_column("Ratio", justify="right")
    table.add_column("Primary marker")
    for t in targets:
        table.add_row(
            t["file_path"],
            f"{t['score']:.1f}",
            f"-{t['total_impact']:.2f}",
            t["effort_bucket"],
            f"{t['impact_per_effort']:.2f}",
            t["primary_biomarker"],
        )
    console.print(table)
    _render_opportunities_console(opportunities, plan_by_id)
    _render_unattached_console(unattached)


def _compose(findings: list, suggestions: list) -> tuple[list, dict]:
    """Fold the detector rows into ranked per-file opportunities.

    The same pure function the analyzer and the server both call, so the CLI
    cannot drift into a third answer about what the work is or how it ranks.
    Returns the opportunities and a lookup from plan id to the serialized plan,
    so a step can render its own type-specific detail.
    """
    from repowise.core.analysis.health.models import primary_biomarker_by_file
    from repowise.core.analysis.health.refactoring.opportunity import compose_opportunities

    leads = primary_biomarker_by_file(findings) if findings else {}
    opportunities = compose_opportunities(
        suggestions, primary_biomarker_by_file=leads, findings=findings or None
    )
    return opportunities, leads


def _plans_by_public_id(suggestions: list, ranked_plans: list[dict]) -> dict[str, dict]:
    """Serialized plans keyed by the id composition uses for a step.

    Composition stamps public ids over the same suggestion list, in the same
    order, so zipping the two is exact. Keyed rather than positional because
    ``serialize_recommendations`` returns rank order, and pairing by position
    against the input is how a step ends up rendering another plan's detail.
    """
    from repowise.core.analysis.health.refactoring.identity import assign_public_ids
    from repowise.core.analysis.health.refactoring.recommendations import rehydrate_suggestion

    hydrated = [rehydrate_suggestion(row) for row in suggestions]
    ids = assign_public_ids(hydrated)
    by_key: dict[tuple, str] = {}
    for suggestion, public_id in zip(hydrated, ids, strict=True):
        by_key[(suggestion.refactoring_type, suggestion.file_path, suggestion.target_symbol)] = (
            public_id
        )
    out: dict[str, dict] = {}
    for plan in ranked_plans:
        key = (plan["refactoring_type"], plan["file_path"], plan["target_symbol"])
        public_id = by_key.get(key)
        if public_id:
            out[public_id] = plan
    return out


def _opportunity_row(o) -> dict:
    """The wire shape, matching the server's queue row field for field."""
    return {
        "opportunity_id": o.opportunity_id,
        # Freshly composed, so no triage has touched it yet: the same state the
        # stored queue lists by default.
        "status": "open",
        "file_path": o.file_path,
        "lead_biomarker": o.lead_biomarker,
        "lead_refactoring_type": o.lead_refactoring_type,
        "addresses_primary_problem": o.addresses_primary_problem,
        "effort_bucket": o.effort_bucket,
        "confidence": o.confidence,
        "step_count": len(o.steps),
        "mechanical_steps": sum(
            1 for s in o.steps if s.applicability.classification == "mechanical"
        ),
        "recoverable_health": round(float(o.recoverable_health), 3),
        "rank_score": round(float(o.rank_score), 4),
        "steps": [
            {
                "plan_id": s.plan_id,
                "refactoring_type": s.refactoring_type,
                "target_symbol": s.target_symbol,
                "file_path": s.file_path,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "classification": s.applicability.classification,
                "reasons": list(s.applicability.reasons),
                "unknowns": list(s.applicability.unknowns),
                "relocated_by": s.relocated_by,
            }
            for s in o.steps
        ],
        "evidence": [
            {
                "plan_id": e.plan_id,
                "refactoring_type": e.refactoring_type,
                "target_symbol": e.target_symbol,
            }
            for e in o.evidence
        ],
    }


def _primary_word(value) -> str:
    """Tri-state, and the unknown case says so rather than reading as a no."""
    if value is True:
        return "addresses the file's main problem"
    if value is False:
        return "does not address the file's main problem"
    return "no dominant problem recorded"


def _stored_row(detail: dict[str, Any]) -> dict[str, Any]:
    """A stored opportunity detail, flattened to the wire row the renderers read."""
    row = {key: detail.get(key) for key in _ROW_KEYS}
    steps = []
    for s in detail.get("steps") or []:
        applicability = s.get("applicability") or {}
        steps.append(
            {
                "plan_id": s.get("plan_id"),
                "refactoring_type": s.get("refactoring_type"),
                "target_symbol": s.get("target_symbol"),
                "file_path": s.get("file_path"),
                "line_start": s.get("line_start"),
                "line_end": s.get("line_end"),
                "classification": applicability.get("classification"),
                "reasons": list(applicability.get("reasons") or []),
                "unknowns": list(applicability.get("unknowns") or []),
                "relocated_by": s.get("relocated_by"),
            }
        )
    row["steps"] = steps
    row["evidence"] = [
        {
            "plan_id": e.get("plan_id"),
            "refactoring_type": e.get("refactoring_type"),
            "target_symbol": e.get("target_symbol"),
        }
        for e in detail.get("evidence") or []
    ]
    return row


_ROW_KEYS = (
    "opportunity_id",
    "status",
    "file_path",
    "lead_biomarker",
    "lead_refactoring_type",
    "addresses_primary_problem",
    "effort_bucket",
    "confidence",
    "step_count",
    "mechanical_steps",
    "recoverable_health",
    "rank_score",
)


def _render_opportunities_console(opportunities: list[dict], plan_by_id: dict) -> None:
    if not opportunities:
        return
    console.print(f"\n[bold]Refactoring opportunities ({len(opportunities)})[/bold]")
    console.print(
        "[dim]One per file. Steps are in dependency-safe order.[/dim]"
    )
    for rank, o in enumerate(opportunities, 1):
        console.print(
            f"\n[bold]{rank}.[/bold] [cyan]{o['file_path']}[/cyan] [dim]-[/dim] "
            f"{o['step_count']} step(s), {o['mechanical_steps']} mechanical "
            f"[dim](effort {o['effort_bucket']}, {o['confidence']} confidence, "
            f"recover ~{o['recoverable_health']:.2f}, "
            f"{_primary_word(o['addresses_primary_problem'])})[/dim]"
        )
        for i, s in enumerate(o["steps"], 1):
            mark = "mechanical" if s["classification"] == "mechanical" else "judgment"
            span = f":{s['line_start']}-{s['line_end']}" if s["line_start"] else ""
            console.print(
                f"  [bold]{i}.[/bold] {s['refactoring_type']} "
                f"[green]{s['target_symbol']}[/green] "
                f"[dim]{s['file_path']}{span}[/dim] [dim]({mark})[/dim]"
            )
            if s["relocated_by"]:
                # The step's own path and span describe where the symbol was.
                console.print(
                    "    [yellow]moved by an earlier step - locate it again "
                    "before applying[/yellow]"
                )
            for line in _plan_detail_console(plan_by_id.get(s["plan_id"], {})):
                console.print(line)
            if s["unknowns"]:
                console.print(f"    [dim]not established: {', '.join(s['unknowns'])}[/dim]")
        for e in o["evidence"]:
            console.print(
                f"  [dim]evidence:[/dim] {e['refactoring_type']} {e['target_symbol']} "
                "[dim](an observation, not a step)[/dim]"
            )


def _render_opportunities_md(opportunities: list[dict], plan_by_id: dict) -> None:
    if not opportunities:
        return
    click.echo("\n## Refactoring opportunities\n")
    click.echo("One per file. Steps are in dependency-safe order.\n")
    for rank, o in enumerate(opportunities, 1):
        click.echo(
            f"{rank}. **{o['file_path']}** - {o['step_count']} step(s), "
            f"{o['mechanical_steps']} mechanical "
            f"(effort {o['effort_bucket']}, {o['confidence']} confidence, "
            f"recover ~{o['recoverable_health']:.2f}, "
            f"{_primary_word(o['addresses_primary_problem'])})"
        )
        for i, s in enumerate(o["steps"], 1):
            mark = "mechanical" if s["classification"] == "mechanical" else "judgment"
            span = f":{s['line_start']}-{s['line_end']}" if s["line_start"] else ""
            click.echo(
                f"   {i}. {s['refactoring_type']} **{s['target_symbol']}** "
                f"({s['file_path']}{span}) - {mark}"
            )
            if s["relocated_by"]:
                click.echo(
                    "    - moved by an earlier step; locate it again before applying"
                )
            for line in _plan_detail_md(plan_by_id.get(s["plan_id"], {})):
                click.echo(line)
            if s["unknowns"]:
                click.echo(f"    - not established: {', '.join(s['unknowns'])}")
        for e in o["evidence"]:
            click.echo(
                f"   - evidence: {e['refactoring_type']} {e['target_symbol']} "
                "(an observation, not a step)"
            )


def _render_unattached_console(plans: list[dict]) -> None:
    """Plans no opportunity claims.

    A file whose only plans are demoted clones publishes no opportunity, so
    without this the CLI would print a smaller number than the analyzer found
    and call it the total. They are listed as observations, under their own
    heading, rather than folded back in as work.
    """
    if not plans:
        return
    console.print(f"\n[bold]Unattached observations ({len(plans)})[/bold]")
    console.print(
        "[dim]Real duplication, not ranked work: no file's opportunity is built "
        "on them.[/dim]"
    )
    for p in plans:
        console.print(f"\n[cyan]{p['target_symbol']}[/cyan] [dim]({p['file_path']})[/dim]")
        for line in _plan_detail_console(p):
            console.print(line)


def _render_unattached_md(plans: list[dict]) -> None:
    if not plans:
        return
    click.echo("\n## Unattached observations\n")
    click.echo(
        "Real duplication, not ranked work: no file's opportunity is built on them.\n"
    )
    for p in plans:
        click.echo(f"- **{p['target_symbol']}** ({p['file_path']})")
        for line in _plan_detail_md(p):
            click.echo(line)


def _plan_detail_console(p: dict) -> list[str]:
    """The type-specific detail for one step, as console lines.

    These used to be six sections, one per refactoring type, printed under the
    table. Type was the organizing principle, so a file needing a split and two
    extractions appeared three times in three places and the reader had to
    reassemble it. The detail itself was the good part and is unchanged; it is
    the grouping that went.

    Empty when the step's plan is not in the rendered set: composition runs over
    every suggestion while the plan list is truncated to ``limit``, so a step can
    outrank its own plan's detail.
    """
    if not p:
        return []
    kind = p["refactoring_type"]
    pl = p.get("plan") or {}
    ev = p.get("evidence") or {}
    out: list[str] = []

    if kind == "extract_class":
        groups = pl.get("groups", [])
        out.append(
            f"    [dim]LCOM4={ev.get('lcom4')}, {ev.get('method_count')} methods, "
            f"WMC={ev.get('wmc')} -> split into {len(groups)} classes[/dim]"
        )
        for i, g in enumerate(groups, 1):
            fields = ", ".join(g["fields"]) or "-"
            out.append(f"    {i}. methods: {', '.join(g['methods'])}  [dim]fields:[/dim] {fields}")
    elif kind == "extract_helper":
        occ = pl.get("occurrences", [])
        co = ev.get("co_change_count", 0)
        out.append(
            f"    [dim]{ev.get('duplicated_lines')} duplicated lines across {len(occ)} sites, "
            f"extract near [/dim][bold]{_helper_site(pl)}[/bold]"
            + (f" [dim](co-changed {co}x)[/dim]" if co else "")
        )
        for o in occ:
            out.append(f"    [dim]-[/dim] {o['file']}:{o['line_start']}-{o['line_end']}")
    elif kind == "extract_method":
        span = pl.get("span", {}) or {}
        params = ", ".join(pl.get("params", [])) or "-"
        returns = ", ".join(pl.get("returns", [])) or "none"
        out.append(
            f"    [dim]extract lines {span.get('start')}-{span.get('end')} "
            f"({ev.get('slice_nloc')} lines, -{ev.get('ccn_removed')} CCN)[/dim]"
        )
        out.append(f"    [dim]in:[/dim] {params}    [dim]out:[/dim] {returns}")
    elif kind == "move_method":
        to_file = pl.get("to_file")
        dest = f"{pl.get('to_class')}" + (f" [dim]({to_file})[/dim]" if to_file else "")
        out.append(
            f"    [dim]move to[/dim] [bold]{dest}[/bold] "
            f"[dim](uses {ev.get('foreign_calls')} of its members vs "
            f"{ev.get('own_calls')} of its own)[/dim]"
        )
    elif kind == "break_cycle":
        cuts = pl.get("cut_edges", [])
        out.append(
            f"    [dim]import cycle of {ev.get('cycle_size')} files "
            f"({ev.get('edge_count')} edges), cut {len(cuts)} edge(s)"
            f"{_idiom_note(pl)}[/dim]"
        )
        for e in cuts:
            out.append(f"    [dim]-[/dim] invert {e['from']} -> {e['to']}")
    elif kind == "split_file":
        groups = pl.get("groups", [])
        br = p.get("blast_radius") or {}
        shim = " +shim" if pl.get("shim_required") else ""
        out.append(
            f"    [dim]{ev.get('symbol_count')} symbols, {ev.get('file_nloc')} NLOC, "
            f"modularity {ev.get('modularity')} -> split into {len(groups)} files{shim} "
            f"({br.get('import_rewrites', 0)} import rewrites in "
            f"{br.get('dependent_count', 0)} files)[/dim]"
        )
        for i, g in enumerate(groups, 1):
            out.append(
                f"    {i}. [green]{g.get('suggested_file') or '(unnamed)'}[/green]: "
                f"{', '.join(g.get('symbols', []))}"
            )
        residual = pl.get("residual")
        if residual and residual.get("symbols"):
            out.append(f"    [dim]core (shared):[/dim] {', '.join(residual['symbols'])}")
    return out


def _plan_detail_md(p: dict) -> list[str]:
    """The same detail, as Markdown lines. Empty for a plan outside the limit."""
    if not p:
        return []
    kind = p["refactoring_type"]
    pl = p.get("plan") or {}
    ev = p.get("evidence") or {}
    out: list[str] = []

    if kind == "extract_class":
        groups = pl.get("groups", [])
        out.append(
            f"    - LCOM4={ev.get('lcom4')}, split into {len(groups)} classes:"
        )
        for i, g in enumerate(groups, 1):
            fields = ", ".join(g["fields"]) or "-"
            out.append(f"      {i}. methods: {', '.join(g['methods'])}  ·  fields: {fields}")
    elif kind == "extract_helper":
        occ = pl.get("occurrences", [])
        out.append(
            f"    - {ev.get('duplicated_lines')} duplicated lines across {len(occ)} sites, "
            f"extract a helper near `{_helper_site(pl)}`:"
        )
        for o in occ:
            out.append(f"      - {o['file']}:{o['line_start']}-{o['line_end']}")
    elif kind == "extract_method":
        span = pl.get("span", {}) or {}
        params = ", ".join(pl.get("params", [])) or "-"
        returns = ", ".join(pl.get("returns", [])) or "none"
        out.append(
            f"    - extract lines {span.get('start')}-{span.get('end')} "
            f"({ev.get('slice_nloc')} lines, -{ev.get('ccn_removed')} CCN)  ·  "
            f"in: {params}  ·  out: {returns}"
        )
    elif kind == "move_method":
        dest = pl.get("to_class")
        if pl.get("to_file"):
            dest = f"{dest} ({pl['to_file']})"
        out.append(
            f"    - move to `{dest}` "
            f"(uses {ev.get('foreign_calls')} vs {ev.get('own_calls')} own members)"
        )
    elif kind == "break_cycle":
        cuts = pl.get("cut_edges", [])
        out.append(
            f"    - import cycle of {ev.get('cycle_size')} files, cut {len(cuts)} edge(s)"
            f"{_idiom_note(pl)}:"
        )
        for e in cuts:
            out.append(f"      - invert {e['from']} -> {e['to']}")
    elif kind == "split_file":
        groups = pl.get("groups", [])
        out.append(
            f"    - {ev.get('symbol_count')} symbols, modularity {ev.get('modularity')}, "
            f"split into {len(groups)} files:"
        )
        for i, g in enumerate(groups, 1):
            name = g.get("suggested_file") or "(unnamed)"
            out.append(f"      {i}. `{name}`: {', '.join(g.get('symbols', []))}")
        residual = pl.get("residual")
        if residual and residual.get("symbols"):
            out.append(f"      - core (shared): {', '.join(residual['symbols'])}")
    return out


def _idiom_note(pl: dict) -> str:
    """Marks a cycle the detector judged idiomatic, so its cut reads as optional."""
    return " (same directory, idiomatic; optional)" if pl.get("idiom") else ""


def _helper_site(pl: dict) -> str:
    """Where a shared helper should live.

    ``directory`` first: it is the only namespace new plans carry, and on plans
    stored before that it was the correct one anyway - ``module`` held a
    community label naming a directory the occurrences were not in on 905 of 905
    measured rows, so rendering it told people to put a shared helper in a
    package two thirds of its callers are not in.
    """
    site = pl.get("suggested_site", {}) or {}
    return site.get("directory") or site.get("module") or "a shared module"
