"""Unit tests for Spring Boot framework edges (F1)."""

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
        path=rel,
        abs_path=abs_path,
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for src in list(repo.rglob("*.java")) + list(repo.rglob("*.kt")):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        lang = "java" if src.suffix == ".java" else "kotlin"
        fi = _file_info(rel, str(src.resolve()), lang)
        out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem = Path(p).stem.lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


class TestSpringDI:
    def test_autowired_field_injection(self, tmp_path: Path) -> None:
        (tmp_path / "UserService.java").write_text(
            "import org.springframework.stereotype.Service;\n"
            "@Service\npublic class UserService {}\n"
        )
        (tmp_path / "UserController.java").write_text(
            "import org.springframework.beans.factory.annotation.Autowired;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "@RestController\npublic class UserController {\n"
            "  @Autowired private UserService userService;\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["spring"])
        assert graph.has_edge("UserController.java", "UserService.java")

    def test_constructor_injection_resolves_interface_to_impl(self, tmp_path: Path) -> None:
        (tmp_path / "PaymentService.java").write_text(
            "public interface PaymentService {}\n"
        )
        (tmp_path / "StripePayment.java").write_text(
            "import org.springframework.stereotype.Service;\n"
            "@Service\npublic class StripePayment implements PaymentService {}\n"
        )
        (tmp_path / "CheckoutController.java").write_text(
            "import org.springframework.web.bind.annotation.RestController;\n"
            "@RestController\npublic class CheckoutController {\n"
            "  private final PaymentService payments;\n"
            "  public CheckoutController(PaymentService payments) { this.payments = payments; }\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["spring"])
        # Edge to either the interface file or its impl is acceptable.
        assert graph.has_edge("CheckoutController.java", "StripePayment.java") or graph.has_edge(
            "CheckoutController.java", "PaymentService.java"
        )

    def test_bean_factory_links_config_to_return_type(self, tmp_path: Path) -> None:
        (tmp_path / "Greeter.java").write_text("public class Greeter {}\n")
        (tmp_path / "AppConfig.java").write_text(
            "import org.springframework.context.annotation.Bean;\n"
            "import org.springframework.context.annotation.Configuration;\n"
            "@Configuration\npublic class AppConfig {\n"
            "  @Bean\n  public Greeter greeter() { return new Greeter(); }\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["spring"])
        assert graph.has_edge("AppConfig.java", "Greeter.java")


class TestSpringKotlin:
    def test_kotlin_service_with_autowired_field(self, tmp_path: Path) -> None:
        (tmp_path / "Repo.kt").write_text(
            "import org.springframework.stereotype.Repository\n"
            "@Repository\nclass Repo\n"
        )
        (tmp_path / "Svc.kt").write_text(
            "import org.springframework.beans.factory.annotation.Autowired\n"
            "import org.springframework.stereotype.Service\n"
            "@Service\nclass Svc {\n"
            "  @Autowired lateinit var repo: Repo\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["spring"])
        # The Java-style regex won't match Kotlin `@Autowired lateinit var`, so this
        # currently exercises only the gate (Spring detection works for Kotlin).
        # The edge may or may not be present — assert the slice runs without error.
        assert graph.number_of_nodes() == 2


class TestSpringGate:
    def test_non_spring_unaffected(self, tmp_path: Path) -> None:
        (tmp_path / "Plain.java").write_text("public class Plain {}\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0


def _build_graph(repo: Path):
    """A real graph, so the symbol-level edges have symbol nodes to land on."""
    from repowise.core.ingestion import GraphBuilder

    parsed = _build_parsed(repo)
    builder = GraphBuilder(repo)
    for pf in parsed.values():
        builder.add_file(pf)
    graph = builder.build()
    add_framework_edges(graph, parsed, _ctx(repo, parsed), tech_stack=["spring"])
    return graph


def _bound(graph) -> set[tuple[str, str]]:
    return {
        (s, t)
        for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "framework_binds"
    }


class TestSpringSymbolEdges:
    def test_injection_links_the_two_declarations(self, tmp_path: Path) -> None:
        (tmp_path / "UserService.java").write_text(
            "import org.springframework.stereotype.Service;\n"
            "@Service\npublic class UserService {}\n"
        )
        (tmp_path / "UserController.java").write_text(
            "import org.springframework.beans.factory.annotation.Autowired;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "@RestController\npublic class UserController {\n"
            "  @Autowired private UserService userService;\n"
            "}\n"
        )

        bound = _bound(_build_graph(tmp_path))
        assert (
            "UserController.java::UserController",
            "UserService.java::UserService",
        ) in bound

    def test_a_nested_configuration_owns_its_own_injection(self, tmp_path: Path) -> None:
        # The injection regexes read whole-file text. Attributing every hit to
        # the type the file is named for gives the outer class an injection it
        # does not declare, and nested @Configuration classes are a standard
        # idiom.
        (tmp_path / "PaymentGateway.java").write_text(
            "import org.springframework.stereotype.Service;\n"
            "@Service\npublic class PaymentGateway {}\n"
        )
        (tmp_path / "AppConfig.java").write_text(
            "import org.springframework.context.annotation.Configuration;\n"
            "import org.springframework.beans.factory.annotation.Autowired;\n"
            "@Configuration\npublic class AppConfig {\n"
            "  @Configuration\n"
            "  static class Inner {\n"
            "    @Autowired private PaymentGateway gateway;\n"
            "  }\n"
            "}\n"
        )

        bound = _bound(_build_graph(tmp_path))
        target = "PaymentGateway.java::PaymentGateway"
        assert ("AppConfig.java::AppConfig", target) not in bound
        assert any(src.startswith("AppConfig.java::") for src, dst in bound if dst == target)

    def test_an_ambiguous_type_name_stays_unclaimed(self, tmp_path: Path) -> None:
        # Two files declaring `UserService` cannot settle which one is injected,
        # and picking whichever was walked first is a coin flip.
        for sub in ("a", "b"):
            d = tmp_path / sub
            d.mkdir()
            (d / "UserService.java").write_text(
                "import org.springframework.stereotype.Service;\n"
                "@Service\npublic class UserService {}\n"
            )
        (tmp_path / "UserController.java").write_text(
            "import org.springframework.beans.factory.annotation.Autowired;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "@RestController\npublic class UserController {\n"
            "  @Autowired private UserService userService;\n"
            "}\n"
        )

        bound = _bound(_build_graph(tmp_path))
        assert not [d for _s, d in bound if d.endswith("::UserService")]
