"""``repowise dead-code`` — detect dead and unused code."""

from __future__ import annotations

import json

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    resolve_repo_path,
    run_async,
)


@click.command("dead-code")
@click.argument("path", required=False, type=click.Path(exists=True))
@click.option("--min-confidence", default=0.4, type=float, help="Minimum confidence threshold.")
@click.option("--safe-only", is_flag=True, help="Only show safe_to_delete=True findings.")
@click.option(
    "--kind",
    type=click.Choice(["unreachable_file", "unused_export", "unused_internal", "zombie_package"]),
    help="Filter by finding kind.",
)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json", "md"]),
    help="Output format.",
)
def dead_code_command(
    path: str | None,
    min_confidence: float,
    safe_only: bool,
    kind: str | None,
    fmt: str,
) -> None:
    """Detect dead and unused code."""
    from pathlib import Path as PathlibPath

    from repowise.core.analysis.dead_code import DeadCodeAnalyzer
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    repo_path = resolve_repo_path(path)

    console.print(f"[bold]repowise dead-code[/bold] — {repo_path}")

    # Ingest
    traverser = FileTraverser(repo_path)
    file_infos = list(traverser.traverse())
    parser = ASTParser()
    graph_builder = GraphBuilder()

    for fi in file_infos:
        try:
            source = PathlibPath(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            graph_builder.add_file(parsed)
        except Exception:
            pass
    graph_builder.build()

    # Git metadata (best effort)
    git_meta_map: dict = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        git_indexer = GitIndexer(repo_path)
        _, metadata_list = run_async(git_indexer.index_repo(""))
        git_meta_map = {m["file_path"]: m for m in metadata_list}
    except Exception:
        pass

    # Analyze
    config = {"min_confidence": min_confidence}
    if kind:
        # Enable only the requested kind
        config["detect_unreachable_files"] = kind == "unreachable_file"
        config["detect_unused_exports"] = kind == "unused_export"
        config["detect_unused_internals"] = kind == "unused_internal"
        config["detect_zombie_packages"] = kind == "zombie_package"

    analyzer = DeadCodeAnalyzer(graph_builder.graph(), git_meta_map)
    report = analyzer.analyze(config)

    findings = report.findings
    if safe_only:
        findings = [f for f in findings if f.safe_to_delete]

    if fmt == "json":
        output = []
        for f in findings:
            output.append({
                "kind": f.kind.value,
                "file_path": f.file_path,
                "symbol_name": f.symbol_name,
                "confidence": f.confidence,
                "reason": f.reason,
                "safe_to_delete": f.safe_to_delete,
                "lines": f.lines,
                "primary_owner": f.primary_owner,
            })
        click.echo(json.dumps(output, indent=2))
        return

    if fmt == "md":
        click.echo(f"# Dead Code Report\n")
        click.echo(f"**Total findings:** {len(findings)}")
        click.echo(f"**Deletable lines:** {report.deletable_lines}\n")
        for f in findings:
            safe = " (safe to remove)" if f.safe_to_delete else ""
            name = f"`{f.symbol_name}`" if f.symbol_name else f"`{f.file_path}`"
            click.echo(f"- [{f.kind.value}] {name} — {f.reason} ({f.confidence:.0%}){safe}")
        return

    # Table format (default)
    table = Table(title=f"Dead Code ({len(findings)} findings)")
    table.add_column("Kind", style="cyan")
    table.add_column("File / Symbol")
    table.add_column("Confidence", justify="right")
    table.add_column("Safe?", justify="center")
    table.add_column("Lines", justify="right")
    table.add_column("Reason")

    for f in findings:
        name = f.symbol_name or f.file_path
        safe = "[green]✓[/green]" if f.safe_to_delete else "[red]✗[/red]"
        table.add_row(
            f.kind.value,
            name,
            f"{f.confidence:.0%}",
            safe,
            str(f.lines),
            f.reason[:60],
        )

    console.print(table)
    console.print(
        f"\nDeletable lines: [bold]{report.deletable_lines:,}[/bold] "
        f"(confidence: {report.confidence_summary})"
    )
