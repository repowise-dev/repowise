"""JVM classes a main resource file names for a framework to load are not dead.

Shapes from apollo, eladmin and litemall, every one reported dead: an H2
``CREATE ALIAS ... FOR "pkg.H2Function.unixTimestamp"`` in an init ``.sql``, a
Hibernate ``metadata_builder_contributor`` in ``application.yml``, a MyBatis
``typeHandler=`` in a mapper ``.xml`` and a P6Spy ``logMessageFormat=`` in
``spy.properties``. A class named only by test resources, and one nothing
names, stay reported.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_COMMON = "common/src/main/java/org/sample/common"
_RES = "app/src/main/resources"


def _cls(package: str, name: str) -> str:
    return f"package org.sample.{package};\n\npublic class {name} {{\n}}\n"


_FILES = {
    "settings.gradle": "rootProject.name = 'sample'\ninclude 'common', 'app'\n",
    "build.gradle": "apply plugin: 'java'\n",
    f"{_COMMON}/jpa/H2Function.java": _cls("common.jpa", "H2Function"),
    f"{_COMMON}/hibernate/SqlFunctionsContributor.java": _cls(
        "common.hibernate", "SqlFunctionsContributor"
    ),
    f"{_COMMON}/mybatis/JsonStringArrayTypeHandler.java": _cls(
        "common.mybatis", "JsonStringArrayTypeHandler"
    ),
    f"{_COMMON}/logging/CustomP6SpyLogger.java": _cls("common.logging", "CustomP6SpyLogger"),
    f"{_COMMON}/testonly/TestOnlyFunction.java": _cls("common.testonly", "TestOnlyFunction"),
    f"{_COMMON}/orphan/Orphan.java": _cls("common.orphan", "Orphan"),
    # Each named only inside a comment of one resource format.
    **{
        f"{_COMMON}/{name.lower()}/{name}.java": _cls(f"common.{name.lower()}", name)
        for name in ("InXmlComment", "InYmlComment", "InPropsComment", "InSqlComment")
    },
    f"{_RES}/jpa/configdb.init.h2.sql": (
        "CREATE ALIAS IF NOT EXISTS UNIX_TIMESTAMP FOR "
        '"org.sample.common.jpa.H2Function.unixTimestamp";\n'
        "-- CREATE ALIAS OLD FOR \"org.sample.common.insqlcomment.InSqlComment.run\";\n"
    ),
    f"{_RES}/application.yml": (
        "spring:\n  jpa:\n    properties:\n      hibernate:\n"
        "        metadata_builder_contributor: "
        "org.sample.common.hibernate.SqlFunctionsContributor\n"
        "        # was: org.sample.common.inymlcomment.InYmlComment\n"
    ),
    f"{_RES}/mapper/OrderMapper.xml": (
        '<mapper namespace="x">\n  <result column="tags" property="tags" '
        'typeHandler="org.sample.common.mybatis.JsonStringArrayTypeHandler"/>\n'
        "  <!-- typeHandler=org.sample.common.inxmlcomment.InXmlComment -->\n</mapper>\n"
    ),
    f"{_RES}/spy.properties": (
        "logMessageFormat=org.sample.common.logging.CustomP6SpyLogger\n"
        "# logMessageFormat=org.sample.common.inpropscomment.InPropsComment\n"
    ),
    "app/src/test/resources/import.sql": (
        'CREATE ALIAS T FOR "org.sample.common.testonly.TestOnlyFunction.run";\n'
    ),
}


def _report(root: Path):
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(root).traverse():
        source = Path(fi.abs_path).read_bytes()
        source_map[fi.path] = source
        builder.add_file(parser.parse_file(fi, source))
    builder.set_source_map(source_map)
    graph = builder.build()
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={}, source_map=source_map, repo_root=root)
    return analyzer.analyze({"min_confidence": 0.0, "detect_zombie_packages": False})


def _flagged(report) -> set[str]:
    return {
        f.symbol_name if f.kind is DeadCodeKind.UNUSED_EXPORT else Path(f.file_path).name
        for f in report.findings
        if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
    }


def test_classes_main_resources_name_are_not_reported(tmp_path: Path) -> None:
    flagged = _flagged(_report(tmp_path))
    named = {"H2Function", "SqlFunctionsContributor", "JsonStringArrayTypeHandler", "CustomP6SpyLogger"}
    assert flagged.isdisjoint(named | {f"{n}.java" for n in named}), flagged


def test_test_resources_and_unnamed_classes_are_still_reported(tmp_path: Path) -> None:
    flagged = _flagged(_report(tmp_path))
    assert {"TestOnlyFunction", "Orphan"} <= flagged


def test_a_class_named_only_in_a_resource_comment_is_still_reported(tmp_path: Path) -> None:
    flagged = _flagged(_report(tmp_path))
    assert {"InXmlComment", "InYmlComment", "InPropsComment", "InSqlComment"} <= flagged
