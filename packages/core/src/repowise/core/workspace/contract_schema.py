"""Schema-level shape for a contract — the request/response field structure.

A :class:`Contract` carries the *identity* of an API surface (its method + path,
its ``service/method``, its topic). This module adds the optional *shape*: the
fields a request or response carries, when a parser can recover them. Only the
gRPC ``.proto`` dialect populates this today (message fields, reusing the
existing proto parser); HTTP gains a schema when an OpenAPI spec is present — a
new :class:`ContractSchema` ``source`` slots in without touching the consumers
here or the breaking-change rules that read it.

Kept deliberately transport-neutral so one diff engine
(:mod:`repowise.core.workspace.breaking_change`) can reason over every schema the
same way: a field has a ``name``, a ``type``, whether it is ``required``, and an
optional wire ``number`` (proto) for field-number-reuse detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SchemaField:
    """One field in a request or response shape.

    ``number`` is the proto field tag (``None`` for transports without one).
    ``repeated`` carries proto list-ness; it is informational for diffing.
    """

    name: str
    type: str
    required: bool = False
    number: int | None = None
    repeated: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.required:
            d["required"] = True
        if self.number is not None:
            d["number"] = self.number
        if self.repeated:
            d["repeated"] = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaField:
        return cls(
            name=data["name"],
            type=data.get("type", ""),
            required=bool(data.get("required", False)),
            number=data.get("number"),
            repeated=bool(data.get("repeated", False)),
        )


@dataclass
class ContractSchema:
    """The structured request/response shape of a contract, when recoverable.

    ``source`` records which parser produced it (``"proto"`` / ``"openapi"``) so
    a diff never compares shapes captured by two different extraction strategies
    as if they were the same fidelity.
    """

    source: str
    request_fields: list[SchemaField] = field(default_factory=list)
    response_fields: list[SchemaField] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.request_fields and not self.response_fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "request_fields": [f.to_dict() for f in self.request_fields],
            "response_fields": [f.to_dict() for f in self.response_fields],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractSchema:
        return cls(
            source=data.get("source", ""),
            request_fields=[SchemaField.from_dict(f) for f in data.get("request_fields", [])],
            response_fields=[SchemaField.from_dict(f) for f in data.get("response_fields", [])],
        )
