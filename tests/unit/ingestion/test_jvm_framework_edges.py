"""Unit tests for Phase 4 JVM framework edges (Spring expansion + Jakarta + Quarkus + Micronaut + Android)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str, language: str) -> FileInfo:
    return FileInfo(
        path=rel, abs_path=abs_path, language=language,
        size_bytes=100, git_hash="", last_modified=datetime.now(),
        is_test=False, is_config=False, is_api_contract=False, is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for src in list(repo.rglob("*.java")) + list(repo.rglob("*.kt")):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        lang = "java" if src.suffix == ".java" else "kotlin"
        fi = _file_info(rel, str(src.resolve()), lang)
        out[rel] = parser.parse_file(fi, src.read_bytes())
    # Also pick up AndroidManifest.xml as a 'parsed' shell so the handler can
    # locate its abs_path. We don't really parse XML.
    for src in repo.rglob("AndroidManifest.xml"):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(src.resolve()), "xml")
        # Build a minimal ParsedFile by reusing parser on empty Java syntax
        # — but we only need file_info, so synthesize directly.
        from repowise.core.ingestion.models import ParsedFile as _PF
        out[rel] = _PF(file_info=fi, symbols=[], imports=[], exports=[])
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem_map.setdefault(Path(p).stem.lower(), []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


def _seed_graph(parsed: dict[str, ParsedFile]) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in parsed:
        g.add_node(p, node_type="file", path=p)
    return g


class TestSpringDataRepository:
    def test_jparepository_marked_entry_point(self, tmp_path: Path) -> None:
        (tmp_path / "User.java").write_text(
            "package com.x;\n"
            "import jakarta.persistence.Entity;\n"
            "@Entity public class User {}\n"
        )
        (tmp_path / "UserRepo.java").write_text(
            "package com.x;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface UserRepo extends JpaRepository<User, Long> {\n"
            "  User findByEmail(String email);\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _seed_graph(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["spring"])
        assert graph.nodes["UserRepo.java"].get("is_entry_point") is True
        assert graph.nodes["UserRepo.java"].get("framework_role") == "spring_data_repository"


class TestLombokRAC:
    def test_required_args_constructor_emits_ctor_edges(self, tmp_path: Path) -> None:
        (tmp_path / "UserService.java").write_text(
            "package com.x;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service public class UserService {}\n"
        )
        (tmp_path / "OrderService.java").write_text(
            "package com.x;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service public class OrderService {}\n"
        )
        (tmp_path / "Controller.java").write_text(
            "package com.x;\n"
            "import lombok.RequiredArgsConstructor;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "@RestController\n@RequiredArgsConstructor\n"
            "public class Controller {\n"
            "  private final UserService users;\n"
            "  private final OrderService orders;\n"
            "  private String name;\n"  # non-final, not in RAC
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _seed_graph(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["spring"])
        assert graph.has_edge("Controller.java", "UserService.java")
        assert graph.has_edge("Controller.java", "OrderService.java")


class TestJakarta:
    def test_jaxrs_path_marks_entry_point(self, tmp_path: Path) -> None:
        (tmp_path / "Books.java").write_text(
            "package com.x;\n"
            "import jakarta.ws.rs.Path;\n"
            "import jakarta.ws.rs.GET;\n"
            "@Path(\"/books\") public class Books {\n"
            "  @GET public String list() { return \"\"; }\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _seed_graph(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["jakarta"])
        assert graph.nodes["Books.java"].get("is_entry_point") is True
        assert graph.nodes["Books.java"].get("framework_role") == "jax_rs_resource"

    def test_jpa_entity_emits_one_to_many_edge(self, tmp_path: Path) -> None:
        (tmp_path / "Order.java").write_text(
            "package com.x;\n"
            "import jakarta.persistence.Entity;\n"
            "@Entity public class Order {}\n"
        )
        (tmp_path / "User.java").write_text(
            "package com.x;\n"
            "import java.util.Set;\n"
            "import jakarta.persistence.Entity;\n"
            "import jakarta.persistence.OneToMany;\n"
            "@Entity public class User {\n"
            "  @OneToMany\n  private Set<Order> orders;\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _seed_graph(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["jakarta"])
        assert graph.has_edge("User.java", "Order.java")


class TestQuarkus:
    def test_incoming_outgoing_cross_link(self, tmp_path: Path) -> None:
        (tmp_path / "Producer.java").write_text(
            "package com.x;\n"
            "import org.eclipse.microprofile.reactive.messaging.Outgoing;\n"
            "public class Producer {\n"
            "  @Outgoing(\"orders\") public String emit() { return \"\"; }\n"
            "}\n"
        )
        (tmp_path / "Consumer.java").write_text(
            "package com.x;\n"
            "import org.eclipse.microprofile.reactive.messaging.Incoming;\n"
            "public class Consumer {\n"
            "  @Incoming(\"orders\") public void on(String s) {}\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _seed_graph(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["quarkus"])
        assert graph.has_edge("Producer.java", "Consumer.java")


class TestMicronaut:
    def test_micronaut_controller_marks_entry(self, tmp_path: Path) -> None:
        (tmp_path / "Hi.java").write_text(
            "package com.x;\n"
            "import io.micronaut.http.annotation.Controller;\n"
            "@Controller(\"/hi\") public class Hi {}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _seed_graph(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["micronaut"])
        assert graph.nodes["Hi.java"].get("is_entry_point") is True


class TestAndroidManifest:
    def test_manifest_links_to_activity_class(self, tmp_path: Path) -> None:
        # Source class
        src_dir = tmp_path / "app" / "src" / "main" / "java" / "com" / "x"
        src_dir.mkdir(parents=True)
        (src_dir / "MainActivity.java").write_text(
            "package com.x;\npublic class MainActivity {}\n"
        )
        # Manifest
        manifest_dir = tmp_path / "app" / "src" / "main"
        (manifest_dir / "AndroidManifest.xml").write_text(
            "<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\">\n"
            "  <application>\n"
            "    <activity android:name=\"com.x.MainActivity\" />\n"
            "  </application>\n"
            "</manifest>\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _seed_graph(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx)
        manifest_rel = "app/src/main/AndroidManifest.xml"
        activity_rel = "app/src/main/java/com/x/MainActivity.java"
        assert graph.has_edge(manifest_rel, activity_rel)
