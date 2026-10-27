"""Unit tests for Lombok synthetic-symbol synthesis."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
