"""C# ``var x = f(..)`` locals typed from the declared return type of ``f``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.languages.receiver_types import scan_call_assignments
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

LIB = """namespace Acme;
public class Item {
    public Item(int n) { }
    public void Use() { }
}
public interface IRepo {
    Item Find(int id);
    System.Threading.Tasks.Task<Item> FindAsync(int id);
    System.Threading.Tasks.Task Save();
    Item[] All();
}
public class Factory {
    public static Item Make() { return new Item(1); }
    public Item Build() { return new Item(2); }
    public T Create<T>() { return default; }
    public Item Pick<Item>() { return default; }
}
"""


def _edges(tmp_path: Path, body: str) -> set[tuple[int, str, str]]:
    """``{(line, callee id, origin)}`` for the calls a ``Run`` method with *body* makes."""
    files = {
        "Lib.cs": LIB,
        "C.cs": (
            "namespace Acme;\npublic class C {\n"
            "    private Factory _f = new Factory();\n"
            "    public async System.Threading.Tasks.Task Run(IRepo repo) {\n"
            f"{body}"
            "    }\n}\n"
        ),
    }
    parser = ASTParser()
    parsed = {}
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(tmp_path / rel),
            language="csharp",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[rel] = parser.parse_file(info, text.encode())
    resolver = CallResolver(parsed, {"C.cs": {"Lib.cs"}}, repo_path=str(tmp_path))
    return {
        (r.line, r.callee_id, r.origin)
        for r in resolver.resolve_file("C.cs", parsed["C.cs"].calls)
    }


USE = "Lib.cs::Item::Use"


@pytest.mark.parametrize(
    "assignment",
    [
        "var x = Factory.Make();",
        "var x = _f.Build();",
        "var x = repo.Find(1);",
        "var x = await repo.FindAsync(1);",
        "using var x = repo.Find(1);",
        "var x = repo.Find(\n            1);",
    ],
)
def test_a_local_from_a_call_takes_the_declared_return_type(
    tmp_path: Path, assignment: str
) -> None:
    body = f"        {assignment}\n        x.Use();\n"
    use_line = 6 + assignment.count("\n")
    assert (use_line, USE, "receiver_typed_import") in _edges(tmp_path, body)


@pytest.mark.parametrize(
    "assignment",
    [
        # Not awaited: the local holds the Task, not the Item.
        "var x = repo.FindAsync(1);",
        # Awaiting a plain Task yields nothing.
        "var x = await repo.Save();",
        # An array, a generic parameter and a longer expression type nothing here.
        "var x = repo.All();",
        "var x = _f.Create<Item>();",
        # The method's own type parameter, though a class shares its name.
        "var x = _f.Pick<Item>();",
        "var x = repo.Find(1).ToString();",
        "var x = repo.Find(1)\n            ?? null;",
    ],
)
def test_untypable_calls_leave_the_local_untyped(tmp_path: Path, assignment: str) -> None:
    body = f"        {assignment}\n        x.Use();\n"
    assert not any(callee == USE for _, callee, _ in _edges(tmp_path, body))


def test_a_class_with_constructors_still_types_its_factory_result(tmp_path: Path) -> None:
    """``Make`` returns ``Item``; resolving that name lands on a constructor first."""
    body = "        var x = Factory.Make();\n        x.Use();\n"
    assert (6, USE, "receiver_typed_import") in _edges(tmp_path, body)


def test_scan_reads_generic_and_awaited_calls() -> None:
    text = "var a = await svc.Get<Foo>(1);\nvar b = Make();\nvar c = Make() + 1;\n"
    found = scan_call_assignments(text, "csharp")
    assert [(a.name, a.receiver, a.target, a.awaited) for a in found] == [
        ("a", "svc", "Get", True),
        ("b", None, "Make", False),
    ]


def test_scan_reads_configure_await() -> None:
    text = (
        "var a = await repo.LoadAsync().ConfigureAwait(false);\n"
        "var b = repo.LoadAsync().ConfigureAwait(false);\n"
        "var c = await repo.LoadAsync().ConfigureAwait(false).ToString();\n"
    )
    found = scan_call_assignments(text, "csharp")
    assert [(a.name, a.receiver, a.target, a.awaited) for a in found] == [
        ("a", "repo", "LoadAsync", True),
    ]


def test_csharp_configure_await_types_local(tmp_path: Path) -> None:
    src = (
        "using System.Threading.Tasks;\n"
        "namespace Acme;\n"
        "public class Order { public void Ship() { } public void Cancel() { } }\n"
        "public class Repo { public Task<Order> LoadAsync() { return null; } }\n"
        "public class Use {\n"
        "    private readonly Repo repo = new Repo();\n"
        "    public async Task Plain() { var a = await repo.LoadAsync(); a.Ship(); }\n"
        "    public async Task Configured() { var b = await repo.LoadAsync().ConfigureAwait(false); b.Cancel(); }\n"
        "}\n"
    )
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    src_file = tmp_path / "Acme/Orders.cs"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(src)
    ft = FileTraverser(tmp_path)
    files = list(ft.traverse())
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in files:
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()

    calls = {
        (u, v)
        for u, v, d in builder._graph.edges(data=True)
        if d.get("edge_type") == "calls" and "Use" in u
    }
    assert ("Acme/Orders.cs::Use::Configured", "Acme/Orders.cs::Order::Cancel") in calls
    assert ("Acme/Orders.cs::Use::Plain", "Acme/Orders.cs::Order::Ship") in calls

