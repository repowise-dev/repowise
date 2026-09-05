"""The portable health read models stay cheap to import.

``analysis/health/__init__`` used to bind the analyzer engine eagerly, so an
import of the record adapters or of any pure fold under this package dragged in
ingestion, git blame and, through them, persistence — which is why the crud
layer had to defer its own imports back out of ``analysis.health`` to keep the
cycle from closing.

The engine now binds on first attribute access, and ``scoring`` keeps its one
biomarker import behind a ``TYPE_CHECKING`` gate. A consumer that only wants the
ranking, the folds or the trend arithmetic pays for none of it: the seven modules
below reach fewer than ten repowise modules between them, against the 419 and a
full set of tree-sitter grammars before. This is a guard, not a benchmark: it
fails when someone reintroduces an eager import, and says nothing about speed.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

_LIGHTWEIGHT = (
    "repowise.core.analysis.health.rows",
    "repowise.core.analysis.health.ranking",
    "repowise.core.analysis.health.aggregation",
    "repowise.core.analysis.health.trends",
    "repowise.core.analysis.health.grading",
    "repowise.core.analysis.health.suggestions",
    "repowise.core.analysis.health.defect_accuracy",
)

# Loading any of these means the seam stopped being portable: a downstream
# consumer holding plain records would be made to install a web framework, a
# database driver, a parser toolchain, or the whole analyzer to read a number.
#
# ``tree_sitter`` and the ingestion package are on the list because they are the
# heaviest tail and the easiest to reintroduce by accident: one runtime import
# of a biomarker type for an annotation pulls in every grammar. That is exactly
# what a `TYPE_CHECKING` gate in ``scoring`` currently prevents, and removing it
# has to fail here rather than in a downstream consumer's install.
_FORBIDDEN = ("fastapi", "sqlalchemy", "pydantic", "tree_sitter")
_FORBIDDEN_REPOWISE = (
    "repowise.core.persistence",
    "repowise.core.ingestion",
    "repowise.core.analysis.health.biomarkers",
    "repowise.core.analysis.health.engine",
    "repowise.server",
)


def _loaded_after_importing(module: str) -> set[str]:
    """Module names present in a fresh interpreter after importing *module*."""
    script = textwrap.dedent(
        f"""
        import importlib, sys
        importlib.import_module({module!r})
        print("\\n".join(sorted(sys.modules)))
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return set(done.stdout.split())


@pytest.mark.parametrize("module", _LIGHTWEIGHT)
def test_the_read_models_import_without_the_heavy_layers(module: str) -> None:
    loaded = _loaded_after_importing(module)
    offenders = sorted(
        name
        for name in loaded
        if name in _FORBIDDEN or name.startswith(_FORBIDDEN_REPOWISE)
    )
    assert offenders == [], f"{module} pulled in {offenders}"


def test_the_engine_is_still_reachable_from_the_package() -> None:
    """Deferring the import must not remove the export."""
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION, HealthAnalyzer

    assert HealthAnalyzer.__name__ == "HealthAnalyzer"
    assert isinstance(HEALTH_ANALYZER_VERSION, int)


def test_an_unknown_package_attribute_still_raises_attribute_error() -> None:
    import repowise.core.analysis.health as health

    with pytest.raises(AttributeError):
        _ = health.NotAThing
