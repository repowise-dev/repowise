"""A C# declaration with no accessibility modifier takes its enclosing scope's default.

``csharp_visibility`` sees modifier text only, so it can return one default for
every declaration site. That default is the top-level-type one, ``internal``.
The real default is ``private`` inside a class/struct/record and ``public``
inside an interface or an enum, so unmodified members used to land in the wrong
dead-code pool. ``refine_csharp_visibility`` supplies the enclosing scope.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _visibility(src: str, path: str = "src/Thing.cs") -> dict[str, str]:
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="csharp",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, src.encode("utf-8"))
    return {s.name: s.visibility for s in parsed.symbols}


def test_unmodified_class_members_are_private() -> None:
    src = """
namespace N
{
    class Service
    {
        void Run() { }
        int _count;
        string Name { get; set; }
        Service() { }
        delegate int Handler(int x);
    }
}
"""
    vis = _visibility(src)
    assert vis["Run"] == "private"
    assert vis["_count"] == "private"
    assert vis["Name"] == "private"
    assert vis["Service"] == "private"  # the constructor shadows the class name
    assert vis["Handler"] == "private"


def test_unmodified_struct_and_record_members_are_private() -> None:
    src = """
namespace N
{
    struct Point { void Shift() { } }
    record Order { void Total() { } }
}
"""
    vis = _visibility(src)
    assert vis["Shift"] == "private"
    assert vis["Total"] == "private"


def test_interface_members_are_public() -> None:
    src = """
namespace N
{
    interface IRepository
    {
        void Save();
        int Count { get; }
        event System.Action Changed;
    }
}
"""
    vis = _visibility(src)
    assert vis["Save"] == "public"
    assert vis["Count"] == "public"
    assert vis["Changed"] == "public"


def test_enum_members_are_public() -> None:
    src = """
namespace N
{
    enum Mode { Fast, Slow }
}
"""
    vis = _visibility(src)
    assert vis["Fast"] == "public"
    assert vis["Slow"] == "public"


def test_nested_types_are_private_and_top_level_types_stay_internal() -> None:
    src = """
namespace N
{
    class Outer
    {
        class Inner { }
        enum Kind { A }
    }
    class TopLevel { }
    interface ITop { }
}
"""
    vis = _visibility(src)
    assert vis["Inner"] == "private"
    assert vis["Kind"] == "private"
    assert vis["TopLevel"] == "internal"
    assert vis["ITop"] == "internal"


def test_top_level_type_outside_any_namespace_stays_internal() -> None:
    vis = _visibility("class Loose { void M() { } }")
    assert vis["Loose"] == "internal"
    assert vis["M"] == "private"


def test_explicit_modifiers_win_everywhere() -> None:
    src = """
namespace N
{
    public class Service
    {
        public void Open() { }
        private void Close() { }
        protected void Reset() { }
        internal void Sync() { }
    }
    interface IThing
    {
        internal void Hidden();
    }
}
"""
    vis = _visibility(src)
    assert vis["Service"] == "public"
    assert vis["Open"] == "public"
    assert vis["Close"] == "private"
    assert vis["Reset"] == "protected"
    assert vis["Sync"] == "internal"
    assert vis["Hidden"] == "internal"


def test_accessibility_written_after_another_modifier_still_wins() -> None:
    """The queries capture one ``(modifier)`` and the parser keeps the first
    match, so ``static internal`` reaches the refinement as ``["static"]``.
    Reading the modifiers off the node is what keeps these correct."""
    src = """
namespace N
{
    class Service
    {
        static internal void Sync() { }
        abstract public void Open();
        static public int Count;
        new public void Reset() { }
        override public string ToString() => "";
    }
}
"""
    vis = _visibility(src)
    assert vis["Sync"] == "internal"
    assert vis["Open"] == "public"
    assert vis["Count"] == "public"
    assert vis["Reset"] == "public"
    assert vis["ToString"] == "public"


def test_paired_accessibility_keywords_keep_one_precedence() -> None:
    """Whichever half of the pair the capture kept, the recorded answer is the
    same — the refinement reads both keywords off the node."""
    src = """
namespace N
{
    class Service
    {
        protected internal void A() { }
        internal protected void B() { }
        private protected void C() { }
        protected private void D() { }
    }
}
"""
    vis = _visibility(src)
    assert vis["A"] == vis["B"] == "protected"
    assert vis["C"] == vis["D"] == "private"


def test_explicit_interface_implementation_is_public() -> None:
    """It carries no modifier and is reachable only through the interface, so
    ``private`` would drop a member with a live caller into the narrow pool."""
    vis = _visibility("namespace N { class Handler : IHandler { void IHandler.Handle() { } } }")
    assert vis["Handle"] == "public"


def test_record_struct_members_are_private() -> None:
    vis = _visibility("namespace N { record struct Point { void Shift() { } } }")
    assert vis["Shift"] == "private"


def test_non_accessibility_modifiers_do_not_block_the_default() -> None:
    src = """
namespace N
{
    class Service
    {
        static void Boot() { }
        async void Tick() { }
    }
}
"""
    vis = _visibility(src)
    assert vis["Boot"] == "private"
    assert vis["Tick"] == "private"


def test_a_leading_byte_order_mark_does_not_shift_the_modifiers() -> None:
    """Node offsets are byte offsets and the parser's source is decoded text,
    so anything that reads modifiers by slicing the source loses them after
    the first multi-byte character. Visual Studio writes the BOM by default,
    which makes this the common case on C# repos, not the corner one."""
    info = FileInfo(
        path="src/Constants.cs",
        abs_path="/repo/src/Constants.cs",
        language="csharp",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    src = b"\xef\xbb\xbfnamespace N;\n\npublic static class Outer\n{\n    public static class Roles { }\n    static internal void Sync() { }\n    void Hidden() { }\n}\n"
    vis = {s.name: s.visibility for s in _PARSER.parse_file(info, src).symbols}
    assert vis["Outer"] == "public"
    assert vis["Roles"] == "public"
    assert vis["Sync"] == "internal"
    assert vis["Hidden"] == "private"


def test_swift_default_is_unchanged() -> None:
    info = FileInfo(
        path="src/Thing.swift",
        abs_path="/repo/src/Thing.swift",
        language="swift",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, b"class Service {\n    func run() { }\n}\n")
    vis = {s.name: s.visibility for s in parsed.symbols}
    assert vis["Service"] == "internal"
    assert vis["run"] == "internal"
