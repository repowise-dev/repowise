"""A change to vendored code is held back from the health delta by reason,
as every other health surface holds its findings back."""

from __future__ import annotations

from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest

from .conftest import python_complex


def test_a_vendored_file_is_excluded_by_reason(make_repo):
    repo = make_repo()
    repo.commit("seed", {"vendor/lib.py": "x = 1\n", "app.py": "y = 1\n"})
    repo.commit(
        "edit",
        {"vendor/lib.py": python_complex("tangle", 16), "app.py": python_complex("knot", 16)},
    )

    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    delta = service.compare(DeltaRequest(str(repo.path), "HEAD"))

    assert delta.skipped.get("vendor/lib.py") == "vendored"
    assert {f.path for f in delta.findings} == {"app.py"}
