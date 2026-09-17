"""Binding a C# extension-method call to the type its ``this`` parameter names.

The method index keys on the containing class, so ``static class
OrderExtensions { decimal NetTotal(this Order o) }`` only ever minted
``("OrderExtensions", "NetTotal")`` while the call site ``o.NetTotal()`` asks
for ``("Order", "NetTotal")``. Nothing wrote that key, so the site produced no
edge at all.

The resolver reads the receiver's declared type off the file text, so these
tests write real files and hand the resolver a repo path rather than parsing
in memory.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.type_names import csharp_extension_receiver

ORDER = "namespace Acme;\npublic class Order { public decimal Amount; }\n"


def _resolve(
    tmp_path: Path,
    files: dict[str, str],
    caller: str,
    imports: dict[str, set[str]] | None = None,
    heritage: dict[str, set[str]] | None = None,
) -> dict[str, tuple[str, float]]:
    """``{method name: (origin, confidence)}`` for every call the caller makes."""
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    parser = ASTParser()
    parsed = {}
    for rel, text in files.items():
        info = FileInfo(
            path=rel,
            abs_path=str(tmp_path / rel),
            language="go" if rel.endswith(".go") else "csharp",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[rel] = parser.parse_file(info, text.encode())

    resolver = CallResolver(
        parsed, imports or {}, repo_path=str(tmp_path), heritage_parents=heritage
    )
    by_callee = {}
    for resolved in resolver.resolve_file(caller, parsed[caller].calls):
        by_callee[resolved.callee_id.rpartition("::")[2]] = (
            resolved.origin,
            resolved.confidence,
        )
    return by_callee


class TestExtensionReceiver:
    """The pure helper, which reports shape and applies no policy."""

    @pytest.mark.parametrize(
        ("signature", "expected"),
        [
            ("NetTotal(this Order o, bool x = false) -> decimal", "Order"),
            ("Tail(this Acme.Domain.Order o) -> string", "Order"),
            ("First(this IEnumerable<T> src) -> T", "IEnumerable"),
            ("Use(this scoped ref readonly Order o) -> int", "Order"),
            ("Maybe(this Order? o) -> string", "Order"),
            # Not an extension method at all.
            ("Compute(int qty, string code) -> decimal", None),
            ("NoParams() -> void", None),
            # ``this`` must be a modifier, not the head of a type name.
            ("Trap(thisNotAModifier x) -> int", None),
            # An array extends the array, not the element.
            ("Sum(this Order[] orders) -> decimal", None),
        ],
    )
    def test_reports_the_extended_type(self, signature: str, expected: str | None) -> None:
        assert csharp_extension_receiver(signature) == expected


class TestTiers:
    def test_same_file(self, tmp_path: Path) -> None:
        files = {
            "src/Order.cs": ORDER,
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static int Net(this Order o) { return 1; }\n"
                "  public static void Run() { Order o = new Order(); var z = o.Net(); }\n"
                "}\n"
            ),
        }
        found = _resolve(tmp_path, files, "src/Ext.cs")
        assert found["Net"] == ("receiver_extension_same_file", 0.93)

    def test_imported(self, tmp_path: Path) -> None:
        files = {
            "src/Order.cs": ORDER,
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static int Net(this Order o) { return 1; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Net(); }\n}\n"
            ),
        }
        imports = {"src/Consumer.cs": {"src/Order.cs", "src/Ext.cs"}}
        found = _resolve(tmp_path, files, "src/Consumer.cs", imports)
        assert found["Net"] == ("receiver_extension_import", 0.88)

    def test_global(self, tmp_path: Path) -> None:
        """No import binds the holder, so only the repo-wide pair answers."""
        files = {
            "src/Order.cs": ORDER,
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static int Net(this Order o) { return 1; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Net(); }\n}\n"
            ),
        }
        found = _resolve(tmp_path, files, "src/Consumer.cs")
        assert found["Net"] == ("receiver_extension_global", 0.75)

    def test_an_instance_method_outranks_an_extension(self, tmp_path: Path) -> None:
        """C# dispatches to the instance method, so the extension must not answer."""
        files = {
            "src/Order.cs": (
                "namespace Acme;\npublic class Order { public int Net() { return 1; } }\n"
            ),
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static int Net(this Order o) { return 2; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Net(); }\n}\n"
            ),
        }
        imports = {"src/Consumer.cs": {"src/Order.cs", "src/Ext.cs"}}
        assert _resolve(tmp_path, files, "src/Consumer.cs")["Net"] == (
            "receiver_typed_global",
            0.75,
        )
        assert _resolve(tmp_path, files, "src/Consumer.cs", imports)["Net"] == (
            "receiver_typed_import",
            0.88,
        )


