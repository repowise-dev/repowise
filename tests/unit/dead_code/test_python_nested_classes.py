"""Python nested classes reached as ``Outer.Inner`` are not unused exports.

Shaped on healthchecks: pydantic models nested in a transport class
(``Zulip.ErrorModel.model_validate_json(...)``, ``results: list[Signal.Result]``),
a Django ``TextChoices`` used bare in its model's body, and Django ``class Meta``
options blocks, which the model metaclass reads and no code names.
"""

from __future__ import annotations

import sys
from datetime import datetime

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.python_local_refs import extract_python_local_refs
from tests.unit.dead_code._helpers import _build_graph

_SOURCE = b"""
from __future__ import annotations


class Signal(Transport):
    class Result(BaseModel):
        type: str

    class Response(BaseModel):
        results: list[Signal.Result]

    class Reply(BaseModel):
        def again(self) -> "Signal.Reply": ...

    class Cached(BaseModel):
        pass

    class Unused(BaseModel):
        def me(self) -> Signal.Unused: ...

    def notify(self):
        return self.Cached


class Zulip(Transport):
    class ErrorModel(BaseModel):
        msg: str

    @classmethod
    def raise_for_response(cls, response):
        return Zulip.ErrorModel.model_validate_json(response.content)


class Member(Model):
    class Role(TextChoices):
        READONLY = "r", "Read-only"

    role = CharField(default=Role.READONLY)

    class Meta:
        ordering = ["role"]
"""


def _unused_exports(path: str, src: bytes) -> set[tuple[str | None, str]]:
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
    parsed = ASTParser().parse_file(info, src)
    symbols = [
        {
            "name": s.name,
            "kind": s.kind,
            "visibility": s.visibility,
            "parent_name": s.parent_name,
            "language": "python",
            "decorators": [],
            "start_line": s.start_line,
            "end_line": s.end_line,
        }
        for s in parsed.symbols
    ]
    g = _build_graph(
        nodes={
            path: {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "local_refs": parsed.local_refs,
                "symbol_count": len(symbols),
                "symbols": symbols,
            },
            "app/main.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 0,
                "symbols": [],
            },
        },
        edges=[("app/main.py", path, {"imported_names": ["Signal", "Zulip", "Member"]})],
    )
    report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False}
    )
    by_name = {s["name"]: s for s in symbols}
    return {
        (by_name[f.symbol_name]["parent_name"], f.symbol_name)
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT
    }


def test_nested_class_named_through_its_outer_class_is_used():
    unused = _unused_exports("app/transport.py", _SOURCE)
    # ``Signal.Result`` annotation, ``self.Cached``, ``Zulip.ErrorModel`` call
    # chain, bare ``Role`` in its model's body, framework ``Meta``.
    for used in ("Result", "Cached", "ErrorModel", "Role"):
        assert not any(name == used for _, name in unused), used
    assert ("Member", "Meta") not in unused


def test_nested_class_named_only_in_its_own_body_stays_reported():
    unused = _unused_exports("app/transport.py", _SOURCE)
    # ``Reply`` and ``Unused`` only name themselves; ``Response`` is never named.
    assert {("Signal", "Reply"), ("Signal", "Unused"), ("Signal", "Response")} <= unused


def test_top_level_meta_class_is_not_a_framework_options_block():
    src = b"class Meta:\n    pass\n"
    assert (None, "Meta") in _unused_exports("app/transport.py", src)


def test_local_refs_record_nested_class_uses_by_qualified_name():
    src = """
class Outer:
    class Inner:
        pass

class Other:
    class Inner:
        pass

def use():
    return pkg.Outer.Inner
"""
    refs = extract_python_local_refs(
        src, {"Outer", "Other", "use"}, {("Outer", "Inner"), ("Other", "Inner")}
    )
    assert "Outer.Inner" in refs
    # Same inner name under another class: not reached, not recorded.
    assert "Other.Inner" not in refs


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 type syntax requires Python 3.12+")
def test_pep_695_type_alias_quoted_local_ref():
    src = """
class Worker:
    pass

type WorkerPool = list["Worker"]
"""
    refs = extract_python_local_refs(src, frozenset(["Worker"]))
    assert "Worker" in refs


def test_typing_type_alias_quoted_rhs_ref():
    src = """
from typing import TypeAlias

class Worker:
    pass

WorkerRegistry: TypeAlias = dict[str, "Worker"]
"""
    refs = extract_python_local_refs(src, frozenset(["Worker"]))
    assert "Worker" in refs


def test_generic_base_class_quoted_argument():
    src = """
from typing import Generic

class Worker:
    pass

class WorkerManager(Generic["Worker"]):
    pass
"""
    refs = extract_python_local_refs(src, frozenset(["Worker"]))
    assert "Worker" in refs


def test_literal_and_call_kwarg_strings_do_not_rescue_classes():
    src = """
from typing import Literal, TypeAlias

class Worker:
    pass

Kind: TypeAlias = Literal["Worker"]
"""
    refs = extract_python_local_refs(src, frozenset(["Worker"]))
    assert "Worker" not in refs

    src_base = """
class Worker:
    pass

class MyClass(Base(name="Worker")):
    pass
"""
    refs_base = extract_python_local_refs(src_base, frozenset(["Worker"]))
    assert "Worker" not in refs_base


def test_self_referential_type_alias_excluded():
    if sys.version_info >= (3, 12):
        src = 'type NodeList = list["NodeList"]'
        assert extract_python_local_refs(src, frozenset(["NodeList"])) == frozenset()

    src_ann = """
from typing import TypeAlias
NodeList: TypeAlias = list["NodeList"]
"""
    assert extract_python_local_refs(src_ann, frozenset(["NodeList"])) == frozenset()

