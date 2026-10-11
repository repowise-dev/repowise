"""Unit tests for Java sealed/permits heritage and JPMS provides scanning."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.jvm_workspace import (
    build_jvm_workspace_index,
)


def _fi(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


class TestSealedPermits:
    def test_permits_emits_subclass_edges(self, tmp_path: Path) -> None:
        src = (
            "package com.foo;\n"
            "public sealed class Shape permits Circle, Square {}\n"
        )
        rel = "Shape.java"
        abs_ = tmp_path / rel
        abs_.write_text(src)
        pf = parse_file(_fi(rel, abs_, "java"), src.encode("utf-8"))
        heritage = pf.heritage
        # Expect two relations: Circle→Shape, Square→Shape, both extends.
        kinds = sorted((h.child_name, h.parent_name, h.kind) for h in heritage)
        assert ("Circle", "Shape", "extends") in kinds, kinds
        assert ("Square", "Shape", "extends") in kinds, kinds

    def test_sealed_interface_permits(self, tmp_path: Path) -> None:
        src = (
            "package com.foo;\n"
            "public sealed interface IFace permits A, B {}\n"
        )
        rel = "IFace.java"
        abs_ = tmp_path / rel
        abs_.write_text(src)
        pf = parse_file(_fi(rel, abs_, "java"), src.encode("utf-8"))
        kinds = sorted((h.child_name, h.parent_name, h.kind) for h in pf.heritage)
        assert ("A", "IFace", "extends") in kinds, kinds
        assert ("B", "IFace", "extends") in kinds, kinds


class TestJpmsProvides:
    def test_module_info_provides_populates_services(self, tmp_path: Path) -> None:
        mi = tmp_path / "module-info.java"
        mi.write_text(
            "module com.foo {\n"
            "  provides com.foo.Service with com.foo.impl.MyImpl, com.foo.impl.OtherImpl;\n"
            "}\n"
        )
        ctx = ResolverContext(
            path_set={"module-info.java"},
            stem_map={"module-info": ["module-info.java"]},
            graph=nx.DiGraph(),
            repo_path=tmp_path,
        )
        index = build_jvm_workspace_index(ctx)
        assert "com.foo.Service" in index.services
        impls = index.services["com.foo.Service"]
        assert "com.foo.impl.MyImpl" in impls
        assert "com.foo.impl.OtherImpl" in impls

    def test_module_info_merges_with_meta_inf_services(self, tmp_path: Path) -> None:
        mi = tmp_path / "module-info.java"
        mi.write_text(
            "module com.foo {\n"
            "  provides com.foo.Plugin with com.foo.impl.A;\n"
            "}\n"
        )
        sdir = tmp_path / "src/main/resources/META-INF/services"
        sdir.mkdir(parents=True)
        (sdir / "com.foo.Plugin").write_text("com.foo.impl.B\n")
        ctx = ResolverContext(
            path_set={"module-info.java"},
            stem_map={"module-info": ["module-info.java"]},
            graph=nx.DiGraph(),
            repo_path=tmp_path,
        )
        index = build_jvm_workspace_index(ctx)
        impls = index.services["com.foo.Plugin"]
        assert "com.foo.impl.A" in impls
        assert "com.foo.impl.B" in impls