class TestRefusals:
    def test_refuses_a_generic_type_parameter(self, tmp_path: Path) -> None:
        """``this T source`` would otherwise bind every receiver in the repo."""
        files = {
            "src/Order.cs": ORDER,
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static T Pick<T>(this T src) { return src; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Pick(); }\n}\n"
            ),
        }
        assert "Pick" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_refuses_a_bcl_receiver(self, tmp_path: Path) -> None:
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static string Slug(this string s) { return s; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                '  public void Run() { string s = "x"; var z = s.Slug(); }\n}\n'
            ),
        }
        assert "Slug" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_refuses_when_the_receiver_inherits_the_method(self, tmp_path: Path) -> None:
        """C# prefers an inherited instance method, and no tier above sees one."""
        files = {
            "src/Base.cs": (
                "namespace Acme;\npublic class BaseOrder { public int Net() { return 1; } }\n"
            ),
            "src/Order.cs": "namespace Acme;\npublic class Order : BaseOrder { }\n",
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static int Net(this Order o) { return 2; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Net(); }\n}\n"
            ),
        }
        heritage = {"src/Order.cs::Order": {"src/Base.cs::BaseOrder"}}
        assert "Net" not in _resolve(tmp_path, files, "src/Consumer.cs", heritage=heritage)

    def test_a_default_value_is_not_a_parameter_list(self, tmp_path: Path) -> None:
        """A parenthesised string in a default value must not mint a pair."""
        files = {
            "src/Order.cs": ORDER,
            "src/Auditor.cs": (
                "namespace Acme;\npublic class Auditor {\n"
                '  public void Log(string message = "(this Order was rejected)") { }\n}\n'
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); o.Log(); }\n}\n"
            ),
        }
        assert "Log" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_a_type_of_that_name_in_another_language_does_not_count(
        self, tmp_path: Path
    ) -> None:
        """The receiver must be declared in C#, not merely somewhere in the repo."""
        files = {
            "src/task.go": "package main\n\ntype Task struct {}\n",
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static void Forget(this Task t) { }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Task t = new Task(); t.Forget(); }\n}\n"
            ),
        }
        assert "Forget" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_ambiguity_is_terminal(self, tmp_path: Path) -> None:
        """Two holders declaring one pair: C# picks by ``using`` scope, we cannot."""
        files = {
            "src/Order.cs": ORDER,
            "src/One.cs": (
                "namespace Acme;\npublic static class One {\n"
                "  public static int Dup(this Order o) { return 1; }\n}\n"
            ),
            "src/Two.cs": (
                "namespace Acme;\npublic static class Two {\n"
                "  public static int Dup(this Order o) { return 2; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Dup(); }\n}\n"
            ),
        }
        assert "Dup" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_an_overload_set_is_not_ambiguous(self, tmp_path: Path) -> None:
        """Every overload in one class shares a symbol id, so the pair survives."""
        files = {
            "src/Order.cs": ORDER,
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static int Net(this Order o) { return 1; }\n"
                "  public static int Net(this Order o, bool round) { return 2; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Net(); }\n}\n"
            ),
        }
        found = _resolve(tmp_path, files, "src/Consumer.cs")
        assert found["Net"] == ("receiver_extension_global", 0.75)
