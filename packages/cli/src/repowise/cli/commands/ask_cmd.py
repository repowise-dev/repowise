"""``repowise ask`` — the CLI adapter over the ``get_answer`` MCP tool."""

from __future__ import annotations

import click

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.output import emit_json


def project(payload: dict, question: str) -> dict:
    """``get_answer``'s dict, trimmed to what a CLI caller reads.

    Field mapping, kept explicit because the session-cost bake-off cites it:

    ==================  ===========================================
    kept                answer, confidence, retrieval_quality,
                        citations, quotes, fallback_targets,
                        best_guesses, episodes (subject / recorded /
                        still_true), error, index (from ``_meta``)
    dropped             retrieval (a 1,500-char ``excerpt`` per hit,
                        plus ``key_symbols`` — the bulk of the
                        payload), candidates, symbol_bodies, the
                        timing half of ``_meta``
    ==================  ===========================================

    ``quotes`` survives the trim although ``retrieval`` does not: it is the
    literal source line behind a claim at ~120 chars a row, which is what makes
    the answer checkable, while ``retrieval`` is the material synthesis already
    consumed. ``--full`` returns everything.
    """
    out: dict = {
        "question": question,
        "answer": payload.get("answer", ""),
        "confidence": payload.get("confidence", ""),
        "retrieval_quality": payload.get("retrieval_quality", ""),
        "citations": list(payload.get("citations") or []),
    }
    quotes = payload.get("quotes") or []
    if quotes:
        out["quotes"] = [
            {
                "path": q.get("path", ""),
                "lines": q.get("lines"),
                "quote": q.get("quote", ""),
            }
            for q in quotes
        ]
    # Present when confidence is low: one-line guesses instead of an empty
    # answer, and the files to read if none of them lands. Each guess carries
    # the candidate page's whole ``excerpt`` — the same 1,500-char blocks
    # dropped from ``retrieval``, and on a low-confidence answer there are
    # several of them, so the abstain path would otherwise be the *largest*
    # payload this command emits. The justification and the score are what
    # make the guess actionable; the excerpt is one `context` call away.
    if payload.get("best_guesses"):
        out["best_guesses"] = [
            {
                "file": g.get("file", ""),
                "why_relevant": g.get("why_relevant", ""),
                "score": g.get("score"),
            }
            for g in payload["best_guesses"]
        ]
    # Only the abstain path sets these, and they say what to do next.
    for key in ("next_action_hint", "note"):
        if payload.get(key):
            out[key] = payload[key]
    if payload.get("fallback_targets"):
        out["fallback_targets"] = list(payload["fallback_targets"])
    episodes = payload.get("episodes") or []
    if episodes:
        out["episodes"] = [
            {
                "subject": e.get("subject", ""),
                "recorded": e.get("recorded", ""),
                "still_true": e.get("still_true", ""),
            }
            for e in episodes
        ]
    note = _ta.index_note(payload)
    if note:
        out["index"] = note
    return out


@click.command("ask")
@click.argument("question")
@_ta.target_options
def ask_command(
    question: str,
    path: str | None,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
    full: bool,
) -> None:
    """Answer a question about this codebase, with citations.

    The same synthesis the get_answer MCP tool performs: hybrid retrieval
    followed by an LLM answer over what it found, so this command costs an
    LLM call where the other read-only commands do not.
    """
    fmt = _ta.resolve_format_for(fmt, full)
    repo_path = _ta.resolve_indexed_repo(
        path=path,
        repo_alias=repo_alias,
        no_workspace=no_workspace,
        fmt=fmt,
        command="ask",
    )

    def _factory():
        from repowise.server.mcp_server.tool_answer import get_answer

        return get_answer(question=question)

    payload = _ta.run(repo_path, _factory)

    if full:
        emit_json(payload)
        return
    _ta.emit_error(payload, fmt, extra={"question": question})
    projected = project(payload, question)
    if fmt == "json":
        emit_json(projected)
        return
    _render(projected)
    _ta.print_index_note(payload, fmt)


def _render(projected: dict) -> None:
    from repowise.cli.helpers import console

    answer = projected.get("answer") or ""
    if answer:
        # click.echo, not rich Markdown. The answer is already markdown, and
        # rendering it centres and pads every heading to the console width —
        # which phase 1 deliberately set to 400 for a non-TTY, so a piped
        # answer would carry hundreds of chars of padding per heading and lose
        # the markdown a caller may want to keep. Echoing the source is the
        # exact bytes, greppable, and reads fine in a terminal.
        click.echo(answer)
    else:
        console.print("[yellow]No answer synthesised for that question.[/yellow]")

    confidence = projected.get("confidence") or "?"
    quality = projected.get("retrieval_quality") or "?"
    colour = {"high": "green", "medium": "yellow"}.get(confidence, "red")
    console.print(
        f"\n[{colour}]confidence: {confidence}[/{colour}]  [dim]retrieval: {quality}[/dim]"
    )

    guesses = projected.get("best_guesses") or []
    if guesses:
        console.print("\n[bold]Best guesses[/bold]")
        for g in guesses:
            console.print(
                f"  [cyan]{g.get('file', '')}[/cyan] [dim]({g.get('score', '?')})[/dim] "
                f"{g.get('why_relevant', '')}"
            )
    if projected.get("next_action_hint"):
        # The tools write their hints for an agent holding the MCP surface, so
        # names like get_context land in front of someone who cannot run one.
        console.print(f"\n[bold]Next[/bold] {_ta.as_cli_prose(projected['next_action_hint'])}")

    for label, key in (("Citations", "citations"), ("Read next", "fallback_targets")):
        values = projected.get(key) or []
        if values:
            console.print(f"\n[bold]{label}[/bold]")
            for value in values:
                console.print(f"  {value}")

    quotes = projected.get("quotes") or []
    if quotes:
        console.print("\n[bold]Evidence[/bold]")
        for q in quotes:
            lines = q.get("lines") or []
            where = f":{lines[0]}" if lines else ""
            console.print(f"  [cyan]{q.get('path', '')}{where}[/cyan]  {q.get('quote', '')}")

    episodes = projected.get("episodes") or []
    if episodes:
        console.print("\n[bold]Recorded about this checkout[/bold]")
        for e in episodes:
            console.print(f"  {e.get('subject', '')}: {e.get('recorded', '')}")
            if e.get("still_true"):
                console.print(f"    [dim]{e['still_true']}[/dim]")
