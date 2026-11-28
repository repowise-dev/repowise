"""C# integration events: publishers and subscribers joined by the event's short type name."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.extractors.topic import TopicExtractor
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.origins import ContractOrigin


def _repo(tmp_path: Path, alias: str, files: dict[str, str]) -> list[Contract]:
    root = tmp_path / alias
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return [
        c for c in TopicExtractor().extract(root, alias) if c.meta.get("broker") == "integration_event"
    ]


def _rows(contracts: list[Contract]) -> set[tuple[str, str, str, int | None]]:
    return {(c.role, c.contract_id, c.file_path, c.line) for c in contracts}


def _event(ns: str, name: str = "OrderPaidIntegrationEvent", base: str = "IntegrationEvent") -> str:
    return f"namespace {ns};\n\npublic record {name}(int OrderId) : {base};\n"


_PAID = "topic::orderpaidintegrationevent"

_PUBLISHER = """namespace Ordering.Handlers;

public class PaidHandler(IEventBus eventBus)
{
    public async Task Handle(int id)
    {
        var paid = new OrderPaidIntegrationEvent(id);
        logger.LogInformation("Publishing {Id}", paid.Id);
        await eventBus.PublishAsync(paid);
    }
}
"""

_SUBSCRIBER = """namespace Catalog;

public static class Extensions
{
    public static void AddServices(this IHostApplicationBuilder builder)
    {
        builder.AddRabbitMqEventBus("eventbus")
               .AddSubscription<OrderPaidIntegrationEvent, OrderPaidIntegrationEventHandler>();
    }
}
"""

_HANDLER = """namespace {ns};

