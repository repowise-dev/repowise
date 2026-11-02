"""The one rule for a row's ``preview`` label on every REST surface."""

from __future__ import annotations

from pydantic import BaseModel, model_serializer


class PreviewLabelled(BaseModel):
    """A model whose ``preview`` is sent only when the row sits on a preview
    language cell, as ``finding_registry.label_previews`` and the MCP and CLI
    rows already do; ``None`` is left out. Each subclass declares the field
    and lists ``BaseModel`` too, so ruff reads it as a model."""

    @model_serializer(mode="wrap")
    def _omit_absent_preview(self, handler):  # no return annotation: keeps the schema
        data = handler(self)
        if getattr(self, "preview", None) is None:
            data.pop("preview", None)
        return data
