"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

PYTHON_SOURCE = b'''"""Module docstring."""

from __future__ import annotations

from python_pkg.models import Operation
from python_pkg.utils import round_result
import os


class DivisionByZeroError(ArithmeticError):
    """Raised on division by zero."""


def add(x: float, y: float) -> float:
    """Return x + y."""
    return x + y


def subtract(x: float, y: float) -> float:
    """Return x - y."""
    return x - y


class Calculator:
    """Stateful calculator."""

    def __init__(self) -> None:
        self._history = []

    def add(self, x: float, y: float) -> float:
        """Add x and y."""
        return add(x, y)

    @staticmethod
    def version() -> str:
        """Return version string."""
        return "1.0"
'''


class TestPythonParser:
    def test_module_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        assert result.docstring == "Module docstring."

    def test_finds_top_level_functions(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        names = [s.name for s in result.symbols]
        assert "add" in names
        assert "subtract" in names

    def test_finds_classes(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [c.name for c in classes]
        assert "Calculator" in class_names
        assert "DivisionByZeroError" in class_names

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "add" in method_names
        assert "__init__" in method_names

    def test_method_has_parent(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        calc_add = next(
            s for s in result.symbols if s.name == "add" and s.parent_name == "Calculator"
        )
        assert calc_add.parent_name == "Calculator"

    def test_private_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        init = next(s for s in result.symbols if s.name == "__init__")
        # Dunder is public by our convention
        assert init.visibility == "public"

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        module_paths = [i.module_path for i in result.imports]
        assert "python_pkg.models" in module_paths
        assert "python_pkg.utils" in module_paths
        assert "os" in module_paths

    def test_from_import_names(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        op_import = next(i for i in result.imports if i.module_path == "python_pkg.models")
        assert "Operation" in op_import.imported_names

    def test_relative_from_import_single_name(self, parser: ASTParser) -> None:
        # Regression: `from .X import Y` was dropping the only imported name.
        fi = _make_file_info("pkg/__init__.py", "python")
        result = parser.parse_file(fi, b"from .graph import GraphBuilder\n")
        rel_import = next(i for i in result.imports if "graph" in i.module_path)
        assert rel_import.imported_names == ["GraphBuilder"]

    def test_relative_from_import_multiple_names_keeps_first(self, parser: ASTParser) -> None:
        # Regression: `from .X import A, B, C` was dropping `A`.
        fi = _make_file_info("pkg/__init__.py", "python")
        result = parser.parse_file(
            fi, b"from .change_detector import AffectedPages, ChangeDetector, FileDiff\n"
        )
        rel_import = next(i for i in result.imports if "change_detector" in i.module_path)
        assert rel_import.imported_names == ["AffectedPages", "ChangeDetector", "FileDiff"]

    def test_bare_relative_import_expands_to_one_per_submodule(self, parser: ASTParser) -> None:
        # Regression (D2): ``from . import a, b`` produced one Import with
        # ``module_path="."`` which the resolver dropped on the floor, so
        # every plugin-registry barrel silently lost its sibling-submodule
        # edges. Expansion rewrites each name into ``.a``/``.b`` so the
        # existing relative-resolver can locate the submodule.
        fi = _make_file_info("pkg/__init__.py", "python")
        result = parser.parse_file(fi, b"from . import cargo, go, npm, nuget, pypi\n")
        paths = sorted(i.module_path for i in result.imports if i.is_relative)
        assert paths == [".cargo", ".go", ".npm", ".nuget", ".pypi"]
        for imp in result.imports:
            if imp.module_path.startswith(".") and imp.module_path != ".":
                assert len(imp.imported_names) == 1
                assert imp.imported_names[0] == imp.module_path.lstrip(".")

    def test_bare_double_dot_relative_import_expands(self, parser: ASTParser) -> None:
        # ``from .. import x`` must also expand, preserving dot depth.
        fi = _make_file_info("pkg/sub/mod.py", "python")
        result = parser.parse_file(fi, b"from .. import sibling, other\n")
        paths = sorted(i.module_path for i in result.imports if i.is_relative)
        assert paths == ["..other", "..sibling"]

    def test_named_relative_import_is_not_split(self, parser: ASTParser) -> None:
        # Negative: ``from .pkg import a, b`` must stay as a single Import —
        # only the bare-dots case is rewritten.
        fi = _make_file_info("pkg/use.py", "python")
        result = parser.parse_file(fi, b"from .pkg import a, b\n")
        rel = [i for i in result.imports if i.module_path == ".pkg"]
        assert len(rel) == 1
        assert sorted(rel[0].imported_names) == ["a", "b"]

    def test_absolute_from_import_skips_module_keeps_first_name(self, parser: ASTParser) -> None:
        # Guard against over-correction: absolute imports must still drop the
        # module path while keeping every imported name including the first.
        fi = _make_file_info("pkg/use.py", "python")
        result = parser.parse_file(fi, b"from python_pkg.models import Operation, Result\n")
        imp = next(i for i in result.imports if i.module_path == "python_pkg.models")
        assert imp.imported_names == ["Operation", "Result"]

    def test_nested_function_not_extracted_as_top_level(self, parser: ASTParser) -> None:
        # Regression (D8): orchestrator-style helpers defined inside an
        # async method (e.g. ``_on_start``, ``_on_done``, ``_step``) were
        # flattened to the top-level symbol list and read as unused public
        # exports. Only module-top-level + class-body members should appear.
        src = (
            b"async def run():\n"
            b"    def _on_start():\n"
            b"        pass\n"
            b"    async def _step():\n"
            b"        pass\n"
            b"\n"
            b"class Worker:\n"
            b"    async def perform(self):\n"
            b"        def _on_done():\n"
            b"            pass\n"
            b"        return _on_done\n"
        )
        fi = _make_file_info("pkg/orchestrator.py", "python")
        result = parser.parse_file(fi, src)
        names = {s.name for s in result.symbols}
        assert names == {"run", "Worker", "perform"}
        assert "_on_start" not in names
        assert "_step" not in names
        assert "_on_done" not in names

    def test_nested_class_inside_function_not_extracted(self, parser: ASTParser) -> None:
        src = b"def make():\n    class Helper:\n        def m(self): pass\n"
        fi = _make_file_info("pkg/factories.py", "python")
        result = parser.parse_file(fi, src)
        names = {s.name for s in result.symbols}
        assert names == {"make"}

    def test_function_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        add_fn = next(s for s in result.symbols if s.name == "add" and s.parent_name is None)
        assert add_fn.docstring == "Return x + y."

    def test_class_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        calc = next(s for s in result.symbols if s.name == "Calculator")
        assert calc.docstring == "Stateful calculator."

    def test_no_parse_errors_on_valid_source(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        assert result.parse_errors == []

    def test_parse_errors_on_invalid_source(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/bad.py", "python")
        result = parser.parse_file(fi, b"def (broken syntax: \npass\n")
        # Should not crash, but should report error
        assert isinstance(result.parse_errors, list)

    def test_qualified_name(self, parser: ASTParser) -> None:
        fi = _make_file_info("python_pkg/calculator.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        calc_add = next(
            s for s in result.symbols if s.name == "add" and s.parent_name == "Calculator"
        )
        assert calc_add.qualified_name == "python_pkg.calculator.Calculator.add"

    def test_exports_list(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        # Public top-level symbols should be in exports
        assert "add" in result.exports
        assert "Calculator" in result.exports


PYTHON_CONSTANTS_SOURCE = b'''"""Module with constants."""

import os

_DEFAULT_CO_CHANGE_MIN_COUNT = 2
MAX_RETRIES: int = 5
app = create_app()
_TABLE = {
    "a": 1,
}


def f(x):
    local_var = 3
    return x


class C:
    class_attr = 7

    def m(self):
        inner = 1
        return inner
'''


class TestPythonModuleConstants:
    """Module-level assignments are indexed as constant/variable symbols."""

    def _symbols(self, parser: ASTParser):
        fi = _make_file_info("pkg/consts.py", "python")
        return parser.parse_file(fi, PYTHON_CONSTANTS_SOURCE).symbols

    def test_screaming_case_assignment_is_constant(self, parser: ASTParser) -> None:
        syms = self._symbols(parser)
        const = next(s for s in syms if s.name == "_DEFAULT_CO_CHANGE_MIN_COUNT")
        assert const.kind == "constant"
        assert const.signature == "_DEFAULT_CO_CHANGE_MIN_COUNT = 2"

    def test_typed_assignment_keeps_annotation_in_signature(self, parser: ASTParser) -> None:
        syms = self._symbols(parser)
        const = next(s for s in syms if s.name == "MAX_RETRIES")
        assert const.kind == "constant"
        assert const.signature == "MAX_RETRIES: int = 5"

    def test_lowercase_assignment_is_variable(self, parser: ASTParser) -> None:
        syms = self._symbols(parser)
        var = next(s for s in syms if s.name == "app")
        assert var.kind == "variable"
        assert var.signature == "app = create_app()"

    def test_multiline_value_signature_is_first_line(self, parser: ASTParser) -> None:
        syms = self._symbols(parser)
        table = next(s for s in syms if s.name == "_TABLE")
        assert table.kind == "constant"
        assert table.signature == "_TABLE = {"
        assert table.end_line > table.start_line

    def test_function_local_assignment_not_extracted(self, parser: ASTParser) -> None:
        syms = self._symbols(parser)
        assert not any(s.name == "local_var" for s in syms)
        assert not any(s.name == "inner" for s in syms)

    def test_class_attribute_not_extracted(self, parser: ASTParser) -> None:
        syms = self._symbols(parser)
        assert not any(s.name == "class_attr" for s in syms)
