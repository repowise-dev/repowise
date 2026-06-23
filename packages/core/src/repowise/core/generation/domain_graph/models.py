"""In-memory dataclasses for the behavior-oriented domain graph.

The structural knowledge graph answers "what is the code?"; the domain graph
answers "what does the system do?". It has three levels:

* :class:`DomainNode` - a capability / bounded context (e.g. "Indexing
  Pipeline"), backed by one or more structural layers.
* :class:`FlowNode` - a process within a domain (e.g. "Index a repository
  from scratch").
* :class:`StepNode` - an ordered stage in a flow, mapped to the concrete
  file/symbol node ids that implement it.

These are pure data with ``to_dict`` / ``from_dict`` round-trips so the graph
can travel through the ``knowledge-graph.json`` artifact and be validated by the
reviewer without a DB or an LLM. Flattening to persistable nodes/edges lives in
:mod:`.persistence_map`; rendering to wiki page content lives in :mod:`.render`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Domain-graph edge types (mirrors the structural graph's typed-edge model).
EDGE_CONTAINS_FLOW = "contains_flow"
EDGE_FLOW_STEP = "flow_step"
EDGE_CROSS_DOMAIN = "cross_domain"


@dataclass
class StepNode:
    """One ordered stage of a flow, mapped to real implementing node ids."""

    order: int
    name: str
    summary: str = ""
    # Real file/symbol node ids ("file:<path>") that implement this step.
    # Always validated against the structural graph before persistence.
    implements: list[str] = field(default_factory=list)

    def id(self, flow_id: str) -> str:
        return f"step:{flow_id.removeprefix('flow:')}/{self.order}"

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "name": self.name,
            "summary": self.summary,
            "implements": list(self.implements),
        }

    @classmethod
    def from_dict(cls, data: dict) -> StepNode:
        return cls(
            order=int(data.get("order", 0)),
            name=str(data.get("name", "")),
            summary=str(data.get("summary", "")),
            implements=list(data.get("implements", [])),
        )


@dataclass
class FlowNode:
    """A process within a domain - an ordered sequence of steps."""

    slug: str
    name: str
    summary: str = ""
    steps: list[StepNode] = field(default_factory=list)

    def id(self, domain_id: str) -> str:
        return f"flow:{domain_id.removeprefix('domain:')}/{self.slug}"

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> FlowNode:
        return cls(
            slug=str(data.get("slug", "")),
            name=str(data.get("name", "")),
            summary=str(data.get("summary", "")),
            steps=[StepNode.from_dict(s) for s in data.get("steps", [])],
        )


@dataclass
class DomainNode:
    """A capability / bounded context backed by structural layers."""

    slug: str
    name: str
    summary: str = ""
    # Structural layer ids this domain is built from.
    member_layer_ids: list[str] = field(default_factory=list)
    # Union of the member layers' file node ids ("file:<path>"). The validation
    # surface flows/steps may map into.
    member_node_ids: list[str] = field(default_factory=list)
    flows: list[FlowNode] = field(default_factory=list)
    # Structural fingerprint of the member set; lets a re-index reuse an
    # unchanged domain's flows/steps without a fresh LLM call.
    fingerprint: str = ""

    @property
    def id(self) -> str:
        return f"domain:{self.slug}"

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "summary": self.summary,
            "member_layer_ids": list(self.member_layer_ids),
            "member_node_ids": list(self.member_node_ids),
            "flows": [f.to_dict() for f in self.flows],
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DomainNode:
        return cls(
            slug=str(data.get("slug", "")),
            name=str(data.get("name", "")),
            summary=str(data.get("summary", "")),
            member_layer_ids=list(data.get("member_layer_ids", [])),
            member_node_ids=list(data.get("member_node_ids", [])),
            flows=[FlowNode.from_dict(f) for f in data.get("flows", [])],
            fingerprint=str(data.get("fingerprint", "")),
        )


@dataclass
class CrossDomainEdge:
    """A dependency from one domain to another, weighted by import volume."""

    source: str  # "domain:<slug>"
    target: str  # "domain:<slug>"
    weight: int = 0

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "weight": self.weight}

    @classmethod
    def from_dict(cls, data: dict) -> CrossDomainEdge:
        return cls(
            source=str(data.get("source", "")),
            target=str(data.get("target", "")),
            weight=int(data.get("weight", 0)),
        )


@dataclass
class DomainGraph:
    """The full behavior-oriented graph: domains, their flows/steps, and the
    cross-domain dependency edges between them."""

    domains: list[DomainNode] = field(default_factory=list)
    cross_domain: list[CrossDomainEdge] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.domains

    def to_dict(self) -> dict:
        return {
            "domains": [d.to_dict() for d in self.domains],
            "cross_domain": [e.to_dict() for e in self.cross_domain],
        }

    @classmethod
    def from_dict(cls, data: dict) -> DomainGraph:
        if not isinstance(data, dict):
            return cls()
        return cls(
            domains=[DomainNode.from_dict(d) for d in data.get("domains", [])],
            cross_domain=[CrossDomainEdge.from_dict(e) for e in data.get("cross_domain", [])],
        )
