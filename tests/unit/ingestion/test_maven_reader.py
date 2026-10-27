"""Unit tests for the Maven pom.xml manifest parser."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.external_systems import (
    extract_external_systems,
    maven_model,
)
from repowise.core.ingestion.external_systems.maven import parse


def _pom(tmp_path: Path, xml: str) -> Path:
    pom = tmp_path / "pom.xml"
    pom.write_text(xml, encoding="utf-8")
    return pom


class TestMavenParse:
    def test_basic_dependencies(self, tmp_path: Path) -> None:
        pom = _pom(tmp_path, """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <groupId>com.example</groupId>
  <artifactId>myapp</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <version>3.2.0</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>""")
        records = parse(pom, tmp_path)
        assert len(records) == 2
        web = next(r for r in records if "spring-boot" in r.name)
        assert web.name == "org.springframework.boot:spring-boot-starter-web"
        assert web.version == "3.2.0"
        assert web.ecosystem == "maven"
        assert not web.is_dev_dep
        junit = next(r for r in records if "junit" in r.name)
        assert junit.is_dev_dep

    def test_property_substitution(self, tmp_path: Path) -> None:
        pom = _pom(tmp_path, """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <groupId>com.example</groupId>
  <artifactId>myapp</artifactId>
  <version>1.0.0</version>
  <properties>
    <guava.version>32.1.3-jre</guava.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
      <version>${guava.version}</version>
    </dependency>
  </dependencies>
</project>""")
        records = parse(pom, tmp_path)
        assert len(records) == 1
        assert records[0].version == "32.1.3-jre"

    def test_dependency_management(self, tmp_path: Path) -> None:
        pom = _pom(tmp_path, """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <groupId>com.example</groupId>
  <artifactId>myapp</artifactId>
  <version>1.0.0</version>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.google.guava</groupId>
        <artifactId>guava</artifactId>
        <version>32.1.3-jre</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
    </dependency>
  </dependencies>
</project>""")
        records = parse(pom, tmp_path)
        assert len(records) == 1
        assert records[0].version == "32.1.3-jre"

    def test_malformed_xml_returns_empty(self, tmp_path: Path) -> None:
        pom = _pom(tmp_path, "not xml at all")
        assert parse(pom, tmp_path) == []

    def test_namespace_prefix(self, tmp_path: Path) -> None:
        pom = _pom(tmp_path, """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>myapp</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.slf4j</groupId>
      <artifactId>slf4j-api</artifactId>
      <version>2.0.9</version>
    </dependency>
  </dependencies>
</project>""")
        records = parse(pom, tmp_path)
        assert len(records) == 1
        assert records[0].name == "org.slf4j:slf4j-api"

    def test_parent_version_inheritance(self, tmp_path: Path) -> None:
        pom = _pom(tmp_path, """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <parent>
    <groupId>com.example</groupId>
    <artifactId>parent</artifactId>
    <version>2.0.0</version>
  </parent>
  <artifactId>child</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>sibling</artifactId>
      <version>${project.version}</version>
    </dependency>
  </dependencies>
</project>""")
        records = parse(pom, tmp_path)
        assert len(records) == 1
        assert records[0].version == "2.0.0"

    def test_child_project_properties_use_child_builtins(self, tmp_path: Path) -> None:
        parent_dir = tmp_path / "parent"
        child_dir = tmp_path / "child"
        parent_dir.mkdir()
        child_dir.mkdir()
        (parent_dir / "pom.xml").write_text(
            """<project><groupId>com.example</groupId>
  <artifactId>parent</artifactId><version>1.0.0</version></project>""",
            encoding="utf-8",
        )
        child = child_dir / "pom.xml"
        child.write_text(
            """<project>
  <parent><groupId>com.example</groupId><artifactId>parent</artifactId>
    <version>1.0.0</version><relativePath>../parent/pom.xml</relativePath></parent>
  <artifactId>child</artifactId><version>2.0.0</version>
  <properties><dependency.version>${project.version}</dependency.version></properties>
  <dependencies><dependency><groupId>com.example</groupId><artifactId>shared</artifactId>
    <version>${dependency.version}</version></dependency></dependencies>
</project>""",
            encoding="utf-8",
        )

        records = parse(child, tmp_path)

        assert len(records) == 1
        assert records[0].version == "2.0.0"

    def test_external_extraction_parses_each_pom_once(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        root = _pom(
            tmp_path,
            """<project><groupId>com.example</groupId><artifactId>parent</artifactId>
  <version>1</version><packaging>pom</packaging>
  <modules><module>child</module></modules></project>""",
        )
        child = tmp_path / "child" / "pom.xml"
        child.parent.mkdir()
        child.write_text(
            """<project><parent><groupId>com.example</groupId><artifactId>parent</artifactId>
  <version>1</version><relativePath>../pom.xml</relativePath></parent>
  <artifactId>child</artifactId><dependencies><dependency>
    <groupId>org.example</groupId><artifactId>library</artifactId><version>2</version>
  </dependency></dependencies></project>""",
            encoding="utf-8",
        )
        parsed: list[Path] = []
        real_parse = maven_model.ET.parse

        def counting_parse(path):
            parsed.append(Path(path))
            return real_parse(path)

        monkeypatch.setattr(maven_model.ET, "parse", counting_parse)

        records = extract_external_systems(tmp_path, [root, child])

        assert [record.name for record in records] == ["org.example:library"]
        assert len(parsed) == 2
        assert set(parsed) == {root.resolve(), child.resolve()}
