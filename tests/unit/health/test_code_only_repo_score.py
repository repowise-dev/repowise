"""A repository's score counts code files only.

Data, markup and config files (JSON, YAML, Markdown and the rest the language
registry does not declare as code) get no metric row, so they cannot lift the
average. A big generated JSON spec used to sit in the average as a flat 10.0
weighted by its line count. And a repository with nothing but such files has
no code to score, so it reads "no score", never a perfect 10.0.
"""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.scoring import compute_kpis
from repowise.core.analysis.health.trends import snapshot_fields

# A long, deeply nested function with many branches, so the file scores well
# below 10 and a 10.0 data file in the average would visibly lift it.
_BRANCHES = "\n".join(
    f"        {'if' if i == 0 else 'elif'} x == {i}:\n"
    "            for j in range(y):\n"
    "                if j % 2:\n"
    "                    while z:\n"
    f"                        z -= {i}"
    for i in range(40)
)
_CODE = f"def tangled(x, y, z, a, b, c, d, e, f):\n    total = 0\n    if a:\n{_BRANCHES}\n    return total\n"


def _parsed(tmp_path, rel: str, language: str, body: str) -> SimpleNamespace:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return SimpleNamespace(
        file_info=SimpleNamespace(path=rel, abs_path=str(path), language=language, is_test=False),
        symbols=[],
    )


def _big_json(tmp_path) -> SimpleNamespace:
    entries = ",\n".join(f'  "key_{i}": {{"value": {i}}}' for i in range(5_000))
    return _parsed(tmp_path, "swagger/swagger.json", "json", "{\n" + entries + "\n}\n")


def _analyze(tmp_path, files: list[SimpleNamespace]):
    return HealthAnalyzer(graph=nx.DiGraph(), parsed_files=files, repo_root=tmp_path).analyze()


def test_a_big_json_file_does_not_lift_the_code_files_score(tmp_path) -> None:
    code = _parsed(tmp_path, "src/tangled.py", "python", _CODE)
    report = _analyze(tmp_path, [_big_json(tmp_path), code])

    by_path = {m.file_path: m for m in report.metrics}
    if "src/tangled.py" not in by_path or by_path["src/tangled.py"].score is None:
        pytest.skip("python tree-sitter pack missing")
    assert "swagger/swagger.json" not in by_path
    assert report.kpis["average_health"] == by_path["src/tangled.py"].score
    assert report.kpis["average_health"] < 10.0


def test_a_repository_of_only_data_files_has_no_score(tmp_path) -> None:
    files = [
        _big_json(tmp_path),
        _parsed(tmp_path, "README.md", "markdown", "# Title\n\nSome prose.\n"),
        _parsed(tmp_path, "config/app.yaml", "yaml", "name: app\nreplicas: 3\n"),
    ]
    report = _analyze(tmp_path, files)

    assert report.metrics == []
    assert report.kpis["average_health"] is None
    assert report.kpis["hotspot_health"] is None
    # No trend point for a number nobody measured.
    assert snapshot_fields(report.kpis, [], []) is None


def test_no_scored_rows_means_no_score() -> None:
    kpis = compute_kpis([], set())
    assert kpis["average_health"] is None
    assert kpis["hotspot_health"] is None
    assert kpis["file_count"] == 0
