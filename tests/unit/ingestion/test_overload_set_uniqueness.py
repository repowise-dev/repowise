"""Behaviour pins for tier 3 counting symbols rather than declaration rows.

``_global_symbols`` holds one entry per declaration, so an overload set is
several rows under one id and ``len(candidates) == 1`` read it as an ambiguity
that does not exist. C#'s ``AddRetry`` is two rows and one symbol; the tier
refused it and the call reached nothing.

The controls are what make the collapse safe, and each is a shape that reads
like the same defect and is not: a name two DIFFERENT symbols declare stays two
ids and stays refused; a getter and its setter are one attribute, not an
overload set; the caller's own hierarchy still answers first; and a non-callable
symbol is still refused.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        out[rel] = parse_file(_file_info(rel, abs_, lang), content.encode("utf-8"))
    return out


def _edges(
    parsed: dict[str, ParsedFile],
    tmp_path: Path,
    heritage: dict[str, set[str]] | None = None,
) -> list[tuple[str, str, float, str]]:
    resolver = CallResolver(parsed, {}, repo_path=str(tmp_path), heritage_parents=heritage)
    return [
        (rc.caller_id, rc.callee_id, rc.confidence, rc.origin)
        for path, pf in parsed.items()
        for rc in resolver.resolve_file(path, pf.calls)
    ]


# Two declarations, one symbol id: the shape the row count read as ambiguous.
CS_OVERLOADS = """
namespace App;

public static class Helpers
{
    public static int Widen(int a) => a;
    public static int Widen(int a, int b) => a + b;
}
"""

CS_CALLER = """
namespace App;

public class Caller
{
    public int Run() => Widen(1, 2);
}
"""

# `Widen` is a method on one class and a property on another: two ids, and the
# tier must keep refusing it.
CS_RIVAL_KINDS = """
namespace App;

public class Other
{
    public int Widen { get; set; }
}
"""

# The caller inherits `Step`, so its own hierarchy answers - and must keep
# answering, at its own confidence, rather than being taken over by the
# collapsed global name.
CS_BASE = """
public class Base
{
    public int Step(int a) => a;
    public int Step(int a, int b) => a + b;
}
"""

CS_DERIVED = """
public class Derived : Base
{
    public int Run() => Step(1, 2);
}
"""

# A field is not callable, so it must not become the unique answer.
CS_FIELD_ONLY = """
namespace App;

public class Holder
{
    public int Amount;
}
"""

CS_FIELD_CALLER = """
namespace App;

public class FieldCaller
{
    public int Run() => Amount();
}
"""


# A getter and its setter are two declarations under one id. That reads as an
# overload set and is not one: the name is an attribute, not a callable.
PY_PROPERTY_PAIR = """
class Amqp:
    @cached_property
    def router(self):
        return 1

    @router.setter
    def router(self, value):
        return value
"""

PY_PROPERTY_CALLER = """
def query(router):
    return router(1, 2)
"""


class TestOverloadSetIsOneSymbol:
    def test_an_overload_set_resolves_instead_of_reading_as_ambiguous(
        self, tmp_path: Path
    ) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "src/Helpers.cs": ("csharp", CS_OVERLOADS),
                "src/Caller.cs": ("csharp", CS_CALLER),
            },
        )
        edges = _edges(parsed, tmp_path)
        hits = [e for e in edges if e[0].endswith("::Caller::Run")]
        assert [e[1].split("::")[-2:] for e in hits] == [["Helpers", "Widen"]], (
            f"Widen's two declarations are one symbol and must resolve; edges: {edges}"
        )

    def test_two_different_symbols_of_one_name_stay_refused(self, tmp_path: Path) -> None:
        """The control the collapse has to fail: this is filtering, not deduping."""
        parsed = _parse_all(
            tmp_path,
            {
                "src/Helpers.cs": ("csharp", CS_OVERLOADS),
                "src/Other.cs": ("csharp", CS_RIVAL_KINDS),
                "src/Caller.cs": ("csharp", CS_CALLER),
            },
        )
        edges = _edges(parsed, tmp_path)
        assert not [e for e in edges if e[0].endswith("::Caller::Run")], (
            f"a name a method and a property both declare must stay ambiguous; edges: {edges}"
        )

    def test_the_callers_own_hierarchy_still_answers_first(self, tmp_path: Path) -> None:
        """Ahead of the inherited tier this rung restated 1,027 Ocelot edges worse."""
        parsed = _parse_all(
            tmp_path,
            {
                "src/Base.cs": ("csharp", CS_BASE),
                "src/Derived.cs": ("csharp", CS_DERIVED),
            },
        )
        edges = _edges(
            parsed,
            tmp_path,
            heritage={"src/Derived.cs::Derived": {"src/Base.cs::Base"}},
        )
        hits = [e for e in edges if e[0].endswith("::Derived::Run")]
        assert hits, f"Step() must resolve through the caller's own base; edges: {edges}"
        assert hits[0][3] == "enclosing_inherited", (
            f"the inherited tier must keep this site, not the global rung; edges: {edges}"
        )

    @pytest.mark.xfail(
        reason="_NON_CALLABLE_KINDS is {'property'} while C# extracts a field "
        "or property as kind 'variable', so the data-member refusal never "
        "reaches C#. Pre-existing, and unchanged by this collapse.",
        strict=True,
    )
    def test_a_non_callable_symbol_is_still_refused(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "src/Holder.cs": ("csharp", CS_FIELD_ONLY),
                "src/FieldCaller.cs": ("csharp", CS_FIELD_CALLER),
            },
        )
        edges = _edges(parsed, tmp_path)
        assert not [e for e in edges if e[0].endswith("::FieldCaller::Run")], (
            f"a data member must not be the unique answer; edges: {edges}"
        )

    def test_a_property_getter_and_setter_are_not_an_overload_set(
        self, tmp_path: Path
    ) -> None:
        """Collapsing made an attribute look like a unique callable."""
        parsed = _parse_all(
            tmp_path,
            {
                "src/amqp.py": ("python", PY_PROPERTY_PAIR),
                "src/routes.py": ("python", PY_PROPERTY_CALLER),
            },
        )
        edges = _edges(parsed, tmp_path)
        assert not [e for e in edges if e[0].endswith("routes.py::query")], (
            f"router(...) is a local parameter, not Amqp.router; edges: {edges}"
        )
