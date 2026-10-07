"""Python visibility refinement from a literal module-level ``__all__``.

``py_visibility`` reads the identifier alone, so a name the module excludes
from ``__all__`` still read as exported and an underscore-prefixed name the
module explicitly exports still read ``private``. ``refine_py_visibility``
raises the second case to ``public`` and deliberately leaves the first alone:
``__all__`` is advisory and often stale, so demoting on absence would move a
genuinely dead export into the opt-in internals pass and hide it.

The signal is capped at the visibility label. It sets no export marker, mints
no edge and suppresses no finding, so membership can never, by itself, mark a
symbol reachable — ``test_python_dead_code_fixes.py`` pins that end to end.
Only a literal list, tuple or set of string constants counts; anything built
at runtime is treated as absent rather than half-parsed.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.extractors.visibility import py_module_all_names
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


# ---------------------------------------------------------------------------
# Visibility refinement
# ---------------------------------------------------------------------------


def test_listed_top_level_names_stay_public() -> None:
    src = """
__all__ = ["foo", "Bar", "CONST_VAL"]

def foo(): pass
class Bar: pass
CONST_VAL = 42
"""
    vis = _symbols(src)
    assert vis["foo"] == "public"
    assert vis["Bar"] == "public"
    assert vis["CONST_VAL"] == "public"


def test_underscore_name_listed_in_all_is_public() -> None:
    """The one hard effect of the signal: an explicit export wins over the name."""
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


def test_name_absent_from_all_is_not_demoted() -> None:
    """``__all__`` is advisory: absence never moves a name out of the export pass.

    Demoting ``baz`` to private would hide it from the default report (the
    uncalled-private pass is opt-in), which is the outcome the issue forbids.
    """
    src = """
__all__ = ["foo"]

def foo(): pass
def _bar(): pass
def baz(): pass
"""
    vis = _symbols(src)
    assert vis["foo"] == "public"
    assert vis["_bar"] == "private"
    assert vis["baz"] == "public"


def test_tuple_and_annotated_literal_forms_are_read() -> None:
    src = """
__all__: tuple[str, ...] = ("export_a", "_export_b")

def export_a(): pass
def _export_b(): pass
def unexported(): pass
"""
    vis = _symbols(src)
    assert vis["export_a"] == "public"
    assert vis["_export_b"] == "public"
    assert vis["unexported"] == "public"


def test_members_are_governed_by_their_own_scope() -> None:
    src = """
__all__ = ["MyClass", "_module_helper"]

class MyClass:
    def public_method(self): pass
    def _private_method(self): pass

    class Inner:
        def _deep(self): pass

def _module_helper(): pass
"""
    vis = _symbols(src)
    assert vis["MyClass"] == "public"
    assert vis["_module_helper"] == "public"
    assert vis["public_method"] == "public"
    assert vis["_private_method"] == "private"
    assert vis["Inner"] == "public"
    assert vis["_deep"] == "private"


def test_no_all_keeps_name_based_visibility() -> None:
    src = """
def public_fn(): pass
def _private_fn(): pass
def __dunder_name__(): pass
"""
    vis = _symbols(src)
    assert vis["public_fn"] == "public"
    assert vis["_private_fn"] == "private"
    assert vis["__dunder_name__"] == "public"


@pytest.mark.parametrize(
    "all_source",
    [
        # += mutates a list the literal read never saw.
        '__all__ = ["foo"]\n__all__ += ["_bar"]',
        # A comprehension is assembled at runtime.
        '__all__ = [name for name in ("_bar",)]',
        # Concatenation, a call, a non-string element.
        '__all__ = ["foo"] + ["_bar"]',
        '__all__ = list(("_bar",))',
        '__all__ = ["foo", NAME]',
        # A second binding and a nested one rewrite the list elsewhere.
        '__all__ = ["foo"]\n__all__ = ["_bar"]',
        'if True:\n    __all__ = ["_bar"]',
        # An unpacking target is a binding this pass cannot enumerate.
        '__all__, other = ["_bar"], 1',
        "from other import __all__",
    ],
)
def test_dynamic_all_is_treated_as_absent(all_source: str) -> None:
    src = f"{all_source}\n\ndef foo(): pass\ndef _bar(): pass\n"
    vis = _symbols(src)
    assert vis["_bar"] == "private"
    assert vis["foo"] == "public"


# ---------------------------------------------------------------------------
# py_module_all_names — the reader behind the refinement
# ---------------------------------------------------------------------------


def test_literal_reader_returns_the_listed_names() -> None:
    assert py_module_all_names('__all__ = ["a", "_b"]') == frozenset({"a", "_b"})
    assert py_module_all_names('__all__ = ("a", "_b")') == frozenset({"a", "_b"})
    assert py_module_all_names('__all__ = {"a", "_b"}') == frozenset({"a", "_b"})
    assert py_module_all_names('__all__: list[str] = ["a"]') == frozenset({"a"})


def test_literal_reader_returns_none_without_a_signal() -> None:
    # No ``__all__``, a built one, and an empty one are different answers:
    # only the first two are "no signal", the third is "nothing exported".
    assert py_module_all_names("def foo(): pass") is None
    assert py_module_all_names("# see __all__ for the API\ndef foo(): pass") is None
    assert py_module_all_names('__all__ = ["a"] + ["b"]') is None
    assert py_module_all_names("__all__: list[str]") is None
    assert py_module_all_names("__all__ = []") == frozenset()


def test_literal_reader_survives_unparseable_source() -> None:
    assert py_module_all_names("def broken(:\n") is None
