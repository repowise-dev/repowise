"""``repowise coverage suggest-gates``: propose path-scoped gates for ``coverage.gates``.

Reads, in order, CODEOWNERS, the repository's top-level packages (from
``git ls-files``) and, when an index exists, the graph's communities. Prints a
YAML block to paste directly below the ``coverage:`` line; it never writes
config and sets no threshold. The first two sources need no index.
"""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.ci import repo_root
from repowise.cli.helpers import repo_index_session, run_async
from repowise.cli.output import emit_json

_UNREADABLE = "index unreadable, so none suggested"


@click.command("suggest-gates")
@click.option(
    "--path",
    "repo",
    default=None,
    help="A path inside the repository (defaults to cwd).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    show_default=True,
    help="yaml prints a block to paste below coverage:; json the same proposals, by source.",
)
def coverage_suggest_gates(repo: str | None, fmt: str) -> None:
    """Propose path-scoped gates (coverage.gates) for this repository.

    Each gate judges the patch coverage of the changed files its globs match,
    in ``repowise coverage check``. Suggestions come from CODEOWNERS (one gate
    per owner), top-level packages, and graph communities when the repository
    is indexed. Nothing is written: paste what you keep directly below the
    ``coverage:`` line in .repowise/config.yaml and give each gate a fail_under.

    Examples:

        repowise coverage suggest-gates
        repowise coverage suggest-gates --format json
    """
    from repowise.core.analysis.patch_coverage.suggest import render_yaml, unique_names

    root = repo_root(repo)
    layout = _layout(root)
    sources = [_codeowners(root), layout, _graph(root, {g.paths for g in layout.gates})]
    unique_names(sources)
    if fmt == "json":
        emit_json({"sources": [s.to_dict() for s in sources]})
    else:
        click.echo(render_yaml(sources), nl=False)


def _codeowners(root: Path):
    from repowise.core.analysis.patch_coverage.suggest import (
        CODEOWNERS_PATHS,
        GateSource,
        codeowners_gates,
    )

    for rel in CODEOWNERS_PATHS:
        path = root / rel
        if path.is_file():
            gates = codeowners_gates(path.read_text(encoding="utf-8", errors="replace"))
            detail = rel if gates else f"{rel} names no owned paths"
            return GateSource("codeowners", detail, gates)
    return GateSource("codeowners", "no CODEOWNERS file")


def _layout(root: Path):
    from repowise.core import git_refs
    from repowise.core.analysis.patch_coverage.suggest import GateSource, layout_gates

    gates = layout_gates(git_refs.tracked_paths(str(root)))
    return GateSource("layout", "git ls-files" if gates else "no source directories found", gates)


def _graph(root: Path, layout_paths: set[tuple[str, ...]]):
    """Community gates, minus any whose globs a layout gate already has."""
    from repowise.core.analysis.patch_coverage.suggest import (
        MIN_COMMUNITY_FILES,
        GateSource,
        community_gates,
    )

    read = run_async(_read_graph(root))
    if isinstance(read, str):
        return GateSource("graph", read)
    communities, commit = read
    at = f"the index at {commit[:7]}" if commit else "the index"
    # One id for every file: the index never computed communities.
    if len(set(communities.values())) <= 1:
        return GateSource("graph", f"{at} has no communities computed")
    gates, cut = community_gates(communities, _file_sizes(root, communities))
    gates = [g for g in gates if g.paths not in layout_paths]
    if not gates:
        return GateSource(
            "graph",
            f"{at}: no community of {MIN_COMMUNITY_FILES}+ source files beyond the packages",
        )
    return GateSource("graph", at + (f"; {cut} smaller communities not shown" if cut else ""), gates)


def _file_sizes(root: Path, files) -> dict[str, int]:
    """Bytes per file on disk, for naming a root-level community; a missing file is left out."""
    sizes: dict[str, int] = {}
    for rel in files:
        try:
            sizes[rel] = (root / rel).stat().st_size
        except OSError:
            continue
    return sizes


async def _read_graph(root: Path) -> tuple[dict[str, int], str | None] | str:
    """``({file: community_id}, indexed commit)``, or why the index could not answer."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence.crud.graph import get_all_file_metrics
    from repowise.core.persistence.crud.repository import get_repository
    from repowise.core.persistence.database import has_db_store

    if not has_db_store(root):
        return "unavailable, no index (run `repowise init` to add them)"
    async with repo_index_session(root) as opened:
        if opened is None:
            return _UNREADABLE
        session, repo_id = opened
        try:
            nodes = await get_all_file_metrics(session, repo_id)
            repo = await get_repository(session, repo_id)
        except SQLAlchemyError:
            return _UNREADABLE
    communities = {n.file_path or n.node_id: n.community_id for n in nodes if not n.is_test}
    return communities, repo.head_commit if repo else None
