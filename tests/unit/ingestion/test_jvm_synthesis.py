"""Unit tests for Java record, Kotlin data class / enum / object, and
MapStruct / AutoValue / Immutables synthesis."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file


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


def _names(tmp_path: Path, name: str, lang: str, src: str) -> list[str]:
    f = tmp_path / name
    f.write_text(src)
    pf = parse_file(_fi(name, f, lang), src.encode("utf-8"))
    return [s.name for s in pf.symbols]


class TestJavaRecord:
    def test_record_accessors_synthesized(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "Pt.java", "java",
            "public record Pt(int x, int y) {}\n",
        )
        assert "x" in names, names
        assert "y" in names, names
        assert "equals" in names, names
        assert "hashCode" in names, names
        assert "toString" in names, names

    def test_record_canonical_ctor_includes_components(self, tmp_path: Path) -> None:
        f = tmp_path / "Person.java"
        src = "public record Person(String name, int age) {}\n"
        f.write_text(src)
        pf = parse_file(_fi("Person.java", f, "java"), src.encode("utf-8"))
        ctors = [s for s in pf.symbols if s.name == "Person" and s.parent_name == "Person"]
        assert len(ctors) == 1, [s.id for s in pf.symbols]
        assert "String name" in ctors[0].signature
        assert "int age" in ctors[0].signature


class TestKotlinDataClass:
    def test_data_class_emits_componentN_and_copy(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "P.kt", "kotlin",
            "data class P(val x: Int, val y: Int)\n",
        )
        assert "component1" in names, names
        assert "component2" in names, names
        assert "copy" in names, names
        assert "equals" in names, names
        assert "hashCode" in names, names
        assert "toString" in names, names

    def test_enum_emits_values_valueOf(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "Color.kt", "kotlin",
            "enum class Color { RED, GREEN, BLUE }\n",
        )
        assert "values" in names, names
        assert "valueOf" in names, names

    def test_object_emits_INSTANCE(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "S.kt", "kotlin",
            "object S { fun work() {} }\n",
        )
        assert "INSTANCE" in names, names

    def test_plain_class_no_componentN(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "C.kt", "kotlin",
            "class C(val x: Int)\n",
        )
        assert "component1" not in names, names


class TestJvmCodegen:
    def test_mapstruct_mapper_emits_impl(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "Mapper.java", "java",
            "import org.mapstruct.Mapper;\n"
            "@Mapper public interface UserMapper { String map(String s); }\n",
        )
        assert "UserMapperImpl" in names, names

    def test_autovalue_emits_subclass(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "Box.java", "java",
            "import com.google.auto.value.AutoValue;\n"
            "@AutoValue public abstract class Box {}\n",
        )
        assert "AutoValue_Box" in names, names

    def test_immutables_emits_subclass(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "Person.java", "java",
            "import org.immutables.value.Value;\n"
            "@Value.Immutable public interface Person { String name(); }\n",
        )
        assert "ImmutablePerson" in names, names

    def test_no_annotation_no_synthesis(self, tmp_path: Path) -> None:
        names = _names(
            tmp_path, "Plain.java", "java",
            "public class Plain {}\n",
        )
        assert "PlainImpl" not in names, names
        assert "AutoValue_Plain" not in names, names
        assert "ImmutablePlain" not in names, names
