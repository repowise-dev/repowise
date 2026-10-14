"""Evidence primitives shared by renderer-neutral contracts.

Twin of ``packages/types/src/evidence.ts`` (how a value is known, and which
entity it points at), validated as strictly as the TypeScript validator in
``@repowise-dev/api-client``: unknown fields, coerced types, non-finite
numbers and an optional field sent as null are all rejected, so a payload
either side accepts the other accepts too.

Numbers are bounded by ``MAX_WIRE_NUMBER`` (2^53 - 1), the largest integer a
JavaScript reader keeps exact. Integer fields are sent as JSON integers: strict
mode rejects ``5.0`` and ``1e3`` there, which a JavaScript reader cannot tell
apart from ``5`` and ``1000``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_WIRE_NUMBER = 2**53 - 1
WireInt = Annotated[int, Field(ge=-MAX_WIRE_NUMBER, le=MAX_WIRE_NUMBER)]
WireFloat = Annotated[float, Field(ge=-MAX_WIRE_NUMBER, le=MAX_WIRE_NUMBER)]


class StrictContract(BaseModel):
    """A wire contract shared with TypeScript: strict, closed, absent over null."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _optional_means_absent(cls, data: Any) -> Any:
        # TypeScript optional fields are absent, never null.
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                if not field.is_required() and name in data and data[name] is None:
                    raise ValueError(f"{name}: omit an optional field rather than send null")
        return data


CardBasisKind = Literal["measured", "inferred", "declared", "unresolved"]
CARD_BASIS_KINDS: tuple[str, ...] = get_args(CardBasisKind)

CardRefType = Literal[
    "repo", "file", "symbol", "module", "component", "commit", "change", "decision"
]
CARD_REF_TYPES: tuple[str, ...] = get_args(CardRefType)


class CardBasis(StrictContract):
    kind: CardBasisKind
    source: str = Field(min_length=1)


class CardRef(StrictContract):
    """An entity a card or view points at. Never a URL; the renderer resolves it."""

    type: CardRefType
    id: str = Field(min_length=1)
    start_line: int | None = Field(default=None, ge=1, le=MAX_WIRE_NUMBER)
    end_line: int | None = Field(default=None, ge=1, le=MAX_WIRE_NUMBER)

    @model_validator(mode="after")
    def _line_rules(self) -> CardRef:
        has_lines = self.start_line is not None or self.end_line is not None
        if has_lines and self.type not in ("file", "symbol"):
            raise ValueError("line numbers only apply to file and symbol refs")
        if self.end_line is not None and self.start_line is None:
            raise ValueError("end_line requires start_line")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line is before start_line")
        return self
