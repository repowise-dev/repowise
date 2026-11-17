"""The persistence package resolves every public name without loading it eagerly."""

from __future__ import annotations

import subprocess
import sys

import repowise.core.persistence as persistence
from repowise.core.persistence import crud
from repowise.core.persistence.crud import analysis


def test_every_exported_name_resolves() -> None:
    for name in persistence.__all__:
        assert getattr(persistence, name) is not None, name


def test_all_lists_exactly_the_eager_and_lazy_names() -> None:
    eager = {"Embedder", "MockEmbedder"}
    assert set(persistence.__all__) == eager | set(persistence._LAZY)
    assert set(persistence.__all__) <= set(dir(persistence))


def test_every_analysis_name_resolves_on_the_crud_facade() -> None:
    for name in analysis.__all__:
        assert getattr(crud, name) is getattr(analysis, name), name


def test_an_unknown_name_still_raises() -> None:
    for module in (persistence, crud):
        assert not hasattr(module, "no_such_name")


def _loaded_after(statement: str, module: str) -> bool:
    probe = f"import sys; {statement}; print({module!r} in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    return out == "True"


def test_the_database_module_does_not_load_the_crud_modules() -> None:
    assert not _loaded_after(
        "import repowise.core.persistence.database", "repowise.core.persistence.crud"
    )


def test_a_page_reader_does_not_load_the_analysis_crud() -> None:
    assert not _loaded_after(
        "from repowise.core.persistence.crud import pages",
        "repowise.core.persistence.crud.analysis",
    )


def test_a_dunder_probe_does_not_load_the_analysis_crud() -> None:
    assert not _loaded_after(
        "import repowise.core.persistence.crud as c; hasattr(c, '__wrapped__')",
        "repowise.core.persistence.crud.analysis",
    )


def test_submodules_stay_reachable_as_package_attributes() -> None:
    probe = (
        "import repowise.core.persistence as p, repowise.core.persistence.crud as c; "
        "print(p.crud.__name__, p.database.__name__, c.analysis.__name__)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.split()
    assert out == [
        "repowise.core.persistence.crud",
        "repowise.core.persistence.database",
        "repowise.core.persistence.crud.analysis",
    ]
