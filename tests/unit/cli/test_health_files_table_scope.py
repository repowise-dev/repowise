"""``repowise health`` ranks the files Fix first keeps in scope, by stored origin."""

from __future__ import annotations

import io
from unittest.mock import patch

from rich.console import Console

from repowise.cli.commands.health_cmd import command as health_module
from repowise.core.analysis.health.models import HealthFileMetricData


def _metric(path: str, score: float, origin: str, *, is_test: bool = False) -> HealthFileMetricData:
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=3,
        max_nesting=1,
        nloc=40,
        has_test_file=False,
        defect_score=score,
        is_test=is_test,
        code_origin=origin,
    )


def test_a_vendored_header_the_path_reads_as_production_is_not_ranked() -> None:
    # The path alone says production; the stored origin saw the license banner.
    rows = [
        _metric("backend/include/nlohmann/json.hpp", 1.0, "vendored"),
        _metric("tests/test_app.py", 2.0, "test", is_test=True),
        _metric("app/main.py", 4.0, "production"),
    ]
    kept, left_out = health_module._ranked_rows(rows, narrowed=False)
    assert [m.file_path for m in kept] == ["app/main.py"]
    assert left_out == {"vendored": 1, "test": 1}
    # Files the reader named are all listed.
    assert health_module._ranked_rows(rows, narrowed=True) == (rows, {})

    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch.object(health_module, "console", console):
        health_module._render_files_table(kept, set(), left_out)
    out = console.file.getvalue()
    assert "json.hpp" not in out and "app/main.py" in out
    assert "Not ranked here: 1 test, 1 vendored" in out
