"""MassTransit producer/consumer contracts for C# and VB.NET.

Fixtures are small hand-written examples.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.extractors import MassTransitExtractor, build_message_type_index
from repowise.core.workspace.matching import match_contracts

NS = "Sample.Contracts"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _contract_project(root: Path, project: str, namespace: str, types: list[str]) -> None:
    """A ``<project>/<project>.csproj`` holding one C# file per message type."""
    name = project.rsplit("/", 1)[-1]
    _write(root, f"{project}/{name}.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')
    for name in types:
        _write(
            root,
            f"{project}/{name}.cs",
            f"namespace {namespace}\n{{\n    public class {name}\n    {{\n    }}\n}}\n",
        )


def _extract(repos: dict[str, Path]) -> tuple[list[Contract], dict[str, dict[str, int]]]:
    index = build_message_type_index(repos)
    rows: list[Contract] = []
    stats: dict[str, dict[str, int]] = {}
    for alias, path in repos.items():
        stats[alias] = {}
        rows += MassTransitExtractor().extract(
            path, alias, message_types=index, stats=stats[alias]
        )
    return rows, stats


def _rows(rows: list[Contract], role: str, file_suffix: str = "") -> list[Contract]:
    return [r for r in rows if r.role == role and r.file_path.endswith(file_suffix)]


def _links(rows: list[Contract]) -> list[ContractLink]:
    return [link for link in match_contracts(rows) if link.contract_type == "topic"]


def _id(name: str, namespace: str = NS) -> str:
    return f"topic::{namespace}:{name}".lower()


@pytest.fixture
def contracts(tmp_path: Path) -> Path:
    root = tmp_path / "contracts"
    _contract_project(
        root, "Sample.Contracts", NS, ["OrderSubmitted", "OrderCancelled", "Ping", "Pong"]
    )
    return root


def _app(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        _write(root, rel, text)
    return root


def _cs_usings() -> str:
    return f"using MassTransit;\nusing {NS};\n"


class TestContractIndex:
    def test_only_contract_projects_contribute_types(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _contract_project(root, "Acme.Contracts", "Acme.Messages", ["InContracts"])
        _contract_project(root, "src/Contracts", "Acme.Folder", ["InFolder"])
        _contract_project(root, "Acme.ContractNotes", "Acme.Notes", ["NotAMessage"])
        _contract_project(root, "Acme.Contracts.Tests", "Acme.Tests", ["TestOnly"])
        _write(
            root,
            "Acme.Contracts/obj/Generated.cs",
            "namespace Acme.Gen { public class Generated {} }",
        )

        index = build_message_type_index({"repo": root})

        assert index.fqns == {"Acme.Messages.InContracts", "Acme.Folder.InFolder"}
        assert index.by_short["InFolder"] == {"Acme.Folder.InFolder"}

    def test_nested_types_are_not_messages(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, "Sample.Contracts/Sample.Contracts.csproj", "<Project />")
        _write(
            root,
            "Sample.Contracts/Outer.cs",
            "namespace Sample.Contracts\n{\n    public class Outer\n    {\n"
            "        public class Inner { }\n    }\n}\n",
        )

        assert build_message_type_index({"repo": root}).fqns == {"Sample.Contracts.Outer"}

    def test_generic_types_are_not_messages(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, "Sample.Contracts/Sample.Contracts.csproj", "<Project />")
        _write(
            root,
            "Sample.Contracts/Envelope.cs",
            "namespace Sample.Contracts\n{\n    public class Envelope<T> { }\n"
            "    public class Plain { }\n}\n",
        )

        assert build_message_type_index({"repo": root}).fqns == {"Sample.Contracts.Plain"}


class TestContractProjectPattern:
    def test_configured_pattern_replaces_the_default(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _contract_project(root, "Acme.Messages", "Acme.Messages", ["Configured"])
        _contract_project(root, "Acme.Contracts", "Acme.Contracts", ["Default"])

        index = build_message_type_index({"repo": root}, contract_project_pattern=r".+\.messages")

        assert index.fqns == {"Acme.Messages.Configured"}

    def test_singular_names_need_a_widened_pattern(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _contract_project(root, "Acme.Contract", "Acme.Contract", ["Singular"])

        assert build_message_type_index({"repo": root}).fqns == set()
        widened = build_message_type_index({"repo": root}, None, r"(?:.+\.)?contracts?")
        assert widened.fqns == {"Acme.Contract.Singular"}

    def test_pattern_must_match_the_whole_directory_name(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _contract_project(root, "Acme.Messages.Extras", "Acme.Extras", ["Partial"])

        index = build_message_type_index({"repo": root}, contract_project_pattern="messages")

        assert index.fqns == set()

    def test_extractor_uses_its_own_pattern_without_a_prebuilt_index(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _contract_project(root, "Acme.Messages", "Acme.Messages", ["Ping"])
        _write(root, "Worker.cs", "using Acme.Messages;\npublic class W : IConsumer<Ping> { }\n")

        rows = MassTransitExtractor(r".+\.messages").extract(root, "repo")

        assert [r.contract_id for r in rows] == ["topic::acme.messages:ping"]


class TestConsumers:
    def test_each_message_binds_to_its_own_consume_method(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "warehouse",
            {
                "OrderConsumer.cs": (
                    "using System.Threading.Tasks;\n"
                    "using MassTransit;\n"
                    f"using {NS};\n\n"
                    "public class Handler : IConsumer<OrderCancelled>, IConsumer<OrderSubmitted>\n"
                    "{\n"
                    "    public Task Consume(ConsumeContext<OrderCancelled> context) =>\n"
                    "        Task.CompletedTask;\n\n"
                    "    public Task Consume(ConsumeContext<OrderSubmitted> context) =>\n"
                    "        Task.CompletedTask;\n"
                    "}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "warehouse": worker})

        by_id = {r.contract_id: r for r in _rows(rows, "consumer")}
        assert set(by_id) == {_id("OrderCancelled"), _id("OrderSubmitted")}
        submitted = by_id[_id("OrderSubmitted")]
        assert submitted.line == 10
        assert by_id[_id("OrderCancelled")].line == 7
        assert submitted.meta["message_type"] == f"{NS}.OrderSubmitted"
        assert submitted.meta["kind"] == "consumer"
        assert submitted.meta["broker"] == "masstransit"
        assert submitted.meta["fault"] is False
        assert submitted.meta["resolution"] == "using"
        assert submitted.confidence == 0.8

    def test_fault_consumer_is_keyed_on_the_inner_message(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {"Faults.cs": f"using {NS};\npublic class Faults : IConsumer<Fault<Ping>>\n{{\n}}\n"},
        )

        rows, _ = _extract({"contracts": contracts, "worker": worker})

        (row,) = _rows(rows, "consumer")
        assert row.contract_id == _id("Ping")
        assert row.meta["fault"] is True

    def test_fault_and_plain_consumer_of_one_message_are_two_rows(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Both.cs": (
                    f"using {NS};\n"
                    "public class Both : IConsumer<Ping>, IConsumer<Fault<Ping>>\n"
                    "{\n"
                    "    public Task Consume(ConsumeContext<Ping> context) => null;\n"
                    "    public Task Consume(ConsumeContext<Fault<Ping>> context) => null;\n"
                    "}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "worker": worker})

        by_fault = {r.meta["fault"]: r for r in _rows(rows, "consumer")}
        assert set(by_fault) == {True, False}
        assert by_fault[False].line == 4
        assert by_fault[True].line == 5

    def test_open_generic_consumer_yields_nothing(self, contracts: Path, tmp_path: Path) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Base.cs": (
                    f"using {NS};\n"
                    "public abstract class Base<TMessage> : IConsumer<TMessage>\n"
                    "    where TMessage : class\n{\n}\n"
                )
            },
        )

        rows, stats = _extract({"contracts": contracts, "worker": worker})

        assert _rows(rows, "consumer") == []
        assert stats["worker"]["masstransit_consumer_unresolved"] == 1

    def test_byte_order_mark_does_not_hide_the_namespace_or_usings(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, "Sample.Contracts/Sample.Contracts.csproj", "<Project />")
        (root / "Sample.Contracts" / "Ping.cs").write_bytes(
            b"\xef\xbb\xbfnamespace Sample.Contracts\n{\n    public class Ping { }\n}\n"
        )
        (root / "C.cs").write_bytes(
            b"\xef\xbb\xbfusing Sample.Contracts;\npublic class C : IConsumer<Ping>\n{\n}\n"
        )

        rows, _ = _extract({"repo": root})

        (row,) = _rows(rows, "consumer")
        assert row.contract_id == _id("Ping")
        assert row.meta["resolution"] == "using"

    def test_vb_implements_with_several_interfaces(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Handler.vb": (
                    f"Imports {NS}\n\n"
                    "Public Class Handler\n"
                    "    Implements IConsumer(Of Ping)\n"
                    "    Implements IConsumer(Of Pong)\n\n"
                    "    Public Async Function Consume(context As ConsumeContext(Of Pong)) "
                    "As Task Implements IConsumer(Of Pong).Consume\n"
                    "    End Function\n"
                    "End Class\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "worker": worker})

        assert {r.contract_id for r in _rows(rows, "consumer")} == {_id("Ping"), _id("Pong")}

    def test_saga_events_are_consumers_only_in_a_state_machine_file(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        sagas = _app(
            tmp_path,
            "sagas",
            {
                "OrderStateMachine.cs": (
                    "public class OrderStateMachine : MassTransitStateMachine<OrderState>\n"
                    "{\n"
                    f"    public Event<{NS}.OrderSubmitted> Submitted {{ get; }}\n"
                    "}\n"
                ),
                "Other.cs": (
                    "public class Other\n{\n"
                    f"    public Event<{NS}.OrderCancelled> Cancelled {{ get; }}\n"
                    "}\n"
                ),
            },
        )

        rows, _ = _extract({"contracts": contracts, "sagas": sagas})

        (row,) = _rows(rows, "consumer", "OrderStateMachine.cs")
        assert row.contract_id == _id("OrderSubmitted")
        assert row.meta["kind"] == "saga"
        assert _rows(rows, "consumer", "Other.cs") == []

    @pytest.mark.parametrize(
        ("vb", "text"),
        [
            (False, "// class C : IConsumer<Ping>\n/* IConsumer<Ping> */"),
            (True, "' Implements IConsumer(Of Ping)\nREM Implements IConsumer(Of Pong)"),
        ],
    )
    def test_commented_out_consumers_are_ignored(
        self, contracts: Path, tmp_path: Path, vb: bool, text: str
    ) -> None:
        imports = f"Imports {NS}\n" if vb else f"using {NS};\n"
        worker = _app(tmp_path, "worker", {"Old.vb" if vb else "Old.cs": imports + text + "\n"})

        rows, _ = _extract({"contracts": contracts, "worker": worker})

        assert _rows(rows, "consumer") == []


class TestProducers:
    @pytest.mark.parametrize(
        ("call", "kind"),
        [
            ("Task.Run(() => bus.Publish<Ping>(new { Id = 1 }));", "publish"),
            ("await bus.Publish<Ping>(\n    new\n    {\n        Id = 1,\n    });", "publish"),
            ("await context.Publish<Ping>(new { Id = 1 }, ct);", "publish"),
            ("x => x.Then(s => s.Publish<OrderState, Ping>(new { Id = 1 }))", "publish"),
            ("await endpoint.Send<Ping>(new { Id = 1 });", "send"),
            (
                "await scheduler.ScheduleRecurringSend<Ping>(uri, schedule, new { Id = 1 });",
                "schedule",
            ),
            ("await bus.Publish(new Ping { Id = 1 });", "publish"),
            ("await scheduler.SchedulePublish(DateTime.UtcNow, new Ping());", "schedule"),
            ("await scheduler.ScheduleSend(uri, DateTime.UtcNow, new Ping());", "schedule"),
            ("await scheduler.ScheduleRecurringSend(uri, schedule, new Ping());", "schedule"),
        ],
    )
    def test_csharp_call_shapes(
        self, contracts: Path, tmp_path: Path, call: str, kind: str
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Publisher.cs": (
                    f"using {NS};\n\npublic class Publisher\n{{\n    void Run()\n    {{\n"
                    f"{call}\n    }}\n}}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        (row,) = _rows(rows, "provider")
        assert row.contract_id == _id("Ping")
        assert row.meta["kind"] == kind
        assert row.file_path == "Publisher.cs"

    def test_schedule_argument_form_reads_the_message_not_the_time(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Timers.cs": (
                    f"using {NS};\n\npublic class Timers\n{{\n"
                    "    void Run(DateTime when)\n    {\n"
                    "        scheduler.SchedulePublish(when, new OrderSubmitted());\n"
                    "        scheduler.ScheduleSend(new Uri(\"queue:x\"), when, new Pong());\n"
                    "        scheduler.SchedulePublish(when, other);\n"
                    "    }\n}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        assert {r.contract_id for r in _rows(rows, "provider")} == {
            _id("OrderSubmitted"),
            _id("Pong"),
        }

    def test_producer_row_line_is_the_call_line(self, contracts: Path, tmp_path: Path) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "P.cs": (
                    f"using {NS};\n\nclass P\n{{\n    void M()\n    {{\n"
                    "        var x = 1;\n        bus.Publish<Ping>(new { });\n    }\n}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        (row,) = _rows(rows, "provider")
        assert row.line == 8
        assert row.meta["receiver"] == "bus"

    def test_vb_dataflow_with_dim_as_new_and_dim_equals_new(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        imports = f"Imports {NS}\n\n"
        app = _app(
            tmp_path,
            "app",
            {
                "AsNew.vb": (
                    imports + "Public Class A\n    Sub Go()\n"
                    "        Dim message As New OrderSubmitted()\n"
                    "        bus.Send(message)\n"
                    "    End Sub\nEnd Class\n"
                ),
                "EqualsNew.vb": (
                    imports + "Public Class B\n    Sub Go()\n"
                    "        Dim message = New OrderSubmitted() With\n"
                    "        {\n            .Id = 1\n        }\n"
                    "        bus.Send(message)\n"
                    "    End Sub\nEnd Class\n"
                ),
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        providers = _rows(rows, "provider")
        assert {r.file_path for r in providers} == {"AsNew.vb", "EqualsNew.vb"}
        for row in providers:
            assert row.contract_id == _id("OrderSubmitted")
            assert row.meta["resolution"] == "dataflow"
            assert row.confidence == 0.7

    def test_vb_publish_with_a_generic_argument(self, contracts: Path, tmp_path: Path) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Publisher.vb": (
                    f"Imports {NS}\n\nPublic Class Publisher\n"
                    "    Public Async Sub Run()\n"
                    "        Await bus.Publish(Of Pong)(New With {.Id = 1})\n"
                    "    End Sub\nEnd Class\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        (row,) = _rows(rows, "provider")
        assert row.contract_id == _id("Pong")

    def test_csharp_dataflow_from_var_declared_and_parameter(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Flow.cs": (
                    f"using {NS};\n\nclass Flow\n{{\n"
                    "    void A()\n    {\n        var msg = new Ping();\n"
                    "        bus.Send(msg);\n    }\n\n"
                    "    void B(Pong other)\n    {\n        bus.Publish(other);\n    }\n}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        assert {r.meta["message_type"].rsplit(".", 1)[-1] for r in _rows(rows, "provider")} == {
            "Ping",
            "Pong",
        }

    def test_vb_variable_declared_in_another_method_is_not_used(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Scope.vb": (
                    f"Imports {NS}\n\nPublic Class S\n"
                    "    Sub First()\n        Dim message As New Ping()\n    End Sub\n\n"
                    "    Sub Second()\n        bus.Send(message)\n    End Sub\nEnd Class\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        assert _rows(rows, "provider") == []

    def test_mediator_anonymous_and_commented_calls_yield_nothing(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Noise.cs": (
                    f"using {NS};\n\nclass N\n{{\n"
                    "    private readonly IPublisher _events;\n\n"
                    "    void M(IMediator m, ISender sender)\n    {\n"
                    "        mediator.Publish(new Ping());\n"
                    "        m.Send(new Ping());\n"
                    "        sender.Send<Ping>(x);\n"
                    "        _events.Publish(new Pong());\n"
                    "        bus.Send(new { A = 1 });\n"
                    "        // bus.Publish<Ping>(new { });\n"
                    "        /* bus.Publish(new Ping()); */\n"
                    '        log("bus.Publish<Ping>(x)");\n'
                    "    }\n}\n"
                ),
                "Noise.vb": (
                    f"Imports {NS}\n\nPublic Class V\n    Sub M()\n"
                    "        ' bus.Publish(New Ping())\n"
                    "        REM bus.Publish(New Ping())\n"
                    "        bus.Send(New With {.A = 1})\n"
                    "    End Sub\nEnd Class\n"
                ),
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        assert [r for r in rows if r.repo == "app"] == []

    def test_a_bus_receiver_next_to_a_mediator_still_counts(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Both.cs": (
                    f"using {NS};\n\nclass B\n{{\n"
                    "    void M(IMediator m, IBus bus)\n    {\n"
                    "        m.Send(new Ping());\n"
                    "        bus.Publish(new Pong());\n"
                    "    }\n}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        assert {r.contract_id for r in _rows(rows, "provider")} == {_id("Pong")}

    def test_test_projects_yield_no_producers_and_no_indexed_types(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Sample.Orders.Tests/PublishTests.cs": (
                    f"using {NS};\nclass T\n{{\n    void M() => bus.Publish<Ping>(new {{ }});\n}}\n"
                )
            },
        )
        _contract_project(app, "Sample.Contracts.Tests", "Sample.Tests", ["TestOnlyMessage"])

        rows, _ = _extract({"contracts": contracts, "app": app})

        assert [r for r in rows if r.repo == "app"] == []
        assert "Sample.Tests.TestOnlyMessage" not in build_message_type_index({"app": app}).fqns

    def test_type_outside_any_contract_project_is_counted_not_linked(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Mail.cs": (
                    "class M\n{\n    void Go()\n    {\n"
                    "        smtp.Send<Envelope>(new Envelope());\n"
                    "        bus.Publish(new Envelope());\n    }\n}\n"
                )
            },
        )

        rows, stats = _extract({"contracts": contracts, "app": app})

        assert [r for r in rows if r.repo == "app"] == []
        assert stats["app"]["masstransit_producer_unresolved"] == 2


class TestResolution:
    @pytest.fixture
    def twin_contracts(self, tmp_path: Path) -> dict[str, Path]:
        a, b = tmp_path / "a", tmp_path / "b"
        _contract_project(a, "Alpha.Contracts", "Alpha.Contracts", ["Ping"])
        _contract_project(b, "Beta.Contracts", "Beta.Contracts", ["Ping"])
        return {"a": a, "b": b}

    def test_same_short_name_in_two_namespaces_is_ambiguous(
        self, twin_contracts: dict[str, Path], tmp_path: Path
    ) -> None:
        worker = _app(tmp_path, "worker", {"C.cs": "public class C : IConsumer<Ping>\n{\n}\n"})

        rows, stats = _extract({**twin_contracts, "worker": worker})

        assert _rows(rows, "consumer") == []
        assert stats["worker"]["masstransit_ambiguous"] == 1

    def test_a_using_picks_the_namespace(
        self, twin_contracts: dict[str, Path], tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {"C.cs": "using Beta.Contracts;\npublic class C : IConsumer<Ping>\n{\n}\n"},
        )

        rows, _ = _extract({**twin_contracts, "worker": worker})

        (row,) = _rows(rows, "consumer")
        assert row.contract_id == "topic::beta.contracts:ping"

    def test_qualified_name_resolves_relative_to_a_parent_namespace(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "S.cs": (
                    "namespace Sample.Orders\n{\n    public class S\n    {\n"
                    "        void M() => bus.Publish<Contracts.Ping>(new { });\n"
                    "    }\n}\n"
                )
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        (row,) = _rows(rows, "provider")
        assert row.meta["resolution"] == "qualified"
        assert row.confidence == 0.8

    def test_unknown_qualified_name_does_not_fall_back_to_the_short_name(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Mail.cs": (
                    "class M\n{\n    void Go()\n    {\n"
                    "        mailer.Send(new Other.Mail.Ping());\n"
                    "        mailer.Send(new Ping());\n    }\n}\n"
                )
            },
        )

        rows, stats = _extract({"contracts": contracts, "app": app})

        (row,) = _rows(rows, "provider", "Mail.cs")
        assert row.meta["resolution"] == "unique_name"
        assert stats["app"]["masstransit_producer_unresolved"] == 1

    def test_using_alias_and_unique_name_fallback(self, contracts: Path, tmp_path: Path) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Aliased.cs": (
                    "using Msg = Sample.Contracts;\nclass A\n{\n"
                    "    void M() => bus.Publish<Msg.Ping>(new { });\n}\n"
                ),
                "Global.cs": "class G\n{\n    void M() => bus.Publish<Pong>(new { });\n}\n",
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        by_file = {r.file_path: r for r in _rows(rows, "provider")}
        assert by_file["Aliased.cs"].meta["resolution"] == "qualified"
        assert by_file["Global.cs"].meta["resolution"] == "unique_name"
        assert by_file["Global.cs"].confidence == 0.6

    def test_short_name_is_not_used_when_the_repo_declares_that_type_itself(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Ping.cs": "namespace Local\n{\n    public class Ping { }\n}\n",
                "Use.cs": "class U\n{\n    void M() => bus.Publish(new Ping());\n}\n",
                "Other.cs": "class O\n{\n    void M() => bus.Publish(new Pong());\n}\n",
            },
        )

        rows, stats = _extract({"contracts": contracts, "app": app})

        assert [r.contract_id for r in _rows(rows, "provider")] == [_id("Pong")]
        assert stats["app"]["masstransit_producer_unresolved"] == 1

    def test_a_using_still_reaches_the_contract_when_a_local_type_shares_the_name(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        app = _app(
            tmp_path,
            "app",
            {
                "Ping.vb": "Public Class Ping\nEnd Class\n",
                "Use.cs": (
                    f"using {NS};\nclass U\n{{\n    void M() => bus.Publish(new Ping());\n}}\n"
                ),
            },
        )

        rows, _ = _extract({"contracts": contracts, "app": app})

        (row,) = _rows(rows, "provider")
        assert row.meta["resolution"] == "using"


class TestLinks:
    @pytest.fixture
    def orders(self, tmp_path: Path) -> Path:
        return _app(
            tmp_path,
            "orders",
            {
                "Sample.Orders/Publisher.vb": (
                    f"Imports {NS}\n\n"
                    "Public Class Publisher\n"
                    "    Public Async Sub Submit()\n"
                    "        Await bus.Publish(New OrderSubmitted() With {.Id = 1})\n"
                    "    End Sub\n"
                    "End Class\n"
                )
            },
        )

    def _consumer_repo(self, tmp_path: Path, name: str) -> Path:
        return _app(
            tmp_path,
            name,
            {
                "Consumers.cs": (
                    f"using {NS};\npublic class OrderConsumer : IConsumer<OrderSubmitted>\n"
                    "{\n}\n"
                )
            },
        )

    def test_one_producer_links_each_consumer_repo(
        self, contracts: Path, orders: Path, tmp_path: Path
    ) -> None:
        repos = {"contracts": contracts, "orders": orders}
        for name in ("x", "y", "z"):
            repos[name] = self._consumer_repo(tmp_path, name)

        rows, _ = _extract(repos)

        links = [link for link in _links(rows) if link.contract_id == _id("OrderSubmitted")]
        assert sorted(link.consumer_repo for link in links) == ["x", "y", "z"]
        assert {link.provider_repo for link in links} == {"orders"}
        assert {link.provider_file for link in links} == {"Sample.Orders/Publisher.vb"}

    def test_dataflow_producer_links_a_consumer(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Ship.vb": (
                    f"Imports {NS}\n\nPublic Class Ship\n    Sub Go()\n"
                    "        Dim message = New Pong() With {.Id = 1}\n"
                    "        bus.Send(message)\n"
                    "    End Sub\nEnd Class\n"
                )
            },
        )
        warehouse = _app(
            tmp_path,
            "warehouse",
            {"Consumer.cs": f"using {NS};\npublic class PongConsumer : IConsumer<Pong> {{ }}\n"},
        )

        rows, _ = _extract({"contracts": contracts, "orders": orders, "warehouse": warehouse})

        (link,) = [link for link in _links(rows) if link.contract_id == _id("Pong")]
        assert (link.provider_repo, link.consumer_repo) == ("orders", "warehouse")
        (provider,) = _rows(rows, "provider", "Ship.vb")
        assert provider.meta["resolution"] == "dataflow"

    def test_same_repo_producer_and_consumer_do_not_self_link(
        self, contracts: Path, orders: Path, tmp_path: Path
    ) -> None:
        _write(
            orders,
            "Sample.Orders/SelfConsumer.cs",
            f"using {NS};\npublic class SelfConsumer : IConsumer<OrderSubmitted>\n{{\n}}\n",
        )
        other = self._consumer_repo(tmp_path, "other")

        rows, _ = _extract({"contracts": contracts, "orders": orders, "other": other})

        links = [link for link in _links(rows) if link.contract_id == _id("OrderSubmitted")]
        assert [(link.provider_repo, link.consumer_repo) for link in links] == [
            ("orders", "other")
        ]
