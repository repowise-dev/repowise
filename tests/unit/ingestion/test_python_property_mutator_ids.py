"""A Python property's setter and deleter get their own ids beside the getter.

One shared id let the last def own the graph node, so a call in the getter
body was drawn from the setter's line.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.decorator_names import property_mutator_role
from tests.unit.ingestion.test_call_resolver_strategies import _edges, _parse_all

_SOURCE = (
    "def load(): pass\n"
    "def store(v): pass\n"
    "def drop(): pass\n\n"
    "class C:\n"
    "    @property\n"
    "    def x(self):\n"
    "        return load()\n\n"
    "    @x.setter\n"
    "    def x(self, v):\n"
    "        store(v)\n\n"
    "    @x.deleter\n"
    "    def x(self):\n"
    "        drop()\n"
)


def test_each_accessor_has_its_own_id_and_span(tmp_path: Path) -> None:
    parsed = _parse_all(tmp_path, {"m.py": ("python", _SOURCE)})
    lines = {s.id: s.start_line for s in parsed["m.py"].symbols if s.name == "x"}
    assert lines == {"m.py::C::x": 7, "m.py::C::x#setter": 11, "m.py::C::x#deleter": 15}


def test_a_call_in_each_body_is_drawn_from_its_own_accessor(tmp_path: Path) -> None:
    parsed = _parse_all(tmp_path, {"m.py": ("python", _SOURCE)})
    edges = {(caller, callee) for caller, callee, _, _ in _edges(parsed, tmp_path)}
    assert ("m.py::C::x", "m.py::load") in edges
    assert ("m.py::C::x#setter", "m.py::store") in edges
    assert ("m.py::C::x#deleter", "m.py::drop") in edges


def test_a_lone_setter_keeps_its_plain_id(tmp_path: Path) -> None:
    source = "class D(C):\n    @C.x.setter\n    def x(self, v):\n        pass\n"
    parsed = _parse_all(tmp_path, {"m.py": ("python", source)})
    assert [s.id for s in parsed["m.py"].symbols if s.name == "x"] == ["m.py::D::x"]


def test_only_a_qualified_setter_or_deleter_is_a_mutator() -> None:
    assert property_mutator_role(["@x.setter"]) == "setter"
    assert property_mutator_role(["@x.deleter"]) == "deleter"
    assert property_mutator_role(["@setter", "@property", "@x.getter"]) is None
