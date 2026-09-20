"""Transport-neutral request and response shapes for workspace contracts.

The legacy protobuf and signature sources use flat fields. OpenAPI adds optional
wire metadata and recursive children without changing those existing serialized
rows. Every optional attribute is omitted when absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SchemaField:
    """One field or root node in a request or response shape."""

    name: str
    type: str
    required: bool = False
    number: int | None = None
    repeated: bool = False
    nullable: bool | None = None
    enum_values: list[Any] | None = None
    location: str | None = None
    source_pointer: str | None = None
    children: list[SchemaField] = field(default_factory=list)
    items: SchemaField | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.required:
            result["required"] = True
        if self.number is not None:
            result["number"] = self.number
        if self.repeated:
            result["repeated"] = True
        if self.nullable is not None:
            result["nullable"] = self.nullable
        if self.enum_values is not None:
            result["enum_values"] = list(self.enum_values)
        if self.location is not None:
            result["location"] = self.location
        if self.source_pointer is not None:
            result["source_pointer"] = self.source_pointer
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        if self.items is not None:
            result["items"] = self.items.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaField:
        return cls(
            name=data["name"],
            type=data.get("type", ""),
            required=bool(data.get("required", False)),
            number=data.get("number"),
            repeated=bool(data.get("repeated", False)),
            nullable=data.get("nullable"),
            enum_values=(list(data["enum_values"]) if "enum_values" in data else None),
            location=data.get("location"),
            source_pointer=data.get("source_pointer"),
            children=[cls.from_dict(child) for child in data.get("children", [])],
            items=cls.from_dict(data["items"]) if data.get("items") else None,
        )


@dataclass(frozen=True)
class SchemaIssue:
    """Why one request or response side was not recovered completely."""

    code: str
    side: str
    source_pointer: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        result = {
            "code": self.code,
            "side": self.side,
            "source_pointer": self.source_pointer,
        }
        if self.detail:
            result["detail"] = self.detail
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaIssue:
        return cls(
            code=str(data.get("code", "openapi_unknown")),
            side=str(data.get("side", "unknown")),
            source_pointer=str(data.get("source_pointer", "")),
            detail=str(data.get("detail", "")),
        )


@dataclass
class ContractSchema:
    """The structured request/response shape of a contract, when recoverable.

    The source names the producing parser. ``comparison_ready`` lets an extractor
    opt into the registry only when rules understand its fidelity and direction.
    """

    source: str
    request_fields: list[SchemaField] = field(default_factory=list)
    response_fields: list[SchemaField] = field(default_factory=list)
    source_version: str | None = None
    comparison_key: str | None = None
    comparison_ready: bool = True
    request_state: str | None = None
    response_state: str | None = None
    request_media_type: str | None = None
    response_media_type: str | None = None
    response_status_code: str | None = None
    issues: list[SchemaIssue] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return (
            not self.request_fields
            and not self.response_fields
            and self.source_version is None
            and self.comparison_key is None
            and self.comparison_ready
            and self.request_state is None
            and self.response_state is None
            and self.request_media_type is None
            and self.response_media_type is None
            and self.response_status_code is None
            and not self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "request_fields": [item.to_dict() for item in self.request_fields],
            "response_fields": [item.to_dict() for item in self.response_fields],
        }
        optional = {
            "source_version": self.source_version,
            "comparison_key": self.comparison_key,
            "request_state": self.request_state,
            "response_state": self.response_state,
            "request_media_type": self.request_media_type,
            "response_media_type": self.response_media_type,
            "response_status_code": self.response_status_code,
        }
        result.update({key: value for key, value in optional.items() if value is not None})
        if not self.comparison_ready:
            result["comparison_ready"] = False
        if self.issues:
            result["issues"] = [issue.to_dict() for issue in self.issues]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractSchema:
        return cls(
            source=data.get("source", ""),
            request_fields=[SchemaField.from_dict(item) for item in data.get("request_fields", [])],
            response_fields=[
                SchemaField.from_dict(item) for item in data.get("response_fields", [])
            ],
            source_version=data.get("source_version"),
            comparison_key=data.get("comparison_key"),
            comparison_ready=bool(data.get("comparison_ready", True)),
            request_state=data.get("request_state"),
            response_state=data.get("response_state"),
            request_media_type=data.get("request_media_type"),
            response_media_type=data.get("response_media_type"),
            response_status_code=data.get("response_status_code"),
            issues=[SchemaIssue.from_dict(issue) for issue in data.get("issues", [])],
        )
