from __future__ import annotations

import ast
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor


def _get_fixture_names(tree: ast.AST) -> set[str]:
    """Walk an AST and return the names of all @pytest.fixture / @fixture decorated functions."""
    names: set[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if _is_fixture_decorator(decorator):
                names.append(node.name)
                break
    return set(names)


def _is_fixture_decorator(decorator: ast.expr) -> bool:
    """Return True if the decorator is @fixture or @pytest.fixture (with or without call)."""
    # @fixture or @pytest.fixture
    if isinstance(decorator, ast.Name) and decorator.id == "fixture":
        return True
    if isinstance(decorator, ast.Attribute) and decorator.attr == "fixture":
        return True
    # @fixture(...) or @pytest.fixture(...)
    if isinstance(decorator, ast.Call):
        return _is_fixture_decorator(decorator.func)
    return False


def _get_test_function_params(tree: ast.AST) -> set[str]:
    """Return all parameter names of test_* functions in the AST."""
    params: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            params.add(arg.arg)
        if node.args.vararg:
            params.add(node.args.vararg.arg)
        if node.args.kwarg:
            params.add(node.args.kwarg.arg)
    return params


class PytestDynamicHints(DynamicHintExtractor):
    name = "pytest_conftest"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        for conftest in repo_root.rglob("conftest.py"):
            try:
                source = conftest.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(conftest))
                rel_conftest = conftest.relative_to(repo_root).as_posix()
            except Exception:
                continue

            fixture_names = _get_fixture_names(tree)
            if not fixture_names:
                continue

            conftest_dir = conftest.parent
            seen_targets: set[str] = set()

            # Find all test files under the conftest's parent directory
            for pattern in ("test_*.py", "*_test.py"):
                for test_file in conftest_dir.rglob(pattern):
                    if test_file == conftest:
                        continue
                    try:
                        rel_test = test_file.relative_to(repo_root).as_posix()
                    except ValueError:
                        continue

                    if rel_test in seen_targets:
                        continue

                    try:
                        test_source = test_file.read_text(encoding="utf-8", errors="ignore")
                        test_tree = ast.parse(test_source, filename=str(test_file))
                    except Exception:
                        continue

                    # Check if any test function uses a fixture from this conftest
                    test_params = _get_test_function_params(test_tree)
                    if test_params & fixture_names:
                        seen_targets.add(rel_test)
                        edges.append(DynamicEdge(
                            source=rel_conftest,
                            target=rel_test,
                            edge_type="dynamic_uses",
                            hint_source=self.name,
                        ))

        return edges
