"""Symbol response models."""

from __future__ import annotations

from pydantic import BaseModel


class SymbolImportanceComponents(BaseModel):
    """Transparent breakdown of the composite importance score so the UI can
    explain *why* a symbol ranks where it does. All fields are normalized to
    [0, 1] except booleans."""

    file_pagerank: float = 0.0
    visibility_factor: float = 0.5
    complexity_norm: float = 0.0
    kind_boost: float = 1.0
    is_entry_point: bool = False


class SymbolResponse(BaseModel):
    id: str
    repository_id: str
    file_path: str
    symbol_id: str
    name: str
    qualified_name: str
    kind: str
    signature: str
    start_line: int
    end_line: int
    docstring: str | None
    visibility: str
    is_async: bool
    complexity_estimate: int
    language: str
    parent_name: str | None
    # Importance signals (populated when the list endpoint joins GraphNode /
    # GitMetadata; nullable so single-symbol lookups remain lightweight).
    importance_score: float | None = None
    importance_components: SymbolImportanceComponents | None = None
    file_pagerank: float | None = None
    is_entry_point: bool | None = None
    file_churn_percentile: float | None = None
    file_is_hotspot: bool | None = None
    # Function-blame join (populated by the list endpoint from
    # git_function_blame when a row exists for the symbol; null otherwise).
    blame_mod_count: int | None = None
    blame_recent_mod_count: int | None = None
    blame_median_author_time: int | None = None
    blame_owner_name: str | None = None
    blame_owner_line_pct: float | None = None
    # Counted bug fixes attributed to this symbol (from the per-file rollup;
    # null when the index predates it, 0 when it ran and found none here).
    fix_count: int | None = None

    @classmethod
    def from_orm(cls, obj: object) -> SymbolResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            file_path=obj.file_path,  # type: ignore[attr-defined]
            symbol_id=obj.symbol_id,  # type: ignore[attr-defined]
            name=obj.name,  # type: ignore[attr-defined]
            qualified_name=obj.qualified_name,  # type: ignore[attr-defined]
            kind=obj.kind,  # type: ignore[attr-defined]
            signature=obj.signature,  # type: ignore[attr-defined]
            start_line=obj.start_line,  # type: ignore[attr-defined]
            end_line=obj.end_line,  # type: ignore[attr-defined]
            docstring=obj.docstring,  # type: ignore[attr-defined]
            visibility=obj.visibility,  # type: ignore[attr-defined]
            is_async=obj.is_async,  # type: ignore[attr-defined]
            complexity_estimate=obj.complexity_estimate,  # type: ignore[attr-defined]
            language=obj.language,  # type: ignore[attr-defined]
            parent_name=obj.parent_name,  # type: ignore[attr-defined]
        )
