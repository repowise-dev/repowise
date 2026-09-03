"""``repowise risk`` — just-in-time change risk, for a change or for paths.

Two questions, one command, because they are the same question asked of
different subjects.

A REVSPEC scores a *change* from its diff shape — size, diffusion, author
familiarity — and prints a benchmarked review priority plus a supporting 0-10
diff-shape score with an attributable breakdown. Runs
in-process: pure git + learned constants, no LLM, no network. A natural
pre-merge / PR gate, complementary to ``repowise health`` (which scores files).

``--target <path>`` instead asks what history says about touching those files:
bug-fix pressure, churn trend, dependents, co-change partners, ownership. That
is the ``get_risk`` MCP tool, and this command is a thin adapter over it — the
same seam ``ask`` / ``context`` / ``symbol`` / ``why`` use. It reads the index
rather than git, so the repo must be indexed.

Examples:
    repowise risk                       # score uncommitted work, else HEAD
    repowise risk HEAD                  # score the last commit
    repowise risk abc123                # score a single commit
    repowise risk main..HEAD            # score a branch / PR range as one change
    repowise risk --target a.py -t b.py # what history says about those files
"""

from __future__ import annotations

import json

import click
from rich.table import Table

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.helpers import console, err_console
from repowise.cli.output import emit_json, format_option, full_option
from repowise.core.analysis.change_risk import (
    change_risk_payload,
    review_priority_classification,
    score_live_change,
)

_PRIORITY_LEAD = {
    "low": "Smaller or more focused than a typical commit in this repo",
    "moderate": "Typical diff size and spread for this repo",
    "high": "Larger or more dispersed than most commits in this repo",
}


#: Per-target keys the trim drops, and nothing else.
#:
#: A denylist, not an allowlist, and deliberately so: the failure mode of a
#: denylist is a payload slightly larger than intended, while the failure mode
#: of an allowlist is a silently discarded answer — which is the mistake the
#: adapter commands' first projections made eight times over. ``get_risk``'s
#: card is a page of small scalars rather than a block of source, so there is
#: little to win by trimming harder and a lot to lose.
#:
#: ``_base_dep_count`` is bookkeeping the tool uses to rebuild ``risk_summary``
#: after enrichment. ``impact_surface`` is the transitive dependent set, the one
#: genuinely unbounded block, and its own summary counts survive in
#: ``risk_summary`` and ``dependents_count``. It is opt-in on the tool now and
#: this command does not ask for it; the entry stays so that a caller passing a
#: hand-built payload gets the same projection.
_DROPPED_TARGET_KEYS = ("_base_dep_count", "impact_surface")


def _project_target(card: dict) -> dict:
    """One ``get_risk`` target card, minus the blocks in ``_DROPPED_TARGET_KEYS``."""
    return {k: v for k, v in card.items() if k not in _DROPPED_TARGET_KEYS}


def project_risk(payload: dict) -> dict:
    """``get_risk``'s dict, trimmed to what a CLI caller reads.

    Kept whole: ``directive`` (in PR mode this *is* the answer, and the tool's
    own docstring tells a reader to lead with it), ``pr_blast_radius``,
    ``global_hotspots``, and the freshness half of ``_meta``. Dropped: the
    timing half of ``_meta``, and the per-target keys named above.

    ``pr_blast_radius`` survives although ``directive`` summarises much of it,
    because ``recommended_reviewers`` has no substitute anywhere else in the
    response, and because the tool has *already* capped its four noisy lists
    (15/10/10/5) before it gets here — so the block a caller would most want is
    the one an allowlist would silently discard, at almost no size.
    """
    out: dict = {}
    if payload.get("directive"):
        out["directive"] = payload["directive"]
    out["targets"] = {
        name: _project_target(card) for name, card in (payload.get("targets") or {}).items()
    }
    for key in ("risk_scales", "pr_blast_radius", "global_hotspots", "omission_marker"):
        if payload.get(key):
            out[key] = payload[key]
    note = _ta.index_note(payload)
    if note:
        out["index"] = note
    return out


def _target_risk(
    repo_path: str,
    targets: tuple[str, ...],
    changed_files: tuple[str, ...],
    fmt: str,
    full: bool,
) -> None:
    """``--target``: what history says about touching these files."""
    fmt = _ta.resolve_format_for(fmt, full)
    repo = _ta.resolve_indexed_repo(
        path=repo_path,
        repo_alias=None,
        no_workspace=False,
        fmt=fmt,
        command="risk",
    )

    def _factory():
        from repowise.server.mcp_server.tool_risk.get_risk import get_risk

        return get_risk(
            targets=list(targets),
            changed_files=list(changed_files) or None,
            # _render_target_risk prints risk_type and change_magnitude.
            include=["churn"],
        )

    payload = _ta.run(repo, _factory, "get_risk")

    if full:
        _ta.emit_full(payload)
        return
    _ta.emit_error(payload, fmt, extra={"targets": list(targets)})
    projected = project_risk(payload)
    if fmt == "json":
        emit_json(projected)
        return
    _render_target_risk(projected, targets)
    _ta.print_index_note(payload, fmt)


