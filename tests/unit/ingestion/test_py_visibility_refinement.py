"""Tests for Python symbol visibility refinement via module __all__.

``py_visibility`` determines default visibility from identifier names (leading
underscore is private, dunders/others are public). When a module declares an
explicit, static ``__all__``, ``refine_py_visibility`` refines top-level symbol
visibility so omitted names are demoted to private and included names (even
underscore-prefixed) become public.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _symbols(src: str, path: str = "src/module.py") -> dict[str, str]:
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="python",
        size_bytes=len(src),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, src.encode("utf-8"))
    return {s.name: s.visibility for s in parsed.symbols}


def test_names_in_all_are_public() -> None:
    src = """
__all__ = ["foo", "Bar", "CONST_VAL"]

def foo(): pass
class Bar: pass
CONST_VAL = 42
def baz(): pass
"""
    vis = _symbols(src)
    assert vis["foo"] == "public"
    assert vis["Bar"] == "public"
    assert vis["CONST_VAL"] == "public"
    assert vis["baz"] == "private"


def test_names_omitted_from_all_are_private() -> None:
    src = """
__all__ = ["foo"]

def foo(): pass
def _bar(): pass
def baz(): pass
"""
    vis = _symbols(src)
    assert vis["foo"] == "public"
    assert vis["_bar"] == "private"
    assert vis["baz"] == "private"


def test_underscore_names_in_all_are_public() -> None:
    src = """
__all__ = ["_internal_hook", "foo"]

def _internal_hook(): pass
def foo(): pass
def _other(): pass
"""
    vis = _symbols(src)
    assert vis["_internal_hook"] == "public"
    assert vis["foo"] == "public"
    assert vis["_other"] == "private"


def test_tuple_and_annotated_all() -> None:
    src = """
__all__: tuple[str, ...] = ("export_a", "_export_b")

def export_a(): pass
def _export_b(): pass
def unexported(): pass
"""
    vis = _symbols(src)
    assert vis["export_a"] == "public"
    assert vis["_export_b"] == "public"
    assert vis["unexported"] == "private"


def test_dynamic_all_treated_as_absent() -> None:
    # Comprehensions, binary ops (+), function calls, or += should be treated
    # as absent rather than partially parsed.
    src_comp = """
__all__ = [name for name in dir() if not name.startswith("_")]

def foo(): pass
def _bar(): pass
def baz(): pass
"""
    vis_comp = _symbols(src_comp)
    assert vis_comp["foo"] == "public"
    assert vis_comp["_bar"] == "private"
    assert vis_comp["baz"] == "public"

    src_concat = """
__all__ = ["foo"] + ["bar"]

def foo(): pass
def _bar(): pass
def baz(): pass
"""
    vis_concat = _symbols(src_concat)
    assert vis_concat["foo"] == "public"
    assert vis_concat["_bar"] == "private"
    assert vis_concat["baz"] == "public"

    src_aug = """
__all__ = ["foo"]
__all__ += ["baz"]

def foo(): pass
def _bar(): pass
def baz(): pass
"""
    vis_aug = _symbols(src_aug)
    assert vis_aug["foo"] == "public"
    assert vis_aug["_bar"] == "private"
    assert vis_aug["baz"] == "public"


def test_class_and_inner_members_unaffected_by_module_all() -> None:
    src = """
__all__ = ["MyClass"]

class MyClass:
    def public_method(self): pass
    def _private_method(self): pass

def top_level_helper(): pass
"""
    vis = _symbols(src)
    assert vis["MyClass"] == "public"
    assert vis["public_method"] == "public"
    assert vis["_private_method"] == "private"
    assert vis["top_level_helper"] == "private"


def test_no_all_uses_standard_visibility() -> None:
    src = """
def public_fn(): pass
def _private_fn(): pass
def __dunder_name__(): pass
"""
    vis = _symbols(src)
    assert vis["public_fn"] == "public"
    assert vis["_private_fn"] == "private"
    assert vis["__dunder_name__"] == "public"
