"""MassTransit links formed through generics, wrappers, requests and saga initialisers.

Fixtures are small hand-written examples: a ``Sample.Contracts`` project declaring
the messages, and app repos that publish or consume them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.extractors import MassTransitExtractor, build_message_type_index
from repowise.core.workspace.matching import match_contracts

NS = "Sample.Contracts"
MESSAGES = ["Ping", "Pong", "OrderSubmitted", "OrderCancelled", "OrderStatusRequest", "OrderStatus"]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _extract(repos: dict[str, Path]) -> list[Contract]:
    index = build_message_type_index(repos)
    rows: list[Contract] = []
    for alias, path in repos.items():
        rows += MassTransitExtractor().extract(path, alias, message_types=index)
    fqns = {f.lower() for f in index.fqns}
    for row in rows:
        # Every emitted id resolves to a declared contract type, never a guess.
        assert row.meta["message_type"].lower() in fqns
    return rows


def _ids(rows: list[Contract], role: str, *, fault: bool = False, kind: str | None = None) -> set:
    return {
        r.contract_id.rsplit(":", 1)[-1]
        for r in rows
        if r.role == role and r.meta["fault"] is fault and (kind is None or r.meta["kind"] == kind)
    }


@pytest.fixture
def contracts(tmp_path: Path) -> Path:
    root = tmp_path / "contracts"
    _write(root, "Sample.Contracts/Sample.Contracts.csproj", '<Project Sdk="Microsoft.NET.Sdk" />')
    for name in MESSAGES:
        _write(
            root,
            f"Sample.Contracts/{name}.cs",
            f"namespace {NS}\n{{\n    public class {name}\n    {{\n    }}\n}}\n",
        )
    return root


def _app(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        _write(root, rel, text)
    return root


ECHO_CONSUMER = """public class EchoConsumer<T> : IConsumer<T> where T : class
{
    public Task Consume(ConsumeContext<T> context) => Task.CompletedTask;
}
"""

AUDIT_FAULTS = """public class AuditFaults<T> : IConsumer<Fault<T>> where T : class
{
    public Task Consume(ConsumeContext<Fault<T>> context) => Task.CompletedTask;
}
"""

ADD_HANDLER = """public static class Extensions
{
    public static void AddHandler<T>(this IBusRegistrationConfigurator cfg) where T : class
    {
        cfg.AddConsumer<EchoConsumer<T>>();
    }
}
"""


def _configure(*statements: str) -> str:
    body = "".join(f"        {s}\n" for s in statements)
    return (
        f"using {NS};\npublic class Setup\n{{\n"
        f"    public void Configure(IBusRegistrationConfigurator cfg)\n    {{\n{body}    }}\n}}\n"
    )


class TestOpenGenericConsumers:
    def test_open_generic_alone_yields_nothing(self, contracts: Path, tmp_path: Path) -> None:
        worker = _app(tmp_path, "worker", {"Echo.cs": ECHO_CONSUMER + ADD_HANDLER})

        rows = _extract({"contracts": contracts, "worker": worker})

        assert [r for r in rows if r.role == "consumer"] == []

    def test_helper_call_sites_register_each_message(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Echo.cs": ECHO_CONSUMER + ADD_HANDLER,
                "Setup.cs": _configure("cfg.AddHandler<Ping>();", "cfg.AddHandler<Pong>();"),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == {"ping", "pong"}
        assert _ids(rows, "consumer", fault=True) == set()

    def test_closed_generic_consumer_registered_directly(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Echo.cs": ECHO_CONSUMER,
                "Setup.cs": _configure("cfg.AddConsumer<EchoConsumer<OrderSubmitted>>();"),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == {"ordersubmitted"}

    def test_fault_consumer_closes_as_a_fault_of_the_argument(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Faults.cs": AUDIT_FAULTS,
                "Setup.cs": _configure("cfg.AddConsumer<AuditFaults<Ping>>();"),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == set()
        assert _ids(rows, "consumer", fault=True) == {"ping"}

    def test_vb_helper_and_generic_class(self, contracts: Path, tmp_path: Path) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Echo.vb": (
                    "Public Class EchoConsumer(Of T As Class)\n"
                    "    Implements IConsumer(Of T)\n"
                    "End Class\n"
                    "Public Module Extensions\n"
                    "    <Extension()>\n"
                    "    Public Sub AddHandler(Of T As Class)"
                    "(ByVal cfg As IBusRegistrationConfigurator)\n"
                    "        cfg.AddConsumer(Of EchoConsumer(Of T))()\n"
                    "    End Sub\n"
                    "End Module\n"
                ),
                "Setup.vb": (
                    f"Imports {NS}\n"
                    "Public Class Setup\n"
                    "    Public Sub Configure(cfg As IBusRegistrationConfigurator)\n"
                    "        cfg.AddHandler(Of Ping)()\n"
                    "    End Sub\n"
                    "End Class\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == {"ping"}


LOGGING_BASE = """public abstract class LoggingConsumer<TMessage> : IConsumer<TMessage>
    where TMessage : class
{
}
"""

FAULT_BASE = """public abstract class FaultHandler<T> : IConsumer<Fault<T>> where T : class
{
}
"""


class TestGenericConsumerBases:
    def test_subclass_is_keyed_on_the_message_argument(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Base.cs": LOGGING_BASE,
                "Handlers.cs": (
                    f"using {NS};\npublic class PingHandler : LoggingConsumer<Ping>\n{{\n}}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == {"ping"}

    def test_other_type_arguments_are_not_consumed(self, contracts: Path, tmp_path: Path) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Handler.cs": (
                    "public class RetrySettings { }\n"
                    "public abstract class Handler<TMessage, TOptions> : IConsumer<TMessage>\n"
                    "    where TMessage : class\n{\n}\n"
                ),
                "Handlers.cs": (
                    f"using {NS};\n"
                    "public class PongHandler : Handler<Pong, RetrySettings>\n{\n}\n"
                    "public class SwappedHandler : Handler<OrderCancelled, Ping>\n{\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        # Ping is only a second type argument there, so it is never consumed.
        assert _ids(rows, "consumer") == {"pong", "ordercancelled"}

    def test_a_three_level_chain_reaches_the_concrete_class(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Chain.cs": (
                    "public abstract class BaseConsumer<T> : IConsumer<T> where T : class\n{\n}\n"
                    "public abstract class MiddleConsumer<T> : BaseConsumer<T> where T : class\n"
                    "{\n}\n"
                ),
                "Concrete.cs": (
                    f"using {NS};\n"
                    "public class SubmittedConsumer : MiddleConsumer<OrderSubmitted>\n{\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == {"ordersubmitted"}

    def test_fault_consumer_base_is_a_fault_of_the_argument(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Base.cs": FAULT_BASE,
                "Handlers.cs": (
                    f"using {NS};\npublic class CancelFaults : FaultHandler<OrderCancelled>\n"
                    "{\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == set()
        assert _ids(rows, "consumer", fault=True) == {"ordercancelled"}

    def test_open_generic_base_alone_yields_nothing(self, contracts: Path, tmp_path: Path) -> None:
        worker = _app(tmp_path, "worker", {"Base.cs": LOGGING_BASE})

        rows = _extract({"contracts": contracts, "worker": worker})

        assert [r for r in rows if r.role == "consumer"] == []

    def test_vb_inherits_a_generic_base(self, contracts: Path, tmp_path: Path) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Base.vb": (
                    "Public MustInherit Class LoggingConsumer(Of TMessage As Class)\n"
                    "    Implements IConsumer(Of TMessage)\n"
                    "End Class\n"
                ),
                "Handlers.vb": (
                    f"Imports {NS}\n"
                    "Public Class PingHandler\n"
                    "    Inherits LoggingConsumer(Of Ping)\n"
                    "End Class\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == {"ping"}


WRAPPER = """using Sample.Contracts;
public static class BusExtensions
{
    public static Task PublishMessage<T>(this IPublishEndpoint e, T m) where T : class
    {
        return e.Publish(m);
    }