def _render_target_risk(projected: dict, requested: tuple[str, ...]) -> None:
    """The human path for ``--target``: the directive first, then a card each."""
    from rich.markup import escape

    directive = projected.get("directive")
    if directive:
        # PR mode leads with the directive because that is the tool's contract:
        # what may break, what is missing, what to run.
        console.print(f"\n[bold]Directive[/bold] {escape(str(directive.get('summary', '')))}")
        for label, key in (
            ("May break", "may_break"),
            ("Tests that may break", "may_break_tests"),
            ("Missing co-changes", "missing_cochanges"),
            ("Files without tests", "missing_tests"),
            ("Tests to run", "tests_to_run"),
        ):
            _print_list(label, directive.get(key) or [])
        for label, key in _DIRECTIVE_RECORD_BLOCKS:
            _print_records(
                label,
                directive.get(key) or [],
                key,
                truncated=directive.get(f"{key}_truncated") or 0,
            )

    targets = projected.get("targets") or {}
    for name in requested:
        card = targets.get(name)
        if card is None:
            # An excluded path is filtered out of the request before the tool
            # sees it, so a requested target with no card is an answer ("this
            # is not indexed here"), not a gap to render as an empty section.
            console.print(f"\n[yellow]{escape(name)} — not indexed (or excluded).[/yellow]")
            continue
        _render_card(name, card)
    # Anything the tool returned that was not asked for by name (it normalises
    # paths), so a caller never loses a card to a spelling difference.
    for name, card in targets.items():
        if name not in requested:
            _render_card(name, card)

    hotspots = projected.get("global_hotspots") or []
    if hotspots:
        table = Table(title="Elsewhere in this repo")
        table.add_column("File", style="cyan")
        table.add_column("Hotspot", justify="right")
        table.add_column("Owner")
        table.add_column("Fixes", justify="right")
        for h in hotspots:
            score = h.get("hotspot_score")
            fixes = h.get("fix_count")
            table.add_row(
                escape(str(h.get("file_path", ""))),
                "?" if score is None else f"{float(score):.0%}",
                escape(str(h.get("primary_owner") or "unknown")),
                "" if fixes is None else str(fixes),
            )
        console.print(table)

    if projected.get("omission_marker"):
        # Printed escaped: it opens with a bracket, which rich would parse as a
        # style tag and delete outright, taking the expand handle with it.
        console.print(escape(str(projected["omission_marker"])), style="cyan")


