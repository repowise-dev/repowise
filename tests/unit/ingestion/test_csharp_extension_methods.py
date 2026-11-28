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
            # A generic type keeps its arity, as receiver lookups key it.
            ("First(this IEnumerable<T> src) -> T", "IEnumerable`1"),
            # Nested arguments count once, at the top level.
            ("Get(this Expression<Func<T, TProperty>> e) -> object", "Expression`1"),
            ("Each(this IRule<T, IEnumerable<List<E>>> r) -> object", "IRule`2"),
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

    def test_an_extension_on_a_base_serves_a_derived_receiver(self, tmp_path: Path) -> None:
        files = {
            "src/Base.cs": "namespace Acme;\npublic class BaseOrder { }\n",
            "src/Order.cs": "namespace Acme;\npublic class Order : BaseOrder { }\n",
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static int Net(this BaseOrder o) { return 1; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run() { Order o = new Order(); var z = o.Net(); }\n}\n"
            ),
        }
        heritage = {"src/Order.cs::Order": {"src/Base.cs::BaseOrder"}}
        found = _resolve(tmp_path, files, "src/Consumer.cs", heritage=heritage)
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
        """A builtin receiver is not typed for this lookup, so its extensions stay out."""
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

    def test_refuses_a_method_type_parameter_spelled_as_a_type(self, tmp_path: Path) -> None:
        """``this TBuilder b`` extends the method's own parameter, not a library ``TBuilder``."""
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static TBuilder Use<TBuilder>(this TBuilder b) where TBuilder : class"
                " { return b; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run(TBuilder b) { var z = b.Use(); }\n}\n"
            ),
        }
        assert "Use" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_an_inherited_instance_method_outranks_an_extension(self, tmp_path: Path) -> None:
        """C# prefers an inherited instance method, so the base's member answers."""
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
        found = _resolve(tmp_path, files, "src/Consumer.cs", heritage=heritage)
        assert found["Net"] == ("receiver_typed_global", 0.75)

    def test_an_extension_serves_when_ancestor_member_has_different_arity(
        self, tmp_path: Path
    ) -> None:
        """An ancestor member with a different arity does not outrank a matching extension."""
        files = {
            "src/Base.cs": (
                "namespace Acme;\npublic class BaseOrder { public void Add(int n) { } }\n"
            ),
            "src/Order.cs": "namespace Acme;\npublic class Order : BaseOrder { }\n",
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static void Add(this Order o, string k, string v) { }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                '  public void Run() { Order o = new Order(); o.Add("a", "b"); }\n}\n'
            ),
        }
        heritage = {"src/Order.cs::Order": {"src/Base.cs::BaseOrder"}}
        found = _resolve(tmp_path, files, "src/Consumer.cs", heritage=heritage)
        assert found["Add"] == ("receiver_extension_global", 0.75)

    def test_an_extension_serves_when_ancestor_overloads_differ_in_arity(
        self, tmp_path: Path
    ) -> None:
        """Overloads on an ancestor do not block an extension if none admit the call arity."""
        files = {
            "src/Base.cs": (
                "namespace Acme;\npublic class BaseOrder {\n"
                "  public void Add(int n) { }\n"
                "  public void Add(int n, int m) { }\n"
                "}\n"
            ),
            "src/Order.cs": "namespace Acme;\npublic class Order : BaseOrder { }\n",
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static void Add(this Order o, string a, string b, string c) { }\n"
                "}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                '  public void Run() { Order o = new Order(); o.Add("x", "y", "z"); }\n'
                "}\n"
            ),
        }
        heritage = {"src/Order.cs::Order": {"src/Base.cs::BaseOrder"}}
        found = _resolve(tmp_path, files, "src/Consumer.cs", heritage=heritage)
        assert found["Add"] == ("receiver_extension_global", 0.75)

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

    def test_a_generic_sibling_type_is_another_type(self, tmp_path: Path) -> None:
        """``this Builder`` and ``this Builder<T>`` extend two types, so neither pair is ambiguous."""
        files = {
            "src/Builder.cs": (
                "namespace Acme;\npublic class Builder { }\npublic class Builder<T> { }\n"
            ),
            "src/Plain.cs": (
                "namespace Acme;\npublic static class Plain {\n"
                "  public static int Retry(this Builder b) { return 1; }\n}\n"
            ),
            "src/Typed.cs": (
                "namespace Acme;\npublic static class Typed {\n"
                "  public static int Retry<T>(this Builder<T> b) { return 2; }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run(Builder b) { var z = b.Retry(); }\n"
                "  public void RunTyped(Builder<int> b) { var z = b.Retry(); }\n"
                "  public void RunNew() { var b = new Builder<int>(); var z = b.Retry(); }\n}\n"
            ),
        }
        parser = ASTParser()
        for rel, text in files.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(text, encoding="utf-8")
        parsed = {
            rel: parser.parse_file(
                FileInfo(
                    path=rel, abs_path=str(tmp_path / rel), language="csharp",
                    size_bytes=len(text), git_hash="", last_modified=datetime.now(),
                    is_test=False, is_config=False, is_api_contract=False, is_entry_point=False,
                ),
                text.encode(),
            )
            for rel, text in files.items()
        }
        resolver = CallResolver(parsed, {}, repo_path=str(tmp_path))
        edges = {
            (r.caller_id.rpartition("::")[2], r.callee_id)
            for r in resolver.resolve_file("src/Consumer.cs", parsed["src/Consumer.cs"].calls)
        }
        assert edges == {
            ("Run", "src/Plain.cs::Plain::Retry"),
            ("RunTyped", "src/Typed.cs::Typed::Retry"),
            ("RunNew", "src/Builder.cs::Builder`1"),
            ("RunNew", "src/Typed.cs::Typed::Retry"),
        }

    def test_an_overload_set_is_not_ambiguous(self, tmp_path: Path) -> None:
        """An overload set is indexed once, so the pair survives and the call
        narrows to the member its arguments admit."""
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
        assert found["Net#1"] == ("receiver_extension_global", 0.75)


