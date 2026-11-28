"""Unit tests for Lombok synthetic-symbol synthesis."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file


def _fi(rel: str, abs_: Path) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language="java",
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse(tmp_path: Path, name: str, src: str) -> list:
    f = tmp_path / name
    f.write_text(src)
    pf = parse_file(_fi(name, f), src.encode("utf-8"))
    return [s.name for s in pf.symbols]


def _parse_full(tmp_path: Path, name: str, src: str):
    f = tmp_path / name
    f.write_text(src)
    return parse_file(_fi(name, f), src.encode("utf-8"))


class TestLombok:
    def test_required_args_constructor_synthesizes_ctor(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "Svc.java",
            "import lombok.*;\n"
            "@RequiredArgsConstructor\n"
            "public class Svc {\n"
            "  private final Repo repo;\n"
            "  private final Cache cache;\n"
            "  private String mutable;\n"
            "}\n",
        )
        # The class itself is one Symbol; @RAC synthesises a ctor with
        # parent_name == "Svc" so its id is "Svc.java::Svc::Svc".
        ctors = [
            s for s in pf.symbols
            if s.name == "Svc" and s.parent_name == "Svc"
        ]
        assert len(ctors) == 1, [s.id for s in pf.symbols]
        # Signature should include the two final fields, not the mutable one
        assert "Repo repo" in ctors[0].signature
        assert "Cache cache" in ctors[0].signature
        assert "mutable" not in ctors[0].signature

    def test_slf4j_emits_log_field(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "Svc.java",
            "import lombok.extern.slf4j.Slf4j;\n"
            "@Slf4j public class Svc {}\n",
        )
        assert "log" in names, names

    @pytest.mark.parametrize(
        ("ann", "logger_type"),
        [("Slf4j", "Logger"), ("XSlf4j", "XLogger"), ("Log4j2", "Logger"),
         ("Log", "Logger"), ("JBossLog", "Logger"), ("CommonsLog", "Log"),
         ("Flogger", "FluentLogger")],
    )
    def test_logger_only_class_keeps_its_log_field(
        self, tmp_path: Path, ann: str, logger_type: str
    ) -> None:
        pf = _parse_full(
            tmp_path, "Svc.java", f"import lombok.extern.*;\n@{ann}\npublic class Svc {{}}\n"
        )
        logs = [s for s in pf.symbols if s.name == "log" and s.parent_name == "Svc"]
        assert len(logs) == 1, [s.id for s in pf.symbols]
        assert logs[0].signature == f"private static final {logger_type} log"
        assert logs[0].start_line == 2

    def test_data_emits_getters_setters_toString_equals(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "Pt.java",
            "import lombok.Data;\n"
            "@Data public class Pt { private int x; private int y; }\n",
        )
        assert "getX" in names, names
        assert "getY" in names, names
        assert "setX" in names, names
        assert "setY" in names, names
        assert "toString" in names, names
        assert "equals" in names, names
        assert "hashCode" in names, names

    def test_value_emits_getters_but_not_setters(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "V.java",
            "import lombok.Value;\n"
            "@Value public class V { int x; int y; }\n",
        )
        assert "getX" in names, names
        assert "setX" not in names, names

    def test_builder_emits_inner_builder_class_and_factory(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "B.java",
            "import lombok.Builder;\n"
            "@Builder public class B { private int x; }\n",
        )
        assert "BBuilder" in names, names
        assert "builder" in names, names
        assert "build" in names, names

    def test_boolean_field_gets_is_getter(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "X.java",
            "import lombok.Getter;\n"
            "@Getter public class X { private boolean active; private String name; }\n",
        )
        assert "isActive" in names, names
        assert "getName" in names, names

    def test_no_lombok_no_synthesis(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "P.java",
            "public class P { private int x; }\n",
        )
        assert "getX" not in names, names
        assert "log" not in names, names


def _by_name(pf) -> dict[str, list]:
    out: dict[str, list] = {}
    for s in pf.symbols:
        out.setdefault(s.name, []).append(s)
    return out


class TestLombokPlacement:
    """Each generated member sits where javac's Lombok puts it, so a call
    edge keys at the same line a compiler-backed index does."""

    _ENTITY = (
        "import lombok.*;\n"                       # 1
        "@NoArgsConstructor\n"                     # 2
        "public class Entity {\n"                  # 3
        "    @Setter\n"                            # 4
        "    @Getter\n"                            # 5
        "    @Column(name = \"status\")\n"         # 6
        "    private String status;\n"             # 7
        "    @Getter(AccessLevel.NONE)\n"          # 8
        "    private String hidden;\n"             # 9
        "    @Getter private boolean isActive;\n"  # 10
        "    @Setter private boolean active2;\n"   # 11
        "    private String plain;\n"              # 12
        "}\n"
    )

    def test_field_annotation_accessors_need_no_class_annotation(self, tmp_path: Path) -> None:
        by = _by_name(_parse_full(tmp_path, "Entity.java", self._ENTITY))
        assert by["getStatus"][0].start_line == 5
        assert by["setStatus"][0].start_line == 4
        assert "getHidden" not in by
        assert "getPlain" not in by and "setPlain" not in by

    def test_boolean_is_prefix(self, tmp_path: Path) -> None:
        by = _by_name(_parse_full(tmp_path, "Entity.java", self._ENTITY))
        assert "isActive" in by and "isIsActive" not in by
        assert "setActive2" in by

    _DATA = (
        "import lombok.*;\n"                       # 1
        "@Data\n"                                  # 2
        "@Builder\n"                               # 3
        "@EqualsAndHashCode(callSuper = false)\n"  # 4
        "public class Dto {\n"                     # 5
        "    private static int counter;\n"        # 6
        "    private final Long id;\n"             # 7
        "    @Size(max = 3)\n"                     # 8
        "    private Child child;\n"               # 9
        "}\n"
    )

    def test_class_annotation_members(self, tmp_path: Path) -> None:
        by = _by_name(_parse_full(tmp_path, "Dto.java", self._DATA))
        # A class-level getter sits on its field's name; a setter on @Data.
        assert by["getChild"][0].start_line == 9
        assert by["setChild"][0].start_line == 2
        assert by["equals"][0].start_line == 4
        assert by["toString"][0].start_line == 2
        assert by["builder"][0].start_line == 3
        assert by["child"][0].start_line == 9 and by["child"][0].parent_name == "DtoBuilder"
        # Static fields get no accessors; final fields no setter.
        assert "getCounter" not in by and "counter" not in by
        assert "getId" in by and "setId" not in by

    def test_data_adds_no_constructor_beside_a_declared_one(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "D.java",
            "import lombok.Data;\n"
            "@Data public class D {\n"
            "  private final Long id;\n"
            "  public D(Long id, String x) { this.id = id; }\n"
            "}\n",
        )
        ctors = [s for s in pf.symbols if s.name == "D" and s.parent_name == "D"]
        assert len(ctors) == 1 and "String x" in ctors[0].signature

    def test_enum_getter(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "E.java",
            "import lombok.Getter;\n"
            "@Getter public enum E { A(\"a\"); private final String code; E(String c) { code = c; } }\n",
        )
        assert "getCode" in names, names

    def test_fluent_accessors_sit_on_the_field(self, tmp_path: Path) -> None:
        by = _by_name(_parse_full(
            tmp_path, "B.java",
            "import lombok.*;\n"
            "@Accessors(fluent = true)\n"
            "public class B {\n"
            "    @Setter\n"
            "    private Integer weight;\n"
            "}\n",
        ))
        assert [s.start_line for s in by["weight"]] == [5]
        assert by["weight"][0].signature.endswith("-> B")

    def test_signatures_declare_return_types(self, tmp_path: Path) -> None:
        by = _by_name(_parse_full(tmp_path, "Dto.java", self._DATA))
        assert by["getChild"][0].signature == "getChild() -> Child"
        assert by["builder"][0].signature == "builder() -> DtoBuilder"
        assert by["child"][0].signature.endswith("-> DtoBuilder")
        assert by["build"][0].signature == "build() -> Dto"


class TestLombokSemantics:
    def test_data_beside_explicit_constructor_annotations(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "D.java",
            "import lombok.*;\n"
            "@Data @NoArgsConstructor @AllArgsConstructor\n"
            "public class D { private final Long id; private String name; }\n",
        )
        sigs = sorted(s.signature for s in pf.symbols if s.name == "D" and s.parent_name == "D")
        assert sigs == ["D()", "D(Long id, String name)"], sigs

    def test_boolean_is_field_getter_sits_on_its_annotation(self, tmp_path: Path) -> None:
        by = _by_name(_parse_full(
            tmp_path, "A.java",
            "import lombok.Getter;\n"
            "public class A {\n"
            "    @Getter\n"
            "    private boolean isOpen;\n"
            "}\n",
        ))
        assert [s.start_line for s in by["isOpen"] if s.kind == "method"] == [3]

    def test_every_declarator_gets_accessors(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "M.java",
            "import lombok.Getter;\n@Getter public class M { private int x, y; }\n",
        )
        assert "getX" in names and "getY" in names, names

    def test_explicit_class_getter_none_beats_data(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "N.java",
            "import lombok.*;\n"
            "@Data @Getter(AccessLevel.NONE) public class N { private int x; }\n",
        )
        assert "getX" not in names and "setX" in names, names

    def test_setter_returns_follow_chain_and_fluent(self, tmp_path: Path) -> None:
        by = _by_name(_parse_full(
            tmp_path, "C.java",
            "import lombok.*;\n"
            "import lombok.experimental.Accessors;\n"
            "@Setter @Accessors(chain = true)\n"
            "public class C {\n"
            "    private int a;\n"
            "    @Accessors(fluent = true, chain = false) private int b;\n"
            "}\n",
        ))
        assert by["setA"][0].signature.endswith("-> C")
        assert [s.signature for s in by["b"] if s.kind == "method"] == ["b(int b) -> void"]

    def test_initialized_final_field_takes_no_builder_or_constructor_slot(
        self, tmp_path: Path
    ) -> None:
        pf = _parse_full(
            tmp_path, "F.java",
            "import lombok.*;\n"
            "@Builder @AllArgsConstructor\n"
            "public class F { private final int k = 1; private int v; }\n",
        )
        names = [s.name for s in pf.symbols if s.parent_name == "FBuilder"]
        assert "v" in names and "k" not in names, names
        ctor = [s for s in pf.symbols if s.name == "F" and s.parent_name == "F"]
        assert [c.signature for c in ctor] == ["F(int v)"]

    def test_a_non_lombok_getter_annotation_generates_nothing(self, tmp_path: Path) -> None:
        names = _parse(
            tmp_path, "G.java",
            "import other.Getter;\npublic class G { @Getter private int x; }\n",
        )
        assert "getX" not in names, names


def _members(pf, parent: str) -> dict[tuple[str, str], str]:
    """``{(name, signature): visibility}`` of the members of *parent* Lombok generates."""
    return {
        (s.name, s.signature): s.visibility
        for s in pf.symbols
        if s.parent_name == parent and s.start_line == s.end_line
    }


class TestLombokAccess:
    """A generated member takes the access Lombok gives it, not always public."""

    def test_a_utility_class_has_a_private_constructor_and_static_fields(
        self, tmp_path: Path
    ) -> None:
        pf = _parse_full(
            tmp_path, "U.java",
            "import lombok.Getter;\nimport lombok.experimental.UtilityClass;\n"
            "@UtilityClass @Getter\npublic class U {\n  int count;\n}\n",
        )
        assert _members(pf, "U") == {("U", "U()"): "private"}
        # A method like a written constructor, which the unused-code pass leaves alone.
        assert [s.kind for s in pf.symbols if s.name == "U" and s.parent_name == "U"] == ["method"]

    @pytest.mark.parametrize(
        ("annotation", "visibility"),
        [
            ("@NoArgsConstructor(access = AccessLevel.MODULE)", "internal"),
            ("@NoArgsConstructor(access = PRIVATE)", "private"),
        ],
        ids=["module", "static-import"],
    )
    def test_a_constructor_takes_its_access(
        self, tmp_path: Path, annotation: str, visibility: str
    ) -> None:
        pf = _parse_full(
            tmp_path, "C.java",
            "import lombok.*;\nimport static lombok.AccessLevel.PRIVATE;\n"
            f"{annotation}\npublic class C {{ private int v; }}\n",
        )
        assert _members(pf, "C") == {("C", "C()"): visibility}

    def test_access_none_generates_no_constructor(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "N.java",
            "import lombok.*;\n"
            "@NoArgsConstructor(access = AccessLevel.NONE) @AllArgsConstructor\n"
            "public class N { private int v; }\n",
        )
        assert _members(pf, "N") == {("N", "N(int v)"): "public"}

    @pytest.mark.parametrize(
        ("annotation", "factory"),
        [
            (
                '@RequiredArgsConstructor(staticName = "of", access = AccessLevel.PROTECTED)',
                "protected",
            ),
            ('@Data(staticConstructor = "of")', "public"),
        ],
        ids=["static-name", "data-static-constructor"],
    )
    def test_a_static_factory_makes_the_constructor_private(
        self, tmp_path: Path, annotation: str, factory: str
    ) -> None:
        pf = _parse_full(
            tmp_path, "S.java",
            f"import lombok.*;\n{annotation}\npublic class S {{ private final int v; }}\n",
        )
        members = _members(pf, "S")
        assert members[("S", "S(int v)")] == "private"
        assert members[("of", "of(int v) -> S")] == factory

    def test_a_declared_constructor_leaves_data_its_static_factory(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "S.java",
            "import lombok.*;\n"
            '@Data(staticConstructor = "of")\n'
            "public class S {\n  private final int v;\n"
            "  public S(int v) {\n    this.v = v;\n  }\n}\n",
        )
        assert _members(pf, "S")[("of", "of(int v) -> S")] == "public"

    def test_a_super_builder_constructor_takes_the_builder_and_data_adds_none(
        self, tmp_path: Path
    ) -> None:
        pf = _parse_full(
            tmp_path, "P.java",
            "import lombok.*;\nimport lombok.experimental.SuperBuilder;\n"
            # An annotation on a type parameter is not its name.
            "@Data @SuperBuilder\npublic class P<@Deprecated T> { private final T v; }\n",
        )
        constructors = {k: v for k, v in _members(pf, "P").items() if k[0] == "P"}
        assert constructors == {("P", "P(PBuilder<T, ?, ?> b)"): "protected"}

    def test_an_enum_constructor_is_private(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "E.java",
            "import lombok.*;\n"
            "@AllArgsConstructor\npublic enum E { A(1); private final int code; }\n",
        )
        assert _members(pf, "E")[("E", "E(int code)")] == "private"

    def test_a_builder_replaces_the_data_constructor_with_a_package_private_one(
        self, tmp_path: Path
    ) -> None:
        pf = _parse_full(
            tmp_path, "B.java",
            "import lombok.*;\n@Data @Builder\n"
            "public class B { private final int a; private String b; }\n",
        )
        constructors = {k: v for k, v in _members(pf, "B").items() if k[0] == "B"}
        assert constructors == {("B", "B(int a, String b)"): "internal"}

    @pytest.mark.parametrize(
        ("declaration", "can_equal"),
        [
            ("@Data class Q", "protected"),
            ("@Data final class Q", None),
            ("@Value class Q", None),
            ("@Value @NonFinal class Q", "protected"),
            ("@Data final class Q extends Base", "protected"),
            ("@Data final class Q extends Object", None),
        ],
    )
    def test_can_equal_is_protected_and_absent_from_a_final_class_that_extends_only_object(
        self, tmp_path: Path, declaration: str, can_equal: str | None
    ) -> None:
        pf = _parse_full(
            tmp_path, "Q.java",
            "import lombok.*;\nimport lombok.experimental.NonFinal;\n"
            f"{declaration} {{ private int a; }}\n",
        )
        members = _members(pf, "Q")
        assert ("equals", "equals(Object o) -> boolean") in members
        assert members.get(("canEqual", "canEqual(Object other) -> boolean")) == can_equal

    @pytest.mark.parametrize(
        ("annotations", "constructor"),
        [
            ("@Value @FieldDefaults(makeFinal = false)", "V(int a, String b)"),
            ("@Value @FieldDefaults(makeFinal = true)", "V(int a)"),
            ("@Value @FieldDefaults(level = AccessLevel.PRIVATE)", "V(int a, String b)"),
            ("@AllArgsConstructor @FieldDefaults(makeFinal = true)", "V(int a)"),
        ],
        ids=["value-not-final", "value-final", "value-no-make-final", "without-value"],
    )
    def test_a_written_field_defaults_decides_which_fields_are_final(
        self, tmp_path: Path, annotations: str, constructor: str
    ) -> None:
        pf = _parse_full(
            tmp_path, "V.java",
            "import lombok.*;\nimport lombok.experimental.FieldDefaults;\n"
            f'{annotations}\nclass V {{ int a; String b = "x"; }}\n',
        )
        assert ("V", constructor) in _members(pf, "V")

    def test_a_value_field_is_final_unless_it_is_non_final(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "V.java",
            "import lombok.*;\nimport lombok.experimental.NonFinal;\n"
            "@Value\npublic class V {\n"
            '  int a;\n  String b = "x";\n  @NonFinal String c = "y";\n}\n',
        )
        assert ("V", "V(int a, String c)") in _members(pf, "V")

    def test_a_static_field_of_a_value_class_is_not_final_and_has_its_setter(
        self, tmp_path: Path
    ) -> None:
        pf = _parse_full(
            tmp_path, "V.java", "import lombok.*;\n@Value\nclass V { @Setter static int count; }\n"
        )
        assert ("setCount", "setCount(int count) -> void") in _members(pf, "V")

    def test_an_accessor_takes_the_access_of_its_annotation(self, tmp_path: Path) -> None:
        pf = _parse_full(
            tmp_path, "A.java",
            "import lombok.*;\n"
            "@Getter @Setter(AccessLevel.PRIVATE)\n"
            "public class A {\n"
            "  @Getter(AccessLevel.PROTECTED) private int a;\n"
            "  private int b;\n"
            "  @Setter private int c;\n"
            "  @With(value = AccessLevel.PACKAGE) private final int d;\n"
            "}\n",
        )
        members = _members(pf, "A")
        assert members[("getA", "getA() -> int")] == "protected"
        assert members[("getB", "getB() -> int")] == "public"
        assert members[("setB", "setB(int b) -> void")] == "private"
        assert members[("setC", "setC(int c) -> void")] == "public"
        assert members[("withD", "withD(int d) -> A")] == "internal"

    @pytest.mark.parametrize(
        ("access", "outer", "inner"),
        [
            ("AccessLevel.PACKAGE", "internal", "internal"),
            ("AccessLevel.PROTECTED", "protected", "public"),
            ("AccessLevel.NONE", "public", "public"),
        ],
    )
    def test_a_builder_takes_its_access(
        self, tmp_path: Path, access: str, outer: str, inner: str
    ) -> None:
        pf = _parse_full(
            tmp_path, "B.java",
            f"import lombok.*;\n@Builder(access = {access})\npublic class B {{ private int v; }}\n",
        )
        members = _members(pf, "B")
        assert members[("builder", "builder() -> BBuilder")] == outer
        assert members[("BBuilder", "class BBuilder")] == outer
        builder = _members(pf, "BBuilder")
        assert builder[("build", "build() -> B")] == inner
        assert builder[("v", "v(int v) -> BBuilder")] == inner
        assert builder[("toString", "toString() -> String")] == "public"

    @pytest.mark.parametrize(
        ("annotation", "log"),
        [
            ("@Slf4j", ("private static final Logger log", "private")),
            ("@Slf4j(access = AccessLevel.PACKAGE)", ("static final Logger log", "internal")),
            ("@Slf4j(access = AccessLevel.NONE)", None),
        ],
    )
    def test_the_logger_takes_its_access(
        self, tmp_path: Path, annotation: str, log: tuple[str, str] | None
    ) -> None:
        pf = _parse_full(
            tmp_path, "L.java",
            "import lombok.AccessLevel;\nimport lombok.extern.slf4j.Slf4j;\n"
            f"{annotation}\npublic class L {{ }}\n",
        )
        logs = [(s.signature, s.visibility) for s in pf.symbols if s.name == "log"]
        assert logs == ([log] if log else [])


class TestLombokCallEdges:
    """A call through a generated member lands on it, chained hops included."""

    def test_getter_and_builder_chains_resolve(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.call_resolver import CallResolver

        files = {
            "src/Child.java": (
                "package p;\n"
                "public class Child {\n"
                "    public String name() { return \"\"; }\n"
                "}\n"
            ),
            "src/Dto.java": (
                "package p;\n"
                "import lombok.*;\n"
                "@Getter\n"
                "@Builder\n"
                "public class Dto {\n"
                "    private Child child;\n"
                "    private String label;\n"
                "}\n"
            ),
            "src/Use.java": (
                "package p;\n"
                "public class Use {\n"
                "    String run(Dto dto) {\n"
                "        Dto made = Dto.builder().label(\"x\").build();\n"
                "        return dto.getChild().name();\n"
                "    }\n"
                "}\n"
            ),
        }
        parsed = {}
        for rel, src in files.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            parsed[rel] = _parse_full(tmp_path, rel, src)
        resolver = CallResolver(parsed, {p: set() for p in parsed}, repo_path=str(tmp_path))
        callees = {rc.callee_id for rc in resolver.resolve_file("src/Use.java", parsed["src/Use.java"].calls)}
        assert "src/Dto.java::Dto::getChild" in callees
        assert "src/Child.java::Child::name" in callees
        assert "src/Dto.java::DtoBuilder::label" in callees
        assert "src/Dto.java::DtoBuilder::build" in callees


def _resolve_with_heritage(tmp_path: Path, files: dict[str, str], caller_file: str) -> list[str]:
    """Callees of *caller_file*, with heritage resolved as the graph builder does."""
    from repowise.core.ingestion.call_resolver import CallResolver
    from repowise.core.ingestion.heritage_resolver import HeritageResolver

    parsed = {}
    for rel, src in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        parsed[rel] = _parse_full(tmp_path, rel, src)
    imports: dict[str, set[str]] = {p: set() for p in parsed}
    parents: dict[str, set[str]] = {}
    for rel, pf in parsed.items():
        for rh in HeritageResolver(parsed, imports).resolve_file(rel, pf.heritage):
            parents.setdefault(rh.child_id, set()).add(rh.parent_id)
    resolver = CallResolver(parsed, imports, repo_path=str(tmp_path), heritage_parents=parents)
    return sorted(rc.callee_id for rc in resolver.resolve_file(caller_file, parsed[caller_file].calls))


class TestSuperBuilder:
    """A ``@SuperBuilder`` subclass builder extends its superclass's, generic in itself."""

    _BASE = (
        "package p;\nimport lombok.experimental.SuperBuilder;\n"
        "@SuperBuilder\npublic class Base {\n    private String name;\n}\n"
    )
    _SUB = (
        "package p;\nimport lombok.experimental.SuperBuilder;\n"
        "@SuperBuilder\npublic class Sub extends Base {\n    private int w;\n}\n"
    )

    def test_the_subclass_builder_extends_the_superclass_builder(self, tmp_path: Path) -> None:
        pf = _parse_full(tmp_path, "Sub.java", self._SUB)
        assert [(h.child_name, h.parent_name) for h in pf.heritage] == [
            ("Sub", "Base"),
            ("SubBuilder", "BaseBuilder"),
        ]

    def test_a_plain_builder_adds_no_builder_heritage(self, tmp_path: Path) -> None:
        src = self._SUB.replace("SuperBuilder", "Builder").replace(".experimental", "")
        pf = _parse_full(tmp_path, "Sub.java", src)
        assert [(h.child_name, h.parent_name) for h in pf.heritage] == [("Sub", "Base")]

    def test_a_chain_keeps_the_subclass_builder_through_an_inherited_method(
        self, tmp_path: Path
    ) -> None:
        use = (
            "package p;\nclass Use {\n    Sub u() {\n"
            "        return Sub.builder().name(\"a\").w(1).build();\n    }\n}\n"
        )
        files = {"p/Base.java": self._BASE, "p/Sub.java": self._SUB, "p/Use.java": use}
        assert _resolve_with_heritage(tmp_path, files, "p/Use.java") == [
            "p/Base.java::BaseBuilder::name",
            "p/Sub.java::Sub::builder",
            "p/Sub.java::SubBuilder::build",
            "p/Sub.java::SubBuilder::w",
        ]

    def test_a_nested_builder_is_a_member_type_of_its_host(self, tmp_path: Path) -> None:
        """Two outer types nest a ``Create``; each chain stays on its own host's builders."""
        outer = (
            "package p;\nimport lombok.experimental.SuperBuilder;\n"
            "public interface {o} {{\n"
            "    @SuperBuilder\n    final class Create extends UpdateCreate {{\n"
            "        private String key;\n    }}\n"
            "    @SuperBuilder\n    class UpdateCreate {{\n        private String colour;\n    }}\n"
            "    static Object u() {{\n"
            "        return Create.builder().colour(\"c\").key(\"k\").build();\n    }}\n}}\n"
        )
        files = {"p/AMgmt.java": outer.format(o="AMgmt"), "p/BMgmt.java": outer.format(o="BMgmt")}
        pf = _parse_full(tmp_path, "AMgmt.java", files["p/AMgmt.java"])
        assert "AMgmt.java::AMgmt::Create::CreateBuilder" in {s.id for s in pf.symbols}
        callees = _resolve_with_heritage(tmp_path, files, "p/AMgmt.java")
        assert "p/AMgmt.java::UpdateCreateBuilder::colour" in callees
        assert "p/AMgmt.java::CreateBuilder::key" in callees
        assert "p/AMgmt.java::CreateBuilder::build" in callees
        assert not any(c.startswith("p/BMgmt.java") for c in callees), callees


