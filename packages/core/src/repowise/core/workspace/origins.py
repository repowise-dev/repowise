"""How a workspace contract was read and how a cross-repo link was made.

Two closed vocabularies, each value carrying exactly one confidence, the same
rule ``docs/layers/GRAPH.md`` applies to call edges:

* :class:`ContractOrigin` names what established a contract: a framework
  registration, a schema document, a client call, a naming convention. Every
  dialect declares one of these as data and :func:`.extractors.dialect.build_contract`
  turns it into the contract's ``confidence``. A new dialect picks an existing
  origin; a genuinely new way of establishing a contract adds one row here.
* :class:`LinkStrategy` names the matching rule that joined a consumer to a
  provider. A link's confidence is the weaker end's confidence times its
  strategy's, so the strategy distribution and the confidence histogram are
  two views of the same data.

``match_type`` (exact / candidate / manual), which the system graph, the API
and the web views read, is derived from the strategy and never set on its own.
"""

from __future__ import annotations

from enum import StrEnum


class ContractOrigin(StrEnum):
    """What established a contract. Confidence per value: :data:`ORIGIN_CONFIDENCE`."""

    #: An OpenAPI or proto document declares the operation.
    IDL = "idl"
    #: A published package exports the symbol another repo imports.
    PACKAGE_EXPORT = "package_export"
    #: A framework registers the route, handler, server or hub in code.
    ROUTE_REGISTRATION = "route_registration"
    #: The parsed index types the receiver as an HTTP client.
    TYPED_CLIENT = "typed_client"
    #: SQL DDL or a migration creates or alters the table.
    DDL = "ddl"
    #: An ORM model names its table explicitly.
    EXPLICIT_TABLE = "explicit_table"
    #: A known client, broker, socket or query-builder call names the destination.
    CLIENT_CALL = "client_call"
    #: A call on a client instance whose base URL the file reads.
    INSTANCE_BASE = "instance_base"
    #: The destination was traced across statements to the call.
    DATAFLOW = "dataflow"
    #: A table named inside SQL text in a string.
    SQL_STRING = "sql_string"
    #: The call shape matches, but only a callee or receiver name says it is a client.
    NAME_HEURISTIC = "name_heuristic"
    #: Derived from a naming convention: an ORM's default table name, a unique short type name.
    NAME_CONVENTION = "name_convention"


ORIGIN_CONFIDENCE: dict[ContractOrigin, float] = {
    ContractOrigin.IDL: 0.90,
    ContractOrigin.PACKAGE_EXPORT: 0.90,
    ContractOrigin.ROUTE_REGISTRATION: 0.85,
    ContractOrigin.TYPED_CLIENT: 0.85,
    ContractOrigin.DDL: 0.85,
    ContractOrigin.EXPLICIT_TABLE: 0.85,
    ContractOrigin.CLIENT_CALL: 0.75,
    ContractOrigin.INSTANCE_BASE: 0.75,
    ContractOrigin.DATAFLOW: 0.70,
    ContractOrigin.SQL_STRING: 0.70,
    ContractOrigin.NAME_HEURISTIC: 0.65,
    ContractOrigin.NAME_CONVENTION: 0.60,
}


