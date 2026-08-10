"""Non-interactive output must not lose information, and must be parseable.

Rich sizes a non-terminal ``Console`` at 80 columns, which is *narrower* than
any real terminal. So the consumer that cannot ask for the rest — an agent, a
pipe, CI — was the one getting truncated hardest: ``repowise search`` rendered
every result path as ``packages/core…``, which cannot be opened or grepped and
cannot even be recognised as truncated without counting characters. Measured on
this repo before the fix: 28 ellipses at the non-TTY default, and still 14 at
``COLUMNS=200`` (the width the session-cost eval pinned as its mitigation).

Widening stops the ellipsis, but a rich table is still not a machine format, so
``search`` also grew ``--format json``. These tests pin both halves.
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest
from rich.console import Console
from rich.table import Table

from repowise.cli.commands.search_cmd import (
    _answered_mode,
    _display_results,
    _display_results_multi,
    _render_symbol_rows,
)
from repowise.cli.output import NON_TTY_WIDTH, emit_json, resolve_console_width

LONG_PATH = "packages/core/src/repowise/core/persistence/vector_store/lancedb_store.py"


class _Stream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class _Result:
    """Minimal stand-in for a wiki-page search hit."""

    def __init__(self, path: str = LONG_PATH) -> None:
        self.score = 15.198
        self.title = "Symbol: LanceDBVectorStore"
        self.page_type = "symbol_spotlight"
        self.target_path = path
        self.snippet = "# a snippet that is comfortably longer than fifty characters wide"


# ---------------------------------------------------------------------------
# Width policy
# ---------------------------------------------------------------------------


def test_non_tty_gets_an_explicit_wide_width() -> None:
    assert resolve_console_width(_Stream(tty=False)) == NON_TTY_WIDTH


def test_terminal_is_left_to_rich() -> None:
    assert resolve_console_width(_Stream(tty=True)) is None


def test_explicit_columns_wins_over_the_policy(monkeypatch) -> None:
    """An operator (or a benchmark pinning a width) keeps control."""
    monkeypatch.setenv("COLUMNS", "120")
    assert resolve_console_width(_Stream(tty=False)) is None


def test_stream_that_cannot_answer_isatty_is_treated_as_non_tty() -> None:
    class Broken:
        def isatty(self):
            raise ValueError("detached")

    assert resolve_console_width(Broken()) == NON_TTY_WIDTH


# ---------------------------------------------------------------------------
# The regression itself: a piped table must keep its paths whole
# ---------------------------------------------------------------------------


def _render_table(width: int | None) -> str:
    buf = io.StringIO()
    table = Table(title="Full-text search")
    for column in ("Score", "Title", "Type", "Path", "Snippet"):
        table.add_column(column)
    for _ in range(3):
        table.add_row("15.198", "Symbol: LanceDBVectorStore", "symbol_spotlight", LONG_PATH, "#")
    Console(file=buf, width=width, no_color=True).print(table)
    return buf.getvalue()


def test_piped_table_keeps_every_path_intact() -> None:
    rendered = _render_table(NON_TTY_WIDTH)
    assert LONG_PATH in rendered
    assert "…" not in rendered


def test_the_old_default_width_is_what_destroyed_the_paths() -> None:
    """Guards the premise: at rich's non-TTY default the path is unusable."""
    rendered = _render_table(80)
    assert LONG_PATH not in rendered
    assert "…" in rendered


# ---------------------------------------------------------------------------
# Machine-readable output
# ---------------------------------------------------------------------------


def test_emit_json_round_trips(capsys) -> None:
    emit_json({"a": 1, "nested": [{"b": 2}]})
    assert json.loads(capsys.readouterr().out) == {"a": 1, "nested": [{"b": 2}]}


def test_emit_json_degrades_unserialisable_values_instead_of_raising(capsys) -> None:
    from decimal import Decimal

    emit_json({"score": Decimal("1.5")})
    assert json.loads(capsys.readouterr().out) == {"score": "1.5"}


def test_search_json_emits_a_document_even_with_no_results(capsys) -> None:
    """A consumer that gets nothing on stdout cannot tell success from a crash."""
    _display_results([], "ignored", "json", query="nothing", mode="fulltext")

    assert json.loads(capsys.readouterr().out) == {
        "query": "nothing",
        "mode": "fulltext",
        "results": [],
    }