class TestSelfBoundedReturn:
    """``B`` of ``Base<B extends Base<B>>`` is the receiver's own type in a chain."""

    def test_a_self_bounded_return_keeps_the_receivers_type(self, tmp_path: Path) -> None:
        outer = (
            "package p;\npublic class Outer {\n"
            "    public abstract static class Base<B extends Base<B>> {\n"
            "        public B name(String n) { return null; }\n    }\n"
            "    public static class Sub extends Base<Sub> {\n"
            "        public Sub extra(int x) { return this; }\n    }\n"
            "    public static Sub sub() { return new Sub(); }\n}\n"
        )
        use = "package p;\nclass Use {\n    void go() {\n        Outer.sub().name(\"a\").extra(1);\n    }\n}\n"
        files = {"p/Outer.java": outer, "p/Use.java": use}
        assert _resolve_with_heritage(tmp_path, files, "p/Use.java") == [
            "p/Outer.java::Base::name",
            "p/Outer.java::Outer::sub",
            "p/Outer.java::Sub::extra",
        ]

    def test_a_self_bounded_method_called_twice_in_one_chain(self, tmp_path: Path) -> None:
        """Each hop is read left of the last, so a repeated name ends instead of recursing."""
        outer = (
            "package p;\npublic class Outer {\n"
            "    public abstract static class Base<B extends Base<B>> {\n"
            "        public B name(String n) { return null; }\n    }\n"
            "    public static class Sub extends Base<Sub> {\n"
            "        public Sub extra(int x) { return this; }\n    }\n"
            "    public static Sub sub() { return new Sub(); }\n}\n"
        )
        use = (
            "package p;\nclass Use {\n    void go() {\n"
            '        Outer.sub().name("a").name("b").name("c").extra(1);\n    }\n}\n'
        )
        files = {"p/Outer.java": outer, "p/Use.java": use}
        assert "p/Outer.java::Sub::extra" in _resolve_with_heritage(tmp_path, files, "p/Use.java")

    def test_a_type_parameter_bounded_by_another_type_is_not_the_receiver(
        self, tmp_path: Path
    ) -> None:
        """``T extends Other<T>`` is not ``Base``'s self type, so the chain stops."""
        outer = (
            "package p;\npublic class Outer {\n"
            "    public static class Other<T> {}\n"
            "    public abstract static class Base<B extends Other<B>> {\n"
            "        public B name(String n) { return null; }\n    }\n"
            "    public static class Sub extends Base<Sub> {\n"
            "        public Sub extra(int x) { return this; }\n    }\n"
            "    public static Sub sub() { return new Sub(); }\n}\n"
        )
        use = "package p;\nclass Use {\n    void go() {\n        Outer.sub().name(\"a\").extra(1);\n    }\n}\n"
        files = {"p/Outer.java": outer, "p/Use.java": use}
        assert "p/Outer.java::Sub::extra" not in _resolve_with_heritage(tmp_path, files, "p/Use.java")