class TestLibraryReceivers:
    """A repo extension on a library type binds when one repo extension can be meant."""

    CONSUMER = (
        "namespace Acme;\nusing System.Linq.Expressions;\npublic class Consumer {\n"
        "  public void Run(Expression<Func<int, string>> expression) {"
        " var m = expression.GetMember(); }\n}\n"
    )

    def test_binds_the_one_extension_on_a_library_type(self, tmp_path: Path) -> None:
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static MemberInfo GetMember<T, TProperty>("
                "this Expression<Func<T, TProperty>> expression) { return null; }\n}\n"
            ),
            "src/Consumer.cs": self.CONSUMER,
        }
        assert _resolve(tmp_path, files, "src/Consumer.cs")["GetMember"] == (
            "receiver_extension_global",
            0.75,
        )

    def test_abstains_when_two_extensions_take_the_name_and_arity(self, tmp_path: Path) -> None:
        """Another library type of the name may be the receiver's base: abstain."""
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static MemberInfo GetMember<T, TProperty>("
                "this Expression<Func<T, TProperty>> expression) { return null; }\n}\n"
            ),
            "src/Other.cs": (
                "namespace Acme;\npublic static class Other {\n"
                "  public static MemberInfo GetMember(this LambdaExpression e) { return null; }\n}\n"
            ),
            "src/Consumer.cs": self.CONSUMER,
        }
        assert "GetMember" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_an_extension_of_another_arity_does_not_compete(self, tmp_path: Path) -> None:
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static MemberInfo GetMember<T, TProperty>("
                "this Expression<Func<T, TProperty>> expression) { return null; }\n}\n"
            ),
            "src/Other.cs": (
                "namespace Acme;\npublic static class Other {\n"
                "  public static MemberInfo GetMember(this LambdaExpression e, bool deep)"
                " { return null; }\n}\n"
            ),
            "src/Consumer.cs": self.CONSUMER,
        }
        assert "GetMember" in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_refuses_when_the_one_taker_is_on_another_type(self, tmp_path: Path) -> None:
        """The key's extension cannot take the arity; the one that can extends another type."""
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static MemberInfo GetMember<T, TProperty>("
                "this Expression<Func<T, TProperty>> expression, bool deep) { return null; }\n}\n"
            ),
            "src/Other.cs": (
                "namespace Acme;\npublic static class Other {\n"
                "  public static MemberInfo GetMember(this LambdaExpression e) { return null; }\n}\n"
            ),
            "src/Consumer.cs": self.CONSUMER,
        }
        assert "GetMember" not in _resolve(tmp_path, files, "src/Consumer.cs")

    def test_overloads_bind_when_the_arguments_pick_one(self, tmp_path: Path) -> None:
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static void Tag(this IServiceCollection s, int n) { }\n"
                "  public static void Tag(this IServiceCollection s, bool b) { }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run(IServiceCollection s) { s.Tag(true); }\n}\n"
            ),
        }
        found = _resolve(tmp_path, files, "src/Consumer.cs")
        assert list(found) == ["Tag#2(IServiceCollection,bool)"]

    def test_overloads_abstain_when_the_arguments_tie(self, tmp_path: Path) -> None:
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static void Tag(this IServiceCollection s, int n) { }\n"
                "  public static void Tag(this IServiceCollection s, bool b) { }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run(IServiceCollection s, object o) { s.Tag(o); }\n}\n"
            ),
        }
        found = _resolve(tmp_path, files, "src/Consumer.cs")
        assert not any(name.startswith("Tag") for name in found)

    def test_refuses_a_same_class_taker_on_another_type(self, tmp_path: Path) -> None:
        """One holder, two library types: the overload that takes the call extends the other one."""
        files = {
            "src/Ext.cs": (
                "namespace Acme;\npublic static class Ext {\n"
                "  public static void Tag(this IFoo f) { }\n"
                "  public static void Tag(this IBar b, int n) { }\n}\n"
            ),
            "src/Consumer.cs": (
                "namespace Acme;\npublic class Consumer {\n"
                "  public void Run(IFoo f) { f.Tag(1); }\n}\n"
            ),
        }
        found = _resolve(tmp_path, files, "src/Consumer.cs")
        assert not any(name.startswith("Tag") for name in found)
