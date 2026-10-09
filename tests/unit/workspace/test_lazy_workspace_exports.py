"""Guard tests for the lazy (PEP 562) ``repowise.core.workspace`` exports.

``repowise mcp`` reaches ``workspace.config`` — a yaml-only leaf — on every
invocation, and must not pay for the analysis subsystem (sqlalchemy via
``registry``, networkx via ``architecture_metrics``, the ingestion graph via
``update``) through the package ``__init__``.
"""

from __future__ import annotations

import subprocess
import sys
from unittest import mock

import pytest

import repowise.core.workspace as workspace

#: Submodules the startup path must not drag in via the package ``__init__``.
HEAVY_SUBMODULES = ("registry", "architecture_metrics", "update", "contracts", "system_graph")


def _probe(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a clean interpreter, where import state is not shared."""
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_all_exports_resolve():
    for name in workspace.__all__:
        assert getattr(workspace, name) is not None, name


def test_dir_includes_lazy_exports():
    listing = dir(workspace)
    assert "WorkspaceConfig" in listing
    assert "find_workspace_root" in listing


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError, match="does_not_exist"):
        _ = workspace.does_not_exist


def test_submodule_import_still_resolves():
    """``from pkg import submodule`` needs ``__getattr__`` to raise AttributeError."""
    from repowise.core.workspace import contracts

    assert contracts.__name__ == "repowise.core.workspace.contracts"


def test_package_attribute_is_patchable():
    """Callers do a function-local ``from repowise.core.workspace import X``,
    so patching the package attribute must still intercept them."""
    sentinel = object()
    with mock.patch("repowise.core.workspace.scan_for_repos", sentinel):
        from repowise.core.workspace import scan_for_repos

        assert scan_for_repos is sentinel

    from repowise.core.workspace import scan_for_repos as restored

    assert restored is not sentinel


def test_config_import_does_not_load_analysis_stack():
    result = _probe(
        "import sys; import repowise.core.workspace.config; "
        f"loaded = [n for n in {HEAVY_SUBMODULES!r} "
        "if f'repowise.core.workspace.{n}' in sys.modules]; "
        "print('loaded:', loaded); sys.exit(1 if loaded else 0)"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lazy_attribute_access_imports_on_demand():
    result = _probe(
        "import sys; import repowise.core.workspace as w; "
        "assert 'repowise.core.workspace.scanner' not in sys.modules; "
        "assert w.scan_for_repos.__name__ == 'scan_for_repos'; "
        "assert 'repowise.core.workspace.scanner' in sys.modules"
    )
    assert result.returncode == 0, result.stderr
