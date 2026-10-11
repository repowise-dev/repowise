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