def _render_card(name: str, card: dict) -> None:
    """One target's risk card."""
    from rich.markup import escape

    console.print(f"\n[bold cyan]{escape(name)}[/bold cyan]")
    summary = card.get("risk_summary")
    if summary:
        console.print(f"  {escape(str(summary))}")
    trend = card.get("trend") or "unknown"
    console.print(
        f"  [dim]hotspot {float(card.get('hotspot_score') or 0.0):.0%} ({trend}) · "
        f"{card.get('dependents_count', 0)} direct dependents · "
        f"{card.get('risk_type', 'unknown')} · owned "
        f"{_ta.owner_share(card.get('owner_pct'))} by "
        f"{escape(str(card.get('primary_owner') or 'unknown'))}[/dim]"
    )
    # Kept in the projection, so each owes a line here: a key kept in the
    # payload that no renderer prints is the second silent failure mode of a
    # trim, and the one a projection test cannot see.
    health = card.get("health_score")
    coverage = card.get("coverage_pct")
    if health is not None or coverage is not None:
        parts = []
        if health is not None:
            parts.append(f"health {float(health):.1f}/10")
        if coverage is not None:
            parts.append(f"coverage {float(coverage):.0f}%")
        console.print(f"  [dim]{escape(' · '.join(parts))}[/dim]")
    # Keyed ``biomarker_type``, not ``name`` — a generic key guess prints the
    # whole dict repr, which is how the directive blocks went wrong too.
    biomarkers = card.get("top_biomarkers") or []
    if biomarkers:
        shown = []
        for b in biomarkers[:3]:
            if not isinstance(b, dict):
                shown.append(str(b))
                continue
            where = f" in {b['function_name']}" if b.get("function_name") else ""
            shown.append(f"{b.get('biomarker_type', '?')} ({b.get('severity', '?')}){where}")
        console.print(f"  [dim]{escape(', '.join(shown))}[/dim]")
    magnitude = card.get("change_magnitude") or {}
    if magnitude:
        console.print(
            f"  [dim]+{magnitude.get('lines_added_90d', 0)} / "
            f"-{magnitude.get('lines_deleted_90d', 0)} lines in 90d · "
            f"avg commit {magnitude.get('avg_commit_size', 0)} lines · "
            f"{card.get('contributor_count', 0)} contributor(s), "
            f"bus factor {card.get('bus_factor', '?')}[/dim]"
        )
    if card.get("original_path"):
        console.print(f"  [dim]Renamed from {escape(str(card['original_path']))}.[/dim]")
    if card.get("recent_owner") and card["recent_owner"] != card.get("primary_owner"):
        # Ownership has moved: the historical owner is not who to ask now.
        console.print(
            f"  [dim]Recently owned {_ta.owner_share(card.get('recent_owner_pct'))} by "
            f"{escape(str(card['recent_owner']))}.[/dim]"
        )
    cross = card.get("cross_repo_impact") or {}
    if cross:
        repos = cross.get("affected_repos") or []
        historical = cross.get("cross_repo_consumers") or []
        contracts = cross.get("contract_consumers") or []
        console.print(
            "  [bold]Crosses repo boundaries[/bold] [dim]"
            f"{cross.get('cross_repo_consumers_total', len(historical))} historical co-change(s)"
            + (
                f", {cross.get('contract_consumers_total', len(contracts))} "
                "typed contract consumer(s)"
                if contracts
                else ""
            )
            + (f" in {', '.join(str(r) for r in repos)}" if repos else "")
            + "[/dim]"
        )
    defect = card.get("defect_profile")
    if defect:
        magnet = " [magenta](bug magnet)[/magenta]" if defect.get("bug_magnet") else ""
        console.print(
            f"  [yellow]{defect.get('fix_count', 0)} bug fix(es)[/yellow], last "
            f"{defect.get('last_fix_days_ago', '?')} day(s) ago{magnet}"
        )
        # ``top_symbols`` is a ``{name: fix_count}`` dict, not a list; iterating
        # it bare prints the names and throws the counts away.
        top = defect.get("top_symbols") or {}
        pairs = top.items() if isinstance(top, dict) else [(s, None) for s in top]
        _print_list(
            "Mostly in",
            [f"{name}" + (f" (x{count})" if count is not None else "") for name, count in pairs],
            indent="    ",
        )
    if card.get("test_gap"):
        console.print("  [yellow]No test file matches this file's name.[/yellow]")
    for signal in card.get("security_signals") or []:
        console.print(
            f"  [red]{escape(str(signal.get('severity', '')))}[/red] "
            f"{escape(str(signal.get('kind', '')))}: {escape(str(signal.get('snippet', '')))}"
        )
    partners = card.get("co_change_partners") or []
    if partners:
        console.print("  [bold]Co-changes with[/bold]")
        for p in partners[:8]:
            link = " [dim](also imports)[/dim]" if p.get("has_import_link") else ""
            # A recency-decayed weight rather than a raw tally, so it is not an
            # integer; ``:g`` keeps a whole number whole and trims the rest.
            weight = p.get("weight", 0)
            weight_text = f"{float(weight):.1f}".rstrip("0").rstrip(".") if weight else "0"
            console.print(
                f"    {escape(str(p.get('file_path', '')))} [dim]x{weight_text}[/dim]{link}"
            )
        if len(partners) > 8:
            console.print(f"    [dim]… and {len(partners) - 8} more (--format json).[/dim]")
    episodes = card.get("episodes")
    if episodes:
        console.print(f"  [dim]{episodes} recorded episode(s) — see 'repowise why'.[/dim]")


def _print_list(label: str, values: list[str], *, indent: str = "  ") -> None:
    from rich.markup import escape

    if not values:
        return
    console.print(f"{indent}[bold]{label}[/bold] " + ", ".join(escape(str(v)) for v in values))


#: The directive blocks whose entries are dicts, with a formatter each.
#:
#: One shared "try these keys in order" chain does not work here: the six
#: blocks are built in ``tool_risk/directives.py`` with six different key sets
#: and only two of them carry any key a generic chain would guess, so the other
#: four printed as raw Python dict reprs. Each formatter is written against
#: that module's construction site.
_DIRECTIVE_RECORD_BLOCKS = (
    ("Consumer structural reach", "will_break_consumers"),
    ("Missing cross-repo co-changes", "missing_cross_repo_cochanges"),
    ("Breaking contract changes", "breaking_changes"),
    ("Conformance violations", "conformance_violations"),
    ("Dependency cycles", "dependency_cycles"),
    ("Governance risk", "governance_risk"),
)


