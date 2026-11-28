"""A Python class statement nested in a function binds its name there.

The nested class is no symbol, so a call of its name must not resolve to a
same-named class declared in another file or at module level.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.ingestion.test_call_resolver_strategies import _edges, _parse_all

_OTHER = "class Inner:\n    pass\n"


def _callees(tmp_path: Path, body: str) -> set[str]:
    parsed = _parse_all(tmp_path, {"other.py": ("python", _OTHER), "m.py": ("python", body)})
    return {callee for caller, callee, _, _ in _edges(parsed, tmp_path) if caller == "m.py::f"}


def test_a_nested_class_hides_another_files_class(tmp_path: Path) -> None:
    body = "def f():\n    class Inner:\n        a = 1\n\n    return Inner()\n"
    assert "other.py::Inner" not in _callees(tmp_path, body)


def test_a_decorated_nested_class_hides_a_module_class(tmp_path: Path) -> None:
    body = (
        "class Inner:\n    pass\n\n"
        "def f():\n    @dataclass\n    class Inner:\n        a: int\n\n    return Inner(1)\n"
    )
    assert "m.py::Inner" not in _callees(tmp_path, body)


def test_a_nested_class_in_another_function_does_not_hide(tmp_path: Path) -> None:
    body = (
        "class Inner:\n    pass\n\n"
        "def g():\n    class Inner:\n        pass\n\n"
        "def f():\n    return Inner()\n"
    )
    assert "m.py::Inner" in _callees(tmp_path, body)


def test_a_class_nested_in_a_class_body_stays_a_member(tmp_path: Path) -> None:
    body = "class Outer:\n    class Meta:\n        pass\n\n    m = Meta()\n"
    parsed = _parse_all(tmp_path, {"m.py": ("python", body)})
    edges = {(a, c) for a, c, _, _ in _edges(parsed, tmp_path)}
    assert ("m.py::Outer", "m.py::Outer::Meta") in edges


def test_a_class_in_a_method_body_is_a_local(tmp_path: Path) -> None:
    body = (
        "class Outer:\n    def f(self):\n        if True:\n"
        "            class Inner:\n                pass\n        return Inner()\n"
    )
    parsed = _parse_all(tmp_path, {"other.py": ("python", _OTHER), "m.py": ("python", body)})
    callees = {c for a, c, _, _ in _edges(parsed, tmp_path) if a == "m.py::Outer::f"}
    assert "other.py::Inner" not in callees
