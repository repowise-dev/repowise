"""Tests for H1-H8 per-language dynamic-hint extractors."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.c import CDynamicHints
from repowise.core.ingestion.dynamic_hints.go import GoDynamicHints
from repowise.core.ingestion.dynamic_hints.luau import LuauDynamicHints
from repowise.core.ingestion.dynamic_hints.php import PhpDynamicHints
from repowise.core.ingestion.dynamic_hints.ruby import RubyDynamicHints
from repowise.core.ingestion.dynamic_hints.scala import ScalaDynamicHints
from repowise.core.ingestion.dynamic_hints.spring import SpringDynamicHints
from repowise.core.ingestion.dynamic_hints.swift import SwiftDynamicHints


class TestSpringHints:
    def test_get_bean_resolves_to_class_file(self, tmp_path: Path) -> None:
        (tmp_path / "Foo.java").write_text(
            "package x;\nimport org.springframework.context.ApplicationContext;\n"
            "public class Foo { void m(ApplicationContext c) { c.getBean(Bar.class); } }\n"
        )
        (tmp_path / "Bar.java").write_text("package x;\npublic class Bar {}\n")
        edges = SpringDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "spring:get_bean"}
        assert "Bar.java" in targets

    def test_bean_factory_links_to_return_type(self, tmp_path: Path) -> None:
        (tmp_path / "Cfg.java").write_text(
            "package x;\nimport org.springframework.context.annotation.Bean;\n"
            "public class Cfg {\n  @Bean\n  public Foo fooBean() { return new Foo(); }\n}\n"
        )
        (tmp_path / "Foo.java").write_text("package x;\npublic class Foo {}\n")
        edges = SpringDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "spring:bean_factory"}
        assert "Foo.java" in targets

    def test_no_spring_no_edges(self, tmp_path: Path) -> None:
        (tmp_path / "Foo.java").write_text("public class Foo {}\n")
        assert SpringDynamicHints().extract(tmp_path) == []


class TestRubyHints:
    def test_send_resolves_method(self, tmp_path: Path) -> None:
        (tmp_path / "a.rb").write_text("class A\n  def hello; end\nend\n")
        (tmp_path / "b.rb").write_text("class B\n  def call(o); o.send(:hello); end\nend\n")
        edges = RubyDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "ruby:send"}
        assert "a.rb" in targets

    def test_const_get(self, tmp_path: Path) -> None:
        (tmp_path / "user.rb").write_text("class User\nend\n")
        (tmp_path / "loader.rb").write_text(
            "klass = Object.const_get(:User)\n"
        )
        edges = RubyDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "ruby:const_get"}
        assert "user.rb" in targets


class TestPhpHints:
    def test_container_get_class(self, tmp_path: Path) -> None:
        (tmp_path / "Foo.php").write_text("<?php\nclass Foo {}\n")
        (tmp_path / "Bar.php").write_text(
            "<?php\nclass Bar { function m($c) { return $c->get(Foo::class); } }\n"
        )
        edges = PhpDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "php:container_get"}
        assert "Foo.php" in targets

    def test_reflection_class(self, tmp_path: Path) -> None:
        (tmp_path / "Foo.php").write_text("<?php\nclass Foo {}\n")
        (tmp_path / "Use.php").write_text(
            "<?php\n$r = new ReflectionClass(Foo::class);\n"
        )
        edges = PhpDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "php:reflection_class"}
        assert "Foo.php" in targets


class TestScalaHints:
    def test_class_forname(self, tmp_path: Path) -> None:
        (tmp_path / "Foo.scala").write_text("package x\nclass Foo\n")
        (tmp_path / "Loader.scala").write_text(
            "package x\nobject Loader { val c = Class.forName(\"x.Foo\") }\n"
        )
        edges = ScalaDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "scala:class_forname"}
        assert "Foo.scala" in targets

    def test_given(self, tmp_path: Path) -> None:
        (tmp_path / "Ord.scala").write_text("package x\ntrait Ord\n")
        (tmp_path / "Use.scala").write_text(
            "package x\nobject Use { given intOrd: Ord = ??? }\n"
        )
        edges = ScalaDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "scala:given"}
        assert "Ord.scala" in targets


class TestSwiftHints:
    def test_nsclass_from_string(self, tmp_path: Path) -> None:
        (tmp_path / "MyClass.swift").write_text("class MyClass {}\n")
        (tmp_path / "Use.swift").write_text(
            "let c = NSClassFromString(\"MyClass\")\n"
        )
        edges = SwiftDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "swift:nsclass_from_string"}
        assert "MyClass.swift" in targets

    def test_selector(self, tmp_path: Path) -> None:
        (tmp_path / "A.swift").write_text("func myAction() {}\n")
        (tmp_path / "B.swift").write_text("let s = Selector(\"myAction\")\n")
        edges = SwiftDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "swift:selector"}
        assert "A.swift" in targets


class TestCHints:
    def test_function_pointer_assignment(self, tmp_path: Path) -> None:
        (tmp_path / "ops.c").write_text(
            "int do_thing(int x) { return x; }\n"
        )
        (tmp_path / "main.c").write_text(
            "extern int do_thing(int);\nint main(){\n  int (*fp)(int);\n  fp = do_thing;\n  return 0;\n}\n"
        )
        edges = CDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "c:fn_ptr"}
        assert "ops.c" in targets

    def test_dlopen_emits_external(self, tmp_path: Path) -> None:
        (tmp_path / "main.c").write_text(
            "#include <dlfcn.h>\nint main(){ void *h = dlopen(\"./libfoo.so\", 0); return 0; }\n"
        )
        edges = CDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "c:dlopen" for e in edges)


class TestLuauHints:
    def test_get_service(self, tmp_path: Path) -> None:
        (tmp_path / "init.luau").write_text(
            "local rs = game:GetService(\"ReplicatedStorage\")\n"
        )
        edges = LuauDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "luau:get_service" for e in edges)

    def test_metatable_index(self, tmp_path: Path) -> None:
        (tmp_path / "Base.luau").write_text("local Base = {}\nreturn Base\n")
        (tmp_path / "Sub.luau").write_text(
            "local Sub = {}\nsetmetatable(Sub, {__index = Base})\nreturn Sub\n"
        )
        edges = LuauDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "luau:metatable_index"}
        assert "Base.luau" in targets


class TestGoHints:
    def test_reflect_typeof(self, tmp_path: Path) -> None:
        (tmp_path / "types.go").write_text(
            "package x\ntype Foo struct{}\n"
        )
        (tmp_path / "use.go").write_text(
            "package x\nimport \"reflect\"\nvar _ = reflect.TypeOf(Foo{})\n"
        )
        edges = GoDynamicHints().extract(tmp_path)
        targets = {e.target for e in edges if e.hint_source == "go:reflect_typeof"}
        assert "types.go" in targets

    def test_plugin_open_emits_external(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").write_text(
            "package main\nimport \"plugin\"\nfunc main(){ plugin.Open(\"./p.so\") }\n"
        )
        edges = GoDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "go:plugin_open" for e in edges)


class TestRegistryWiring:
    def test_default_registry_includes_new_extractors(self) -> None:
        from repowise.core.ingestion.dynamic_hints.registry import HintRegistry

        names = {ex.name for ex in HintRegistry()._extractors}
        for required in ("spring", "ruby", "php", "scala", "swift", "c", "luau", "go"):
            assert required in names, f"missing {required}"
