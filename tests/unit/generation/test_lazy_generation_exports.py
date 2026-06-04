"""Guard tests for the lazy (PEP 562) ``repowise.core.generation`` exports.

The index-only update path imports light leaves (``generation.report``) and
must not pay for the assembler/generator stack via the package ``__init__``.
"""

from __future__ import annotations

import subprocess
import sys

import repowise.core.generation as generation


def test_all_exports_resolve():
    for name in generation.__all__:
        assert getattr(generation, name) is not None, name


def test_dir_includes_lazy_exports():
    listing = dir(generation)
    assert "PageGenerator" in listing
    assert "GenerationConfig" in listing


def test_unknown_attribute_raises():
    try:
        generation.does_not_exist
    except AttributeError as exc:
        assert "does_not_exist" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected AttributeError")


def test_light_leaf_import_does_not_load_heavy_stack():
    """``generation.report`` (index-only update path) must not pull the
    assembler/generator/editor_files stack through the package ``__init__``."""
    code = (
        "import sys; import repowise.core.generation.report; "
        "heavy = [m for m in sys.modules if m.startswith('repowise.core.generation.') "
        "and m.split('.')[3] in ('context_assembler', 'page_generator', 'editor_files')]; "
        "sys.exit(1 if heavy else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_lazy_attribute_access_imports_on_demand():
    code = (
        "from repowise.core.generation import PageGenerator; "
        "import sys; "
        "assert 'repowise.core.generation.page_generator' in sys.modules; "
        "assert PageGenerator.__name__ == 'PageGenerator'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
