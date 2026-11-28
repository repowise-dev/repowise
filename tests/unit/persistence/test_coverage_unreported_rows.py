"""The coverage summary's unreported-files rule over plain rows (#5873).

A host holding health rows and a report's paths, with no session, gets the
same list the stored summary names.
"""

from __future__ import annotations

from repowise.core.persistence.crud.analysis.coverage import unreported_production_files


def _row(path: str, origin: str | None = "production", nloc: int = 10) -> dict:
    return {"file_path": path, "code_origin": origin, "nloc": nloc}


def test_unreported_production_files_over_plain_rows() -> None:
    metrics = [
        _row("src/b.py"),
        _row("src/a.py"),
        _row("src/covered.py"),
        _row("src/empty/__init__.py", nloc=0),
        _row("src/gen/out.py", origin="generated"),
        _row("tests/test_a.py", origin="test"),
        _row("web/app.ts"),  # a type the report never measured
        _row("src/skip/me.py"),  # the ingest's coverage.ignore
        _row("tests/test_legacy.py", origin=None),  # no stored origin: the path decides
        _row("src/legacy.py", origin=None),
    ]
    assert unreported_production_files(
        metrics, ["src/covered.py"], ignore=["src/skip/"]
    ) == ["src/a.py", "src/b.py", "src/legacy.py"]


def test_nothing_reported_means_nothing_is_measured() -> None:
    assert unreported_production_files([_row("src/a.py")], []) == []