def test_search_json_carries_whole_paths_and_declares_its_mode(capsys) -> None:
    _display_results([_Result()], "ignored", "json", query="lancedb", mode="fulltext")

    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "lancedb"
    assert payload["mode"] == "fulltext"
    assert payload["results"][0]["path"] == LONG_PATH


def test_search_json_does_not_clip_the_snippet(capsys) -> None:
    """The 50-char clip is there to fit a column; JSON has no column."""
    result = _Result()
    _display_results([result], "ignored", "json", query="q", mode="fulltext")

    assert json.loads(capsys.readouterr().out)["results"][0]["snippet"] == result.snippet


def test_multi_repo_json_labels_each_row_with_its_repo(capsys) -> None:
    _display_results_multi(
        [("api", _Result()), ("web", _Result())],
        "ignored",
        "json",
        query="q",
        mode="fulltext",
    )

    rows = json.loads(capsys.readouterr().out)["results"]
    assert [r["repo"] for r in rows] == ["api", "web"]


def test_symbol_json_omits_repo_in_single_repo_mode(capsys) -> None:
    row = ("LanceDBVectorStore", "a.b.LanceDBVectorStore", "class", LONG_PATH, 60)
    _render_symbol_rows([(None, row)], "lancedb", 10, "json", multi=False)

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "symbol"
    assert payload["results"] == [
        {
            "name": "LanceDBVectorStore",
            "qualified_name": "a.b.LanceDBVectorStore",
            "kind": "class",
            "path": LONG_PATH,
            "line": 60,
        }
    ]


def test_symbol_json_labels_the_repo_in_workspace_mode(capsys) -> None:
    row = ("LanceDBVectorStore", "a.b.LanceDBVectorStore", "class", LONG_PATH, 60)
    _render_symbol_rows([("api", row)], "lancedb", 10, "json", multi=True)

    assert json.loads(capsys.readouterr().out)["results"][0]["repo"] == "api"


def test_shared_console_actually_applies_the_policy() -> None:
    """The seam itself, not just the function that computes the width.

    ``output.py`` can be perfectly correct while ``helpers.py`` forgets to use
    it, which would leave every table truncated exactly as before.
    """
    if os.environ.get("COLUMNS"):
        pytest.skip("COLUMNS is pinned here, and the policy defers to it by design")

    from repowise.cli import helpers

    assert not sys.stdout.isatty(), "pytest captures stdout; this test assumes a non-TTY"
    assert helpers.console.width == NON_TTY_WIDTH


def test_a_degraded_embedder_warns_on_stderr_so_json_stdout_stays_parseable(
    capsys, monkeypatch
) -> None:
    """Regression: this warning used to print to stdout, in front of the JSON.

    ``build_embedder`` is two modules below ``search``, so the command-level
    stderr diversion cannot reach it — the warning has to be on stderr at its
    source. A repo whose embedder key has gone away is the common case for
    ``--mode semantic``, not an exotic one, so this corrupted the payload for
    exactly the users most likely to hit it.
    """
    from repowise.cli import providers

    def _boom(name: str, **kwargs: object):
        raise RuntimeError("no key configured")

    monkeypatch.setattr("repowise.core.providers.embedding.registry.get_embedder", _boom)

    providers.build_embedder("openai")
    emit_json({"query": "auth", "mode": "fulltext", "results": []})

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"query": "auth", "mode": "fulltext", "results": []}
    assert "openai" in captured.err


@pytest.mark.parametrize(
    ("requested", "keyless", "mixed", "expected"),
    [
        # Nothing fell back: the request is the answer.
        ("semantic", [], False, "semantic"),
        # Every repo fell back, so these are FTS rows on an FTS score scale.
        ("semantic", ["api"], False, "fulltext"),
        # Some answered semantically and some did not; neither label is true,
        # and this is the case the ranking code fuses on rank for.
        ("semantic", ["api"], True, "mixed"),
        # Non-semantic requests cannot degrade, so they pass through.
        ("fulltext", ["api"], True, "fulltext"),
        ("symbol", ["api"], True, "symbol"),
    ],
)
def test_answered_mode_reports_what_answered_not_what_was_asked(
    requested: str, keyless: list[str], mixed: bool, expected: str
) -> None:
    assert _answered_mode(requested, keyless, mixed) == expected


def test_symbol_json_honours_the_limit(capsys) -> None:
    row = ("N", "q.N", "class", LONG_PATH, 1)
    _render_symbol_rows([(None, row)] * 5, "n", 2, "json", multi=False)

    assert len(json.loads(capsys.readouterr().out)["results"]) == 2