def test_a_long_superbuilder_chain_resolves_each_link_once(tmp_path: Path, monkeypatch) -> None:
    """Each self-typed hop walks back down its chain; a remembered link keeps that linear."""
    from repowise.core.ingestion.call_resolver import CallResolver

    fields = [f"f{i}" for i in range(24)]
    base = (
        "package p;\nimport lombok.experimental.SuperBuilder;\n@SuperBuilder\npublic class Base {\n"
        + "".join(f"    private int {f};\n" for f in fields)
        + "}\n"
    )
    sub = (
        "package p;\nimport lombok.experimental.SuperBuilder;\n"
        "@SuperBuilder\npublic class Sub extends Base {\n    private int w;\n}\n"
    )
    chain = "".join(f".{f}({i})" for i, f in enumerate(fields))
    use = f"package p;\nclass Use {{\n    Sub u() {{\n        return Sub.builder(){chain}.w(1).build();\n    }}\n}}\n"
    calls = 0
    resolve_one = CallResolver._resolve_one

    def counted(self, *args):
        nonlocal calls
        calls += 1
        return resolve_one(self, *args)

    monkeypatch.setattr(CallResolver, "_resolve_one", counted)
    files = {"p/Base.java": base, "p/Sub.java": sub, "p/Use.java": use}
    callees = _resolve_with_heritage(tmp_path, files, "p/Use.java")
    assert {f"p/Base.java::BaseBuilder::{f}" for f in fields} <= set(callees)
    assert "p/Sub.java::SubBuilder::build" in callees
    assert calls < 20 * len(fields)
