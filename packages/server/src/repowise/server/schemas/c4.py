"""C4 diagram response models (L1 System Context, L2 Containers, L3 Components)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class C4PersonResponse(BaseModel):
    id: str
    name: str
    description: str = ""


class C4SystemResponse(BaseModel):
    id: str
    name: str
    description: str = ""


class C4ExternalSystemResponse(BaseModel):
    id: str
    name: str
    display_name: str
    category: str  # framework | service | tool | library
    ecosystem: str
    version: str | None = None
    io_kind: str | None = None  # db | network | filesystem | subprocess | lock | null


class C4ContainerResponse(BaseModel):
    id: str
    name: str
    path: str
    language: str
    file_count: int
    symbol_count: int
    hotspot_count: int = 0
    dead_count: int = 0


class C4ComponentResponse(BaseModel):
    id: str
    name: str
    path: str
    container_id: str
    file_count: int
    symbol_count: int


class C4RelationResponse(BaseModel):
    source_id: str
    target_id: str
    label: str = ""
    edge_count: int = 1
    edge_types: list[str] = Field(default_factory=list)


class C4L1Response(BaseModel):
    system: C4SystemResponse
    people: list[C4PersonResponse]
    external_systems: list[C4ExternalSystemResponse]
    relations: list[C4RelationResponse]


class C4L2Response(BaseModel):
    containers: list[C4ContainerResponse]
    external_systems: list[C4ExternalSystemResponse]
    relations: list[C4RelationResponse]


class C4L3Response(BaseModel):
    container: C4ContainerResponse
    components: list[C4ComponentResponse]
    external_systems: list[C4ExternalSystemResponse]
    relations: list[C4RelationResponse]
