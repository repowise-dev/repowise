"""``repowise health`` says when a file's score has almost no history half."""

from __future__ import annotations

import io
from unittest.mock import patch

from rich.console import Console

from repowise.cli.commands.health_cmd import command as health_module
from repowise.core.analysis.health.models import HealthFileMetricData


def _metric(path: str) -> HealthFileMetricData:
    return HealthFileMetricData(
        file_path=path, score=7.0, max_ccn=3, max_nesting=1, nloc=40,
        has_test_file=False, defect_score=7.0,
    )


def test_json_rows_carry_history_short_only_for_thin_history() -> None:
    short = {"src/new.py"}
    assert health_module._json_metric(_metric("src/new.py"), short)["history"] == "short"
    assert "history" not in health_module._json_metric(_metric("src/old.py"), short)


def test_table_marks_the_score_and_explains_the_mark() -> None:
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch.object(health_module, "console", console):
        health_module._render_files_table([_metric("src/new.py")], {"src/new.py"})
        marked = console.file.getvalue()
        console.file = io.StringIO()
        health_module._render_files_table([_metric("src/old.py")], {"src/new.py"})
        plain = console.file.getvalue()
    assert "7.00*" in marked and "Short history" in marked
    assert "*" not in plain and "Short history" not in plain