def _record_text(key: str, entry: dict) -> str:
    """One directive entry as a line, per the shape its block is built with."""
    if key in ("will_break_consumers", "missing_cross_repo_cochanges"):
        where = f" [{entry.get('repo')}]" if entry.get("repo") else ""
        return f"{entry.get('service') or entry.get('repo') or '?'}{where}"
    if key == "breaking_changes":
        consumers = entry.get("impacted_consumers") or []
        who = f" — endangers {len(consumers)} consumer(s)" if consumers else ""
        return (
            f"{entry.get('contract_id') or '?'}: {entry.get('kind') or '?'}"
            f" ({entry.get('severity') or '?'}) {entry.get('detail') or ''}{who}".rstrip()
        )
    if key == "conformance_violations":
        rule = entry.get("rule") or f"{entry.get('source')} -> {entry.get('target')}"
        return f"{rule}{': ' + entry['description'] if entry.get('description') else ''}"
    if key == "dependency_cycles":
        nodes = entry.get("nodes") or []
        return " -> ".join(str(n) for n in nodes) or f"cycle of {entry.get('length', '?')}"
    if key == "governance_risk":
        # ``reason`` alone is a bare enum-ish token; the title is what names
        # the decision the change runs into.
        return f"{entry.get('title') or entry.get('decision_id') or '?'} ({entry.get('reason')})"
    return str(entry)