    public static Task SendMessage(this ISendEndpoint endpoint, object message)
        => endpoint.Send(message);

    public static Task Describe(this IBus bus, string text)
    {
        return Console.Out.Send(prefix + text);
    }
}
"""


class TestWrapperMethods:
    def test_extension_that_publishes_its_message_makes_callers_producers(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Bus.cs": WRAPPER,
                "Submit.cs": (
                    f"using {NS};\n"
                    "public class Submit\n{\n"
                    "    public Task Run(IPublishEndpoint bus)\n    {\n"
                    "        return bus.PublishMessage(new OrderSubmitted());\n"
                    "    }\n"
                    "    public Task Cancel(IPublishEndpoint bus)\n    {\n"
                    "        return bus.PublishMessage<OrderCancelled>(null);\n"
                    "    }\n"
                    "    public Task Text(IBus bus)\n    {\n"
                    "        return bus.Describe(new OrderStatus().ToString());\n"
                    "    }\n}\n"
                ),
            },
        )
        worker = _app(
            tmp_path,
            "worker",
            {
                "Handler.cs": (
                    f"using {NS};\npublic class H : IConsumer<OrderSubmitted>\n{{\n"
                    "    public Task Consume(ConsumeContext<OrderSubmitted> context) => null;\n}\n"
                )
            },
        )

        rows = _extract({"contracts": contracts, "orders": orders, "worker": worker})

        assert _ids(rows, "provider", kind="publish") == {"ordersubmitted", "ordercancelled"}
        # Describe sends text, not its message parameter, so it is not a wrapper.
        assert "orderstatus" not in _ids(rows, "provider")
        links = [link for link in match_contracts(rows) if link.contract_type == "topic"]
        assert len(links) == 1

    def test_vb_extension_wrapper(self, contracts: Path, tmp_path: Path) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Bus.vb": (
                    "Public Module BusExtensions\n"
                    "    <Extension()>\n"
                    "    Public Function SendMessage(Of T As Class)"
                    "(ByVal endpoint As ISendEndpoint, ByVal message As T) As Task\n"
                    "        Return endpoint.Send(message)\n"
                    "    End Function\n"
                    "End Module\n"
                ),
                "Submit.vb": (
                    f"Imports {NS}\n"
                    "Public Class Submit\n"
                    "    Public Sub Run(endpoint As ISendEndpoint)\n"
                    "        endpoint.SendMessage(New OrderSubmitted())\n"
                    "    End Sub\n"
                    "End Class\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "orders": orders})

        assert _ids(rows, "provider", kind="send") == {"ordersubmitted"}

    def test_non_extension_method_is_not_a_wrapper(self, contracts: Path, tmp_path: Path) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Util.cs": (
                    "public static class Util\n{\n"
                    "    public static Task PublishMessage<T>(IPublishEndpoint bus, T message)\n"
                    "        where T : class => bus.Publish(message);\n}\n"
                ),
                "Submit.cs": (
                    f"using {NS};\n"
                    "public class Submit\n{\n"
                    "    public Task Run(IPublishEndpoint bus) =>\n"
                    "        Util.PublishMessage(bus, null);\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "orders": orders})

        assert _ids(rows, "provider") == set()


class TestRequestResponse:
    def test_request_clients_produce_the_request_and_responses_produce_the_response(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        client = _app(
            tmp_path,
            "client",
            {
                "Lookup.cs": (
                    f"using {NS};\n"
                    "public class Lookup\n{\n"
                    "    public Lookup(IRequestClient<OrderStatusRequest> client) { }\n}\n"
                ),
                "Other.cs": (
                    f"using {NS};\n"
                    "public class Other\n{\n"
                    "    public void Run(IBus bus)\n    {\n"
                    "        var client = bus.CreateRequestClient<OrderSubmitted>();\n"
                    "    }\n}\n"
                ),
            },
        )
        server = _app(
            tmp_path,
            "server",
            {
                "Handler.cs": (
                    f"using {NS};\n"
                    "public class StatusHandler : IConsumer<OrderStatusRequest>\n{\n"
                    "    public async Task Consume(ConsumeContext<OrderStatusRequest> context)\n"
                    "    {\n"
                    "        await context.RespondAsync<OrderStatus>(new { });\n"
                    "        context.Respond(new OrderCancelled());\n"
                    "    }\n}\n"
                )
            },
        )

        rows = _extract({"contracts": contracts, "client": client, "server": server})

        assert _ids(rows, "provider", kind="request") == {"orderstatusrequest", "ordersubmitted"}
        assert _ids(rows, "provider", kind="respond") == {"orderstatus", "ordercancelled"}
        links = {
            (link.provider_repo, link.consumer_repo, link.contract_id.rsplit(":", 1)[-1])
            for link in match_contracts(rows)
            if link.contract_type == "topic"
        }
        assert ("client", "server", "orderstatusrequest") in links

    def test_mediator_request_client_is_not_a_producer(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        client = _app(
            tmp_path,
            "client",
            {
                "Lookup.cs": (
                    f"using {NS};\n"
                    "public class Lookup\n{\n"
                    "    public void Run(IMediator mediator)\n    {\n"
                    "        var c = mediator.CreateRequestClient<OrderStatusRequest>();\n"
                    "    }\n}\n"
                )
            },
        )

        rows = _extract({"contracts": contracts, "client": client})

        assert _ids(rows, "provider") == set()


class TestSagaInitialisers:
    def test_init_inside_publish_and_send_async_is_a_producer(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        saga = _app(
            tmp_path,
            "saga",
            {
                "Machine.cs": (
                    f"using {NS};\n"
                    "public class OrderStateMachine : MassTransitStateMachine<OrderState>\n{\n"
                    "    public OrderStateMachine()\n    {\n"
                    "        During(Submitted, When(Timeout)\n"
                    "            .PublishAsync(ctx => ctx.Init<OrderSubmitted>(new { }))\n"
                    '            .SendAsync(new Uri("queue:x"), '
                    "(ctx) => ctx.Init<OrderCancelled>(new { })));\n"
                    "    }\n}\n"
                )
            },
        )

        rows = _extract({"contracts": contracts, "saga": saga})

        assert _ids(rows, "provider") == {"ordersubmitted", "ordercancelled"}

    def test_init_outside_a_publish_or_send_is_not_a_producer(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        saga = _app(
            tmp_path,
            "saga",
            {
                "Machine.cs": (
                    f"using {NS};\n"
                    "public class Helper\n{\n"
                    "    public object Build(Ctx ctx) => ctx.Init<OrderSubmitted>(new { });\n}\n"
                    "// .Publish(ctx => ctx.Init<OrderCancelled>(new { }))\n"
                )
            },
        )

        rows = _extract({"contracts": contracts, "saga": saga})

        assert rows == []


class TestEndpointConventions:
    def test_convention_markers_are_never_producers(self, contracts: Path, tmp_path: Path) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Setup.cs": (
                    f"using {NS};\n"
                    "public class Setup\n{\n"
                    "    public void Configure()\n    {\n"
                    '        EndpointConvention.Map<OrderSubmitted>(new Uri("queue:orders"));\n'
                    "        EndpointConventions.Map.TypesFromNamespaceToQueue<OrderCancelled>"
                    '("orders");\n'
                    "    }\n}\n"
                )
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "provider") == set()


class TestGateAndPrecision:
    def test_names_without_consumer_still_close_bases_and_helpers(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Bases.cs": (
                    "public abstract class Handler<TMsg, TOpts> : IConsumer<TMsg>\n"
                    "    where TMsg : class\n{\n}\n"
                    "public class Listener<T> : IConsumer<T> where T : class\n{\n}\n"
                    "public static class Wiring\n{\n"
                    "    public static void AddPair<TA, TB>(this IBusRegistrationConfigurator c)\n"
                    "        where TA : class\n    {\n"
                    "        c.AddConsumer<Listener<TA>>();\n    }\n}\n"
                ),
                "Receiver.cs": (
                    f"using {NS};\npublic class PingReceiver : Handler<Ping, Pong>\n{{\n}}\n"
                ),
                "Setup.cs": (
                    f"using {NS};\n"
                    "public class Setup\n{\n"
                    "    public void Run(IBusRegistrationConfigurator cfg)\n    {\n"
                    "        cfg.AddPair<OrderCancelled, OrderStatus>();\n    }\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert _ids(rows, "consumer") == {"ping", "ordercancelled"}

    def test_wrapper_precision(self, contracts: Path, tmp_path: Path) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Bus.cs": (
                    "public static class Utils\n{\n"
                    "    public static Task PubWith<TBus, TMsg>(this IBus bus, TMsg m)\n"
                    "        where TMsg : class\n    {\n        return bus.Publish(m);\n    }\n"
                    "    public static Task PubInner<T>(this IBus bus, T m)\n"
                    "        where T : class\n    {\n"
                    "        return bus.Publish(m.Inner);\n    }\n"
                    "    public static Task PubLog<T>(this ILogger log, T m)\n"
                    "        where T : class\n    {\n"
                    "        return log.Publish(m);\n    }\n"
                    "    public static Task PubOther<T>(this IBus bus, T m)\n"
                    "        where T : class\n    {\n"
                    "        return other.Publish(m);\n    }\n}\n"
                ),
                "Use.cs": (
                    f"using {NS};\n"
                    "public class Use\n{\n"
                    "    public Task Run(IBus bus, ILogger logger)\n    {\n"
                    "        bus.PubWith<OrderStatus, OrderSubmitted>(null);\n"
                    "        bus.PubInner(new OrderCancelled());\n"
                    "        logger.PubLog(new OrderCancelled());\n"
                    "        bus.PubOther(new OrderCancelled());\n"
                    "        return Task.CompletedTask;\n    }\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "orders": orders})

        assert _ids(rows, "provider") == {"ordersubmitted"}

    def test_static_form_skips_the_receiver_argument(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Bus.cs": (
                    "public static class Utils\n{\n"
                    "    public static Task PubWith<T>(this IBus bus, T m) where T : class\n    {\n"
                    "        return bus.Publish(m);\n    }\n}\n"
                ),
                "Use.cs": (
                    f"using {NS};\n"
                    "public class Use\n{\n"
                    "    public Task Run(IBus bus2)\n    {\n"
                    "        return Utils.PubWith(bus2, new OrderSubmitted());\n    }\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "orders": orders})

        assert _ids(rows, "provider") == {"ordersubmitted"}

    def test_vb_body_send_on_another_receiver_is_not_a_wrapper(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Bus.vb": (
                    "Public Module Utils\n"
                    "    <Extension()>\n"
                    "    Public Sub Log(Of T As Class)(ByVal bus As IBus, ByVal msg As T)\n"
                    "        Console.Send(msg)\n"
                    "    End Sub\n"
                    "End Module\n"
                ),
                "Use.vb": (
                    f"Imports {NS}\n"
                    "Public Class Use\n"
                    "    Public Sub Run(bus As IBus)\n"
                    "        bus.Log(New OrderSubmitted())\n"
                    "    End Sub\n"
                    "End Class\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "orders": orders})

        assert _ids(rows, "provider") == set()

    def test_helper_name_collision_on_a_container_is_not_a_registration(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        worker = _app(
            tmp_path,
            "worker",
            {
                "Echo.cs": ECHO_CONSUMER + ADD_HANDLER.replace("AddHandler", "Register"),
                "Other.cs": (
                    "public static class Di\n{\n"
                    "    public static void Register<T>(this IServiceProvider sp)\n"
                    "        where T : class\n    {\n"
                    "        sp.AddConsumer<EchoConsumer<T>>();\n    }\n}\n"
                ),
                "Setup.cs": (
                    f"using {NS};\n"
                    "public class Setup\n{\n"
                    "    public void Run(IContainer container)\n    {\n"
                    "        container.Register<OrderSubmitted>();\n    }\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "worker": worker})

        assert [r for r in rows if r.role == "consumer"] == []

    def test_overloads_with_different_arity_pick_the_matching_type_argument(
        self, contracts: Path, tmp_path: Path
    ) -> None:
        orders = _app(
            tmp_path,
            "orders",
            {
                "Bus.cs": (
                    "public static class Utils\n{\n"
                    "    public static Task PubWith<T>(this IPublishEndpoint bus, T m)\n"
                    "        where T : class\n"
                    "    {\n        return bus.Publish(m);\n    }\n"
                    "    public static Task PubWith<TCtx, T>(this ConsumeContext<TCtx> ctx, T m)\n"
                    "        where T : class\n    {\n        return ctx.Publish(m);\n    }\n}\n"
                ),
                "Use.cs": (
                    f"using {NS};\n"
                    "public class Use\n{\n"
                    "    public Task Run(IPublishEndpoint bus, ConsumeContext<OrderStatus> ctx)\n"
                    "    {\n"
                    "        bus.PubWith<OrderSubmitted>(null);\n"
                    "        return ctx.PubWith<OrderStatus, OrderCancelled>(null);\n    }\n}\n"
                ),
            },
        )

        rows = _extract({"contracts": contracts, "orders": orders})

        assert _ids(rows, "provider") == {"ordersubmitted", "ordercancelled"}
