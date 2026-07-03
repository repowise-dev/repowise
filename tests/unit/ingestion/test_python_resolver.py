import pytest
from repowise.core.ingestion.models import Import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.python import resolve_python_import, resolve_python_import_all


def test_resolve_python_import_all_submodules():
    path_set = {
        "src/app.py",
        "src/routers/__init__.py",
        "src/routers/workspace.py",
        "src/routers/git/__init__.py",
    }
    
    ctx = ResolverContext(path_set=path_set, stem_map={}, graph=None)
    
    # 1. Single submodule without __init__.py package
    imp = Import(
        module_path="routers",
        module_type="python",
        imported_names=("workspace", "git", "overview"),
        range_start=0,
        range_end=0,
        is_type_only=False,
    )
    
    targets = resolve_python_import_all(imp, "src/app.py", ctx)
    assert "src/routers/__init__.py" in targets
    assert "src/routers/workspace.py" in targets
    assert "src/routers/git/__init__.py" in targets
    assert len(targets) == 3


def test_resolve_python_import_all_no_submodules():
    path_set = {
        "src/app.py",
        "src/routers/__init__.py",
    }
    
    ctx = ResolverContext(path_set=path_set, stem_map={}, graph=None)
    
    imp = Import(
        module_path="routers",
        module_type="python",
        imported_names=("missing",),
        range_start=0,
        range_end=0,
        is_type_only=False,
    )
    
    targets = resolve_python_import_all(imp, "src/app.py", ctx)
    assert targets == ("src/routers/__init__.py",)