def _print_records(label: str, values: list, key: str, truncated: int = 0) -> None:
    """A directive block whose entries are dicts, one line each."""
    from rich.markup import escape

    if not values:
        return
    console.print(f"  [bold]{label}[/bold]")
    for entry in values:
        text = _record_text(key, entry) if isinstance(entry, dict) else str(entry)
        console.print(f"    {escape(str(text))}")
    if truncated:
        console.print(f"    [dim]... and {truncated} more[/dim]")


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 93 -> '93rd', 11 -> '11th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@click.command("risk")
@click.argument("revspec", required=False, default=None)
@click.option(
    "--path",
    "repo_path",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Path to the git repository (default: current directory).",
)
@click.option(
    "--ext",
    "ext",
    default=None,
    help="Comma-separated file suffixes to count (e.g. '.py' or '.ts,.tsx'). "
    "Default: count every changed file.",
)
@click.option(
    "--baseline",
    "baseline",
    default=200,
    type=click.IntRange(min=0),
    help="Sample this many recent commits to rank the change within the repo "
    "(0 disables; shows only the absolute per-commit model-score band).",
)
@click.option(
    "--exclude",
    "-x",
    "exclude",
    multiple=True,
    metavar="PATTERN",
    help="Gitignore-style pattern to exclude. Repeatable; also applies to the baseline.",
)
@click.option(
    "--target",
    "-t",
    "targets",
    multiple=True,
    metavar="PATH",
    help="Score what history says about these FILES instead of a change. "
    "Repeatable. Reads the index, so the repo must be indexed.",
)
@click.option(
    "--changed-file",
    "changed_files",
    multiple=True,
    metavar="PATH",
    help="With --target: PR mode. The response leads with a directive naming "
    "structural review candidates, missing co-changes/tests, and what to run.",
)
@format_option()
@full_option()
def risk_command(
    revspec: str | None,
    repo_path: str,
    ext: str | None,
    baseline: int,
    exclude: tuple[str, ...],
    targets: tuple[str, ...],
    changed_files: tuple[str, ...],
    fmt: str,
    full: bool,
) -> None:
    """Assess a change's review priority, or the history of named files.

    With no --target this scores REVSPEC (a commit, or a ``base..head``
    range) from its diff. Omit REVSPEC to score your uncommitted work, or
    HEAD when the tree is clean. With --target it reports what the repo's
    history says about the named files, the same as the get_risk MCP tool.
    """
    if targets:
        _target_risk(repo_path, targets, changed_files, fmt, full)
        return
    if changed_files:
        raise click.UsageError("--changed-file needs at least one --target.")
    # ``--full`` means "the complete payload, as JSON" on every command that has
    # it. The REVSPEC path has no tool to be raw about, but it does have a full
    # payload, and silently ignoring the flag would hand a script that asked for
    # JSON a rich table and exit 0.
    fmt = _ta.resolve_format_for(fmt, full)
    extensions = tuple(e.strip() for e in ext.split(",")) if ext else ()
    status = err_console if fmt != "table" else console

    if baseline and fmt == "table":
        status.print(f"[dim]Sampling up to {baseline} recent commits…[/dim]")
    try:
        result = score_live_change(
            repo_path,
            revspec,
            extensions=extensions,
            exclude_patterns=exclude,
            baseline=baseline,
        )
    except Exception as exc:
        # Surface git errors (bad revspec, not a repo) as a clean CLI message.
        raise click.ClickException(
            f"Could not read change {revspec or 'HEAD'!r} in {repo_path}: {exc}"
        ) from exc

    features = result.features
    risk = result.risk
    percentile = result.percentile
    priority = result.priority
    request_excludes = result.request_excludes

    if features.nf == 0:
        status.print(
            f"[yellow]No counted file changes in {features.ref!r} "
            f"(check the revspec, --ext, or exclusion filters).[/yellow]"
        )

    if fmt == "json":
        click.echo(json.dumps(change_risk_payload(result, scales=True), indent=2))
        return

    # Lead with the benchmarked population-relative authority. Without a usable
    # baseline, label the offline absolute band explicitly as the fallback.
    if percentile is not None and priority is not None:
        console.print(
            f"\n[bold]Benchmarked review priority[/bold]: "
            f"{review_priority_classification(priority)} · "
            f"{_ordinal(round(percentile))} percentile of recent commits by size and spread"
        )
        console.print(f"  [dim]{_PRIORITY_LEAD[priority]}.[/dim]")
    else:
        color = {"high": "red", "moderate": "yellow", "low": "green"}[risk.level]
        console.print(
            f"\n[bold]Absolute fallback band[/bold]: [{color}]{risk.level}[/{color}] "
            "(absolute per-commit band — no repo baseline to rank against)"
        )

    # Fix history remains separate evidence about where the change lands.
    if not result.fix_history_available:
        console.print(
            f"\n[bold]Change risk[/bold] for [cyan]{features.ref}[/cyan]: "
            "[yellow]fix history unavailable[/yellow] (the git history walk failed)"
        )
    elif result.hot_files:
        where = (
            f" · {_ordinal(round(result.fix_percentile))} percentile of this repo's recent commits"
            if result.fix_percentile is not None
            else ""
        )
        console.print(
            f"\n[bold]Change risk[/bold] for [cyan]{features.ref}[/cyan]: "
            f"[red]touches files that have broken before[/red]{where}"
        )
    else:
        console.print(
            f"\n[bold]Change risk[/bold] for [cyan]{features.ref}[/cyan]: "
            "[green]no bug-fix history in the files it touches[/green]"
        )
    if result.working_tree:
        console.print("  [dim]Scoring your uncommitted changes, not the last commit.[/dim]")
    if features.subject:
        console.print(f"  [dim]{features.subject}[/dim]")
    if request_excludes:
        console.print(f"  [dim]Excluding {len(request_excludes)} pattern(s).[/dim]")
    console.print(
        f"  +{features.la} / -{features.ld} lines · {features.nf} files · "
        f"{features.nd} dirs · {features.ns} subsystems · "
        f"entropy {features.entropy:.2f} · author exp "
        f"{'unknown' if features.exp is None else features.exp}"
        + ("  [magenta](fix)[/magenta]" if features.is_fix else "")
    )
    # The fix record, named in the headline above and itemized here.
    if result.hot_files:
        hot = Table(show_header=True)
        hot.add_column("File")
        hot.add_column("Lines", justify="right")
        hot.add_column("Prior fixes", justify="right")
        for path, churn, pressure in result.hot_files:
            hot.add_row(path, str(churn), f"{pressure:.1f}")
        console.print(hot)
        console.print(
            "  [dim]Prior fixes are recency-weighted against this change's own date — "
            "one from a year earlier counts a half.[/dim]"
        )

    console.print(
        f"  [dim]Diff-size score: {risk.score:.1f}/10 — how big and spread out the change is, "
        f"not where it lands. Corpus-anchored to a single commit.[/dim]"
    )

    table = Table(title="Diff shape (each driver vs. the model's baseline commit)")
    table.add_column("Driver")
    table.add_column("Value", justify="right")
    table.add_column("Push", justify="right")
    for d in risk.top_drivers:
        push_color = "red" if d.contribution > 0 else "green"
        sign = "+" if d.contribution >= 0 else ""
        table.add_row(
            d.label,
            "-" if d.value is None else f"{d.value:g}",
            f"[{push_color}]{sign}{d.contribution:.2f}[/{push_color}]",
        )
    console.print(table)