public class OrderPaidIntegrationEventHandler : IIntegrationEventHandler<OrderPaidIntegrationEvent>
{{
    public Task Handle(OrderPaidIntegrationEvent @event) => Task.CompletedTask;
}}
"""


def test_events_redeclared_per_service_link_by_short_type_name(tmp_path: Path) -> None:
    ordering = _repo(
        tmp_path,
        "ordering",
        {"Events/OrderPaid.cs": _event("Ordering.Events"), "Handlers/PaidHandler.cs": _PUBLISHER},
    )
    catalog = _repo(
        tmp_path,
        "catalog",
        {
            "Events/OrderPaid.cs": _event("Catalog.IntegrationEvents"),
            "Extensions.cs": _SUBSCRIBER,
            "Handlers/OrderPaidHandler.cs": _HANDLER.format(ns="Catalog"),
        },
    )
    # Only a handler class subscribes here: the bus wires it by assembly scan.
    webhooks = _repo(
        tmp_path,
        "webhooks",
        {
            "OrderPaid.cs": _event("Webhooks.Events"),
            "OrderPaidHandler.cs": _HANDLER.format(ns="Webhooks"),
        },
    )
    assert _rows(ordering) == {("provider", _PAID, "Handlers/PaidHandler.cs", 7)}
    # The registration is the subscription; the handler class adds no second row.
    assert _rows(catalog) == {("consumer", _PAID, "Extensions.cs", 8)}
    assert _rows(webhooks) == {("consumer", _PAID, "OrderPaidHandler.cs", 3)}
    links = {
        (link.provider_repo, link.consumer_repo, link.contract_id)
        for link in match_contracts(ordering + catalog + webhooks)
    }
    assert links == {("ordering", "catalog", _PAID), ("ordering", "webhooks", _PAID)}


def test_publish_origin_says_how_the_event_reached_the_call(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Events.cs": _event("Svc", "AInteg") + _event("Svc.B", "BInteg"),
            "Pub.cs": (
                "class P {\n"
                "  async Task Run(IEventBus bus) {\n"
                "    await bus.PublishAsync(new AInteg(1));\n"
                "    var b = new BInteg(2);\n"
                "    await bus.PublishAsync(b);\n"
                "  }\n}\n"
            ),
        },
    )
    origins = {c.contract_id: c.origin for c in rows}
    assert origins == {
        "topic::ainteg": ContractOrigin.CLIENT_CALL,
        "topic::binteg": ContractOrigin.DATAFLOW,
    }


def test_an_outbox_method_taking_the_base_type_is_a_publish_call(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "ordering",
        {
            "Events.cs": _event("Ordering"),
            "IOutbox.cs": "public interface IOutbox\n{\n    Task AddAndSaveEventAsync(IntegrationEvent evt);\n}\n",
            # A ternary and an if/else both assign the variable that reaches the call.
            "Handler.cs": (
                "class H {\n  async Task Run(bool ok) {\n"
                "    IntegrationEvent e;\n"
                "    if (ok) { e = new OrderPaidIntegrationEvent(1); }\n"
                "    else { e = new OrderPaidIntegrationEvent(2); }\n"
                "    await _outbox.AddAndSaveEventAsync(e);\n  }\n}\n"
            ),
        },
    )
    assert _rows(rows) == {("provider", _PAID, "Handler.cs", 4)}


def test_a_construction_that_reaches_no_publish_call_is_not_a_publisher(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Events.cs": _event("Svc"),
            "Mapper.cs": (
                "class M {\n  object Map(int id) {\n"
                "    var e = new OrderPaidIntegrationEvent(id);\n"
                "    _log.Write(e);\n    return e;\n  }\n}\n"
            ),
        },
    )
    assert rows == []


def test_a_type_not_derived_from_the_event_base_is_not_published(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Msg.cs": "namespace Svc;\npublic record OrderSubmitted(int Id);\n",
            "Pub.cs": "class P {\n  Task Run() => _bus.Publish(new OrderSubmitted(1));\n}\n",
        },
    )
    assert rows == []


def test_an_event_derived_through_another_event_type_is_read(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Base.cs": _event("Svc", "OrderEventBase"),
            "Paid.cs": _event("Svc", base="OrderEventBase"),
            "Pub.cs": "class P {\n  Task Run() => _bus.PublishAsync(new OrderPaidIntegrationEvent(1));\n}\n",
        },
    )
    assert _rows(rows) == {("provider", _PAID, "Pub.cs", 2)}


def test_the_event_bus_library_itself_yields_nothing(tmp_path: Path) -> None:
    # The generic registration and handler interface name type parameters, not events.
    rows = _repo(
        tmp_path,
        "eventbus",
        {
            "IIntegrationEventHandler.cs": (
                "public interface IIntegrationEventHandler<in TIntegrationEvent> : IIntegrationEventHandler\n"
                "    where TIntegrationEvent : IntegrationEvent\n{\n"
                "    Task Handle(TIntegrationEvent @event);\n}\n"
            ),
            "EventBusBuilderExtensions.cs": (
                "public static class EventBusBuilderExtensions\n{\n"
                "    public static IEventBusBuilder AddSubscription<T, TH>(this IEventBusBuilder b)\n"
                "        where T : IntegrationEvent\n"
                "        where TH : class, IIntegrationEventHandler<T>\n"
                "    {\n        return b;\n    }\n}\n"
            ),
            "IEventBus.cs": "public interface IEventBus\n{\n    Task PublishAsync(IntegrationEvent @event);\n}\n",
            "IntegrationEvent.cs": "public record IntegrationEvent\n{\n    public Guid Id { get; set; }\n}\n",
        },
    )
    assert rows == []


def test_a_subscription_to_a_type_the_repo_declares_as_no_event_is_refused(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Paid.cs": "namespace Svc;\npublic class OrderPaidIntegrationEvent { }\n",
            "Extensions.cs": _SUBSCRIBER,
        },
    )
    assert rows == []


def test_a_subscription_to_a_type_declared_in_a_shared_contract_repo_is_read(tmp_path: Path) -> None:
    rows = _repo(tmp_path, "svc", {"Extensions.cs": _SUBSCRIBER})
    assert _rows(rows) == {("consumer", _PAID, "Extensions.cs", 8)}


def test_subscribe_needs_an_event_the_repo_declares(tmp_path: Path) -> None:
    startup = (
        "class Startup {\n  void Configure() {\n"
        "    eventBus.Subscribe<OrderPaidIntegrationEvent, IIntegrationEventHandler<OrderPaidIntegrationEvent>>();\n"
        "    observable.Subscribe<Tick, TickObserver>();\n  }\n}\n"
    )
    rows = _repo(tmp_path, "svc", {"Events.cs": _event("Svc"), "Startup.cs": startup})
    assert _rows(rows) == {("consumer", _PAID, "Startup.cs", 3)}


def test_a_commented_out_subscription_is_not_read(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {"Extensions.cs": _SUBSCRIBER.replace("               .AddSubscription", "            // .AddSubscription")},
    )
    assert rows == []


def test_a_same_named_variable_in_another_method_is_another_value(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Events.cs": _event("Svc"),
            "Svc.cs": (
                "class S {\n"
                "  void Build(int id) {\n    var e = new OrderPaidIntegrationEvent(id);\n    Log(e);\n  }\n"
                "  Task Send(IntegrationEvent other) {\n    var e = other;\n    return bus.PublishAsync(e);\n  }\n}\n"
            ),
        },
    )
    assert rows == []


def test_only_a_method_named_for_publishing_is_a_publish_call(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Events.cs": _event("Svc"),
            "Audit.cs": (
                "class A {\n  void Log(IntegrationEvent e) { }\n"
                "  void Check(object x) { if (x is IntegrationEvent e) { } }\n}\n"
            ),
            "Svc.cs": (
                "class S {\n  void Run(int id) {\n"
                "    var e = new OrderPaidIntegrationEvent(id);\n    _audit.Log(e);\n"
                "    if (new OrderPaidIntegrationEvent(2) != null) { }\n  }\n}\n"
            ),
        },
    )
    assert rows == []


def test_a_file_publishing_only_through_an_outbox_method_is_read(tmp_path: Path) -> None:
    # The handler names neither the event base nor `Publish`: the outbox method is its only tie.
    rows = _repo(
        tmp_path,
        "ordering",
        {
            "Events.cs": _event("Ordering", "OrderPaid"),
            "IOutbox.cs": "public interface IOutbox\n{\n    Task AddAndSaveEventAsync(IntegrationEvent evt);\n}\n",
            "Handler.cs": (
                "class H {\n  async Task Run(int id) {\n"
                "    var e = new OrderPaid(id);\n"
                "    await _outbox.AddAndSaveEventAsync(e);\n  }\n}\n"
            ),
        },
    )
    assert _rows(rows) == {("provider", "topic::orderpaid", "Handler.cs", 3)}


def test_the_event_variable_may_follow_several_arguments(tmp_path: Path) -> None:
    rows = _repo(
        tmp_path,
        "svc",
        {
            "Events.cs": _event("Svc"),
            "Pub.cs": (
                "class P {\n  async Task Run(int id) {\n"
                "    var e = new OrderPaidIntegrationEvent(id);\n"
                '    await bus.PublishAsync(tenantId, "x", e);\n  }\n}\n'
            ),
        },
    )
    assert _rows(rows) == {("provider", _PAID, "Pub.cs", 3)}