class LinkStrategy(StrEnum):
    """The rule that joined a consumer to a provider. Confidence: :data:`STRATEGY_CONFIDENCE`."""

    #: Both ends name the same normalized id (HTTP method and gRPC service wildcards included).
    EXACT_ID = "exact_id"
    #: The consumer's subscription pattern (``orders.*``) matches the provider's name.
    PATTERN_MATCHED = "pattern_matched"
    #: A queue bound to the exchange the provider publishes to, with a routing key it receives.
    BINDING_ROUTED = "binding_routed"
    #: A service that only models a table, linked to the one service whose schema defines it.
    SHARED_TABLE = "shared_table"
    #: The consumer's host or base token maps to the providing service.
    SERVICE_MAPPED = "service_mapped"
    #: The consumer's host or base token maps to a service, but no route there serves
    #: its path: a service-level dependency, not a route link.
    HOST_ONLY = "host_only"
    #: A gateway's config forwards a path prefix to the service: the gateway's
    #: own edge to it, and a call through the gateway linked on the path the
    #: route forwards (see :mod:`.service_names.spring_gateway`).
    GATEWAY_ROUTE = "gateway_route"
    #: The consumer's base is unread and exactly one service serves the path.
    UNIQUE_PATH = "unique_path"
    #: The consumer's base is unread and several services of one repo serve the path;
    #: linked to each. Services in two or more repos are not linked at all.
    AMBIGUOUS_PATH = "ambiguous_path"
    #: Matched only after stripping mount, version or base prefixes from both paths.
    PREFIX_STRIPPED = "prefix_stripped"
    #: A concrete path segment in the call (``/render/1``) met a provider's ``{param}``
    #: segment (``/render/{param}``), every other segment equal.
    PARAM_SEGMENT = "param_segment"
    #: Declared in the workspace config.
    MANUAL = "manual"
    #: A manifest declares the package dependency (system-graph package edges only).
    MANIFEST = "manifest"


STRATEGY_CONFIDENCE: dict[LinkStrategy, float] = {
    LinkStrategy.EXACT_ID: 1.0,
    LinkStrategy.PATTERN_MATCHED: 1.0,
    LinkStrategy.BINDING_ROUTED: 1.0,
    LinkStrategy.SHARED_TABLE: 1.0,
    LinkStrategy.SERVICE_MAPPED: 1.0,
    LinkStrategy.HOST_ONLY: 0.5,
    LinkStrategy.GATEWAY_ROUTE: 1.0,
    LinkStrategy.UNIQUE_PATH: 1.0,
    LinkStrategy.AMBIGUOUS_PATH: 0.6,
    LinkStrategy.PREFIX_STRIPPED: 0.6,
    LinkStrategy.PARAM_SEGMENT: 0.6,
    LinkStrategy.MANUAL: 1.0,
    LinkStrategy.MANIFEST: 1.0,
}

_CANDIDATE = frozenset(
    {
        LinkStrategy.AMBIGUOUS_PATH,
        LinkStrategy.PREFIX_STRIPPED,
        LinkStrategy.PARAM_SEGMENT,
        LinkStrategy.HOST_ONLY,
    }
)


def origin_confidence(origin: str) -> float:
    """The one confidence *origin* carries. Raises ``ValueError`` outside the vocabulary."""
    return ORIGIN_CONFIDENCE[ContractOrigin(origin)]


def match_type_for(strategy: str) -> str:
    """The ``match_type`` a link made by *strategy* is shown as: exact, candidate or manual."""
    s = LinkStrategy(strategy)
    if s is LinkStrategy.MANUAL:
        return "manual"
    return "candidate" if s in _CANDIDATE else "exact"


def link_confidence(strategy: str, *end_confidences: float) -> float:
    """The weakest end's confidence scaled by *strategy*'s, rounded as links store it."""
    return round(min(end_confidences) * STRATEGY_CONFIDENCE[LinkStrategy(strategy)], 3)


def legacy_strategy(match_type: str) -> str | None:
    """The strategy a link stored before strategies existed most likely had.

    A guess: a stored ``candidate`` may have been an ambiguous path rather than
    a stripped prefix. A compatibility read only: such a store is re-extracted on the next update
    (its version is older), and until then the reads it serves name a strategy.
    """
    return {
        "exact": LinkStrategy.EXACT_ID,
        "candidate": LinkStrategy.PREFIX_STRIPPED,
        "manual": LinkStrategy.MANUAL,
    }.get(match_type)


__all__ = [
    "ORIGIN_CONFIDENCE",
    "STRATEGY_CONFIDENCE",
    "ContractOrigin",
    "LinkStrategy",
    "legacy_strategy",
    "link_confidence",
    "match_type_for",
    "origin_confidence",
]
