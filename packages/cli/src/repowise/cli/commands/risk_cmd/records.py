"""Directive blocks whose entries are dicts, one line per entry."""

from __future__ import annotations

from rich.markup import escape

from repowise.cli.helpers import console

#: The dict-entry directive blocks. Each is built in ``tool_risk/directives.py``
#: with its own key set, so each gets its own formatter in ``_record_text``.
_DIRECTIVE_RECORD_BLOCKS = (
    ("Consumer structural reach", "will_break_consumers"),
    ("Missing cross-repo co-changes", "missing_cross_repo_cochanges"),
    ("Breaking contract changes", "breaking_changes"),
    ("Conformance violations", "conformance_violations"),
    ("Dependency cycles", "dependency_cycles"),
    ("Governance risk", "governance_risk"),
)


def _consumer_text(entry: dict) -> str:
    where = f" [{entry.get('repo')}]" if entry.get("repo") else ""
    return f"{entry.get('service') or entry.get('repo') or '?'}{where}"


def _breaking_change_text(entry: dict) -> str:
    consumers = entry.get("impacted_consumers") or []
    who = f" — endangers {len(consumers)} consumer(s)" if consumers else ""
    return (
        f"{entry.get('contract_id') or '?'}: {entry.get('kind') or '?'}"
        f" ({entry.get('severity') or '?'}) {entry.get('detail') or ''}{who}".rstrip()
    )


def _conformance_text(entry: dict) -> str:
    rule = entry.get("rule") or f"{entry.get('source')} -> {entry.get('target')}"
    return f"{rule}{': ' + entry['description'] if entry.get('description') else ''}"


def _cycle_text(entry: dict) -> str:
    nodes = entry.get("nodes") or []
    return " -> ".join(str(n) for n in nodes) or f"cycle of {entry.get('length', '?')}"


def _governance_text(entry: dict) -> str:
    # The title names the decision; ``reason`` alone is a bare token.
    return f"{entry.get('title') or entry.get('decision_id') or '?'} ({entry.get('reason')})"


_RECORD_FORMATTERS = {
    "will_break_consumers": _consumer_text,
    "missing_cross_repo_cochanges": _consumer_text,
    "breaking_changes": _breaking_change_text,
    "conformance_violations": _conformance_text,
    "dependency_cycles": _cycle_text,
    "governance_risk": _governance_text,
}


def _record_text(key: str, entry: dict) -> str:
    """One directive entry as a line, per the shape its block is built with."""
    formatter = _RECORD_FORMATTERS.get(key)
    return formatter(entry) if formatter else str(entry)


def _print_records(label: str, values: list, key: str, truncated: int = 0) -> None:
    """A directive block whose entries are dicts, one line each."""
    if not values:
        return
    console.print(f"  [bold]{label}[/bold]")
    for entry in values:
        text = _record_text(key, entry) if isinstance(entry, dict) else str(entry)
        console.print(f"    {escape(str(text))}")
    if truncated:
        console.print(f"    [dim]... and {truncated} more[/dim]")
