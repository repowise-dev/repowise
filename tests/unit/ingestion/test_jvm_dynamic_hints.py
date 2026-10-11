"""Unit tests for the JVM dynamic-hint extractor."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.jvm import JvmDynamicHints


def _edges_to_pairs(edges):
    return {(e.source, e.target, e.hint_source.rsplit(":", 1)[-1]) for e in edges}


class TestClassForName:
    def test_resolves_fqn_to_file(self, tmp_path: Path) -> None:
        (tmp_path / "Loader.java").write_text(
            "package com.x;\n"
            "public class Loader {\n"
            "  void load() { Class.forName(\"com.x.Plugin\"); }\n"
            "}\n"
        )
        (tmp_path / "Plugin.java").write_text(
            "package com.x;\npublic class Plugin {}\n"
        )
        edges = JvmDynamicHints().extract(tmp_path)
        assert ("Loader.java", "Plugin.java", "class_forname") in _edges_to_pairs(edges)


class TestMockito:
    def test_mockk_resolves_in_kotlin(self, tmp_path: Path) -> None:
        (tmp_path / "Svc.kt").write_text("package x\nclass Svc\n")
        (tmp_path / "Test.kt").write_text(
            "package x\nclass Test {\n  fun t() { val s = mockk<Svc>() }\n}\n"
        )
        edges = JvmDynamicHints().extract(tmp_path)
        assert any(
            e.source == "Test.kt" and e.target == "Svc.kt" and e.hint_source.endswith("mockk")
            for e in edges
        )

    def test_mockito_resolves_in_java(self, tmp_path: Path) -> None:
        (tmp_path / "Svc.java").write_text(
            "package x;\npublic class Svc {}\n"
        )
        (tmp_path / "SvcTest.java").write_text(
            "package x;\nimport static org.mockito.Mockito.mock;\n"
            "public class SvcTest {\n  Svc s = Mockito.mock(Svc.class);\n}\n"
        )
        edges = JvmDynamicHints().extract(tmp_path)
        assert any(
            e.source == "SvcTest.java" and e.target == "Svc.java"
            for e in edges
        )


class TestSpringBootRun:
    def test_kotlin_run_application(self, tmp_path: Path) -> None:
        (tmp_path / "MyApp.kt").write_text("package x\nclass MyApp\n")
        (tmp_path / "Main.kt").write_text(
            "package x\nfun main(args: Array<String>) { runApplication<MyApp>(*args) }\n"
        )
        edges = JvmDynamicHints().extract(tmp_path)
        assert any(
            e.source == "Main.kt" and e.target == "MyApp.kt"
            and e.hint_source.endswith("spring_boot_run")
            for e in edges
        )


class TestMapStruct:
    def test_mappers_getmapper(self, tmp_path: Path) -> None:
        (tmp_path / "UserMapper.java").write_text(
            "package x;\npublic interface UserMapper {}\n"
        )
        (tmp_path / "Caller.java").write_text(
            "package x;\nimport org.mapstruct.factory.Mappers;\n"
            "public class Caller {\n"
            "  UserMapper m = Mappers.getMapper(UserMapper.class);\n"
            "}\n"
        )
        edges = JvmDynamicHints().extract(tmp_path)
        assert any(
            e.source == "Caller.java" and e.target == "UserMapper.java"
            for e in edges
        )


class TestJacksonReadValue:
    def test_object_mapper_read_value(self, tmp_path: Path) -> None:
        (tmp_path / "Dto.java").write_text("package x;\npublic class Dto {}\n")
        (tmp_path / "Parser.java").write_text(
            "package x;\nimport com.fasterxml.jackson.databind.ObjectMapper;\n"
            "public class Parser {\n"
            "  Dto p(ObjectMapper m, String j) throws Exception { return m.readValue(j, Dto.class); }\n"
            "}\n"
        )
        edges = JvmDynamicHints().extract(tmp_path)
        assert any(
            e.source == "Parser.java" and e.target == "Dto.java"
            for e in edges
        )
