"""Cross-repository Maven coordinate detection and evidence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.ingestion.external_systems.maven_model import load_maven_reactor
from repowise.core.workspace.cross_repo import (
    CrossRepoOverlay,
    CrossRepoPackageDep,
    CrossRepoPackageDiagnostic,
    detect_package_dependencies,
    detect_package_dependencies_with_diagnostics,
    load_overlay,
    save_overlay,
)


def _write(repo: Path, relative: str, body: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _project(
    group: str,
    artifact: str,
    version: str = "1.0.0",
    dependencies: str = "",
) -> str:
    return f"""<project>
  <groupId>{group}</groupId>
  <artifactId>{artifact}</artifactId>
  <version>{version}</version>
  {dependencies}
</project>"""


def _dependency(
    group: str,
    artifact: str,
    *,
    version: str = "1.0.0",
    scope: str = "",
    optional: bool = False,
) -> str:
    scope_xml = f"<scope>{scope}</scope>" if scope else ""
    optional_xml = "<optional>true</optional>" if optional else ""
    return f"""<dependency>
      <groupId>{group}</groupId><artifactId>{artifact}</artifactId>
      <version>{version}</version>{scope_xml}{optional_xml}
    </dependency>"""


def test_reactor_parent_properties_create_evidenced_package_edge(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(
        producer,
        "pom.xml",
        """<project>
  <groupId>com.acme</groupId><artifactId>producer-reactor</artifactId>
  <version>${revision}</version><packaging>pom</packaging>
  <properties><revision>1.4.0</revision></properties>
  <modules><module>shared-lib</module></modules>
</project>""",
    )
    _write(
        producer,
        "shared-lib/pom.xml",
        """<project>
  <parent>
    <groupId>com.acme</groupId><artifactId>producer-reactor</artifactId>
    <version>${revision}</version><relativePath>../pom.xml</relativePath>
  </parent>
  <artifactId>shared-lib</artifactId>
</project>""",
    )
    _write(
        consumer,
        "pom.xml",
        f"""<project>
  <groupId>com.acme</groupId><artifactId>consumer</artifactId><version>1</version>
  <properties><shared.version>1.4.0</shared.version></properties>
  <dependencies>
    {_dependency("com.acme", "shared-lib", version="${shared.version}")}
    {_dependency("junit", "junit", scope="test")}
    {_dependency("com.acme", "optional-tool", optional=True)}
  </dependencies>
  <profiles><profile><id>inactive</id><dependencies>
    {_dependency("com.acme", "profile-only")}
  </dependencies></profile></profiles>
</project>""",
    )

    deps, diagnostics, total = detect_package_dependencies_with_diagnostics(
        {"producer": producer, "consumer": consumer}
    )

    assert len(deps) == 1
    dep = deps[0]
    assert dep.source_repo == "consumer"
    assert dep.target_repo == "producer"
    assert dep.source_manifest == "pom.xml"
    assert dep.target_manifest == "shared-lib/pom.xml"
    assert dep.kind == "maven_coordinate"
    assert dep.target_package == "com.acme:shared-lib"
    assert dep.requested_version == "1.4.0"
    assert dep.scope == "compile"
    assert dep.resolution_basis == "unique_workspace_coordinate"
    assert diagnostics == []
    assert total == 0
    reversed_deps = detect_package_dependencies({"consumer": consumer, "producer": producer})
    assert reversed_deps == deps


def test_inherited_dependency_management_resolves_version(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared", "2.1.0"))
    _write(
        consumer,
        "pom.xml",
        """<project>
  <groupId>com.acme</groupId><artifactId>consumer-parent</artifactId>
  <version>1</version><packaging>pom</packaging>
  <dependencyManagement><dependencies><dependency>
    <groupId>com.acme</groupId><artifactId>shared</artifactId><version>2.1.0</version>
  </dependency></dependencies></dependencyManagement>
  <modules><module>app</module></modules>
</project>""",
    )
    _write(
        consumer,
        "app/pom.xml",
        """<project>
  <parent><groupId>com.acme</groupId><artifactId>consumer-parent</artifactId>
    <version>1</version><relativePath>../pom.xml</relativePath></parent>
  <artifactId>app</artifactId>
  <dependencies><dependency>
    <groupId>com.acme</groupId><artifactId>shared</artifactId>
  </dependency></dependencies>
</project>""",
    )

    deps = detect_package_dependencies({"producer": producer, "consumer": consumer})

    assert len(deps) == 1
    assert deps[0].requested_version == "2.1.0"
    assert deps[0].source_manifest == "app/pom.xml"


def test_inherited_dependency_is_attributed_to_each_effective_consumer(
    tmp_path: Path,
) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared"))
    _write(
        consumer,
        "pom.xml",
        f"""<project><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><packaging>pom</packaging>
  <modules><module>one</module><module>two</module></modules>
  <dependencies>{_dependency("com.acme", "shared")}</dependencies>
</project>""",
    )
    for child in ("one", "two"):
        _write(
            consumer,
            f"{child}/pom.xml",
            f"""<project><parent><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><relativePath>../pom.xml</relativePath></parent>
  <artifactId>{child}</artifactId></project>""",
        )

    deps = detect_package_dependencies({"producer": producer, "consumer": consumer})

    assert [dep.source_manifest for dep in deps] == [
        "one/pom.xml",
        "pom.xml",
        "two/pom.xml",
    ]


def test_child_dependency_override_replaces_inherited_dependency(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared"))
    _write(
        consumer,
        "pom.xml",
        f"""<project><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><packaging>pom</packaging><modules><module>app</module></modules>
  <dependencies>{_dependency("com.acme", "shared")}</dependencies></project>""",
    )
    _write(
        consumer,
        "app/pom.xml",
        f"""<project><parent><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><relativePath>../pom.xml</relativePath></parent>
  <artifactId>app</artifactId>
  <dependencies>{_dependency("com.acme", "shared", scope="test")}</dependencies>
</project>""",
    )

    deps = detect_package_dependencies({"producer": producer, "consumer": consumer})

    assert [dep.source_manifest for dep in deps] == ["pom.xml"]


def test_inherited_dependency_expressions_use_child_properties(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared", "2.1.0"))
    inherited = """<dependency><groupId>${dep.group}</groupId><artifactId>shared</artifactId>
  <version>${dep.version}</version><scope>${dep.scope}</scope>
  <optional>${dep.optional}</optional><type>${dep.type}</type></dependency>"""
    _write(
        consumer,
        "pom.xml",
        f"""<project><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><packaging>pom</packaging>
  <properties><dep.group>com.acme</dep.group><dep.version>1.0.0</dep.version>
    <dep.scope>compile</dep.scope><dep.optional>false</dep.optional><dep.type>jar</dep.type></properties>
  <modules><module>included</module><module>optional</module><module>bom</module></modules>
  <dependencies>{inherited}</dependencies></project>""",
    )
    child_template = """<project><parent><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><relativePath>../pom.xml</relativePath></parent>
  <artifactId>{artifact}</artifactId><properties>{properties}</properties></project>"""
    _write(
        consumer,
        "included/pom.xml",
        child_template.format(
            artifact="included",
            properties="<dep.version>2.1.0</dep.version><dep.scope>runtime</dep.scope>",
        ),
    )
    _write(
        consumer,
        "optional/pom.xml",
        child_template.format(
            artifact="optional",
            properties="<dep.optional>true</dep.optional>",
        ),
    )
    _write(
        consumer,
        "bom/pom.xml",
        child_template.format(artifact="bom", properties="<dep.type>pom</dep.type>"),
    )

    deps = detect_package_dependencies({"producer": producer, "consumer": consumer})

    by_manifest = {dep.source_manifest: dep for dep in deps}
    assert set(by_manifest) == {"included/pom.xml", "pom.xml"}
    assert by_manifest["included/pom.xml"].requested_version == "2.1.0"
    assert by_manifest["included/pom.xml"].scope == "runtime"


@pytest.mark.parametrize("metadata", ["scope", "optional", "type", "classifier"])
def test_unresolved_structural_metadata_refuses_edge(tmp_path: Path, metadata: str) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared"))
    _write(
        consumer,
        "pom.xml",
        f"""<project><groupId>com.acme</groupId><artifactId>consumer</artifactId>
  <version>1</version><dependencies><dependency><groupId>com.acme</groupId>
  <artifactId>shared</artifactId><version>1</version>
  <{metadata}>${{missing.{metadata}}}</{metadata}></dependency></dependencies></project>""",
    )

    deps, diagnostics, _total = detect_package_dependencies_with_diagnostics(
        {"producer": producer, "consumer": consumer}
    )

    assert deps == []
    assert "unresolved_dependency_metadata" in {diagnostic.code for diagnostic in diagnostics}


def test_classified_child_dependency_does_not_override_inherited_jar(
    tmp_path: Path,
) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared"))
    _write(
        consumer,
        "pom.xml",
        f"""<project><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><packaging>pom</packaging><modules><module>app</module></modules>
  <dependencies>{_dependency("com.acme", "shared")}</dependencies></project>""",
    )
    _write(
        consumer,
        "app/pom.xml",
        """<project><parent><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><relativePath>../pom.xml</relativePath></parent><artifactId>app</artifactId>
  <dependencies><dependency><groupId>com.acme</groupId><artifactId>shared</artifactId>
  <version>1</version><classifier>tests</classifier><scope>test</scope></dependency></dependencies>
</project>""",
    )

    deps = detect_package_dependencies({"producer": producer, "consumer": consumer})

    assert [dep.source_manifest for dep in deps] == ["app/pom.xml", "pom.xml"]


def test_duplicate_producer_coordinate_is_ambiguous(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    consumer = tmp_path / "consumer"
    _write(first, "pom.xml", _project("com.acme", "shared"))
    _write(second, "pom.xml", _project("com.acme", "shared"))
    _write(
        consumer,
        "pom.xml",
        _project(
            "com.acme",
            "consumer",
            dependencies=f"<dependencies>{_dependency('com.acme', 'shared')}</dependencies>",
        ),
    )

    deps, diagnostics, total = detect_package_dependencies_with_diagnostics(
        {"first": first, "second": second, "consumer": consumer}
    )

    assert deps == []
    assert total == 1
    assert diagnostics[0].code == "ambiguous_coordinate"
    assert "first:pom.xml" in diagnostics[0].detail
    assert "second:pom.xml" in diagnostics[0].detail


@pytest.mark.parametrize(
    ("scope", "optional", "expected"),
    [
        ("", False, True),
        ("runtime", False, True),
        ("test", False, False),
        ("provided", False, False),
        ("system", False, False),
        ("compile", True, False),
    ],
)
def test_only_production_scopes_create_edges(
    tmp_path: Path,
    scope: str,
    optional: bool,
    expected: bool,
) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared"))
    _write(
        consumer,
        "pom.xml",
        _project(
            "com.acme",
            "consumer",
            dependencies=(
                "<dependencies>"
                + _dependency("com.acme", "shared", scope=scope, optional=optional)
                + "</dependencies>"
            ),
        ),
    )

    deps = detect_package_dependencies({"producer": producer, "consumer": consumer})

    assert bool(deps) is expected


def test_distinct_consumer_manifests_keep_distinct_evidence(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "shared"))
    _write(
        consumer,
        "pom.xml",
        """<project><groupId>com.acme</groupId><artifactId>apps</artifactId>
  <version>1</version><packaging>pom</packaging>
  <modules><module>one</module><module>two</module></modules></project>""",
    )

    def child(artifact: str) -> str:
        return f"""<project>
  <parent><groupId>com.acme</groupId><artifactId>apps</artifactId>
    <version>1</version><relativePath>../pom.xml</relativePath></parent>
  <artifactId>{artifact}</artifactId><dependencies>
    {_dependency("com.acme", "shared")}
  </dependencies></project>"""

    _write(consumer, "one/pom.xml", child("one"))
    _write(consumer, "two/pom.xml", child("two"))

    deps = detect_package_dependencies({"producer": producer, "consumer": consumer})

    assert [dep.source_manifest for dep in deps] == ["one/pom.xml", "two/pom.xml"]


def test_root_reactor_ignores_unlisted_nested_fixture_poms(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", _project("com.acme", "real-project"))
    _write(
        producer,
        "fixtures/pom.xml",
        _project("com.acme", "fixture-only"),
    )
    _write(
        consumer,
        "pom.xml",
        _project(
            "com.acme",
            "consumer",
            dependencies=(
                "<dependencies>" + _dependency("com.acme", "fixture-only") + "</dependencies>"
            ),
        ),
    )

    deps, diagnostics, _total = detect_package_dependencies_with_diagnostics(
        {"producer": producer, "consumer": consumer}
    )

    assert deps == []
    assert [diagnostic.code for diagnostic in diagnostics] == ["external_coordinate"]


def test_malformed_root_does_not_promote_nested_fixture_pom(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write(producer, "pom.xml", "<project>")
    _write(
        producer,
        "fixtures/pom.xml",
        _project("com.acme", "fixture-only"),
    )
    _write(
        consumer,
        "pom.xml",
        _project(
            "com.acme",
            "consumer",
            dependencies=(
                "<dependencies>" + _dependency("com.acme", "fixture-only") + "</dependencies>"
            ),
        ),
    )

    deps, diagnostics, _total = detect_package_dependencies_with_diagnostics(
        {"producer": producer, "consumer": consumer}
    )

    assert deps == []
    assert {diagnostic.code for diagnostic in diagnostics} == {
        "external_coordinate",
        "malformed_pom",
    }


def test_malformed_and_unmatched_poms_are_diagnostic_not_edges(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    consumer = tmp_path / "consumer"
    _write(broken, "pom.xml", "<project>")
    _write(
        consumer,
        "pom.xml",
        _project(
            "com.acme",
            "consumer",
            dependencies=f"<dependencies>{_dependency('com.vendor', 'external')}</dependencies>",
        ),
    )

    deps, diagnostics, total = detect_package_dependencies_with_diagnostics(
        {"broken": broken, "consumer": consumer}
    )

    assert deps == []
    assert total == 2
    assert {diagnostic.code for diagnostic in diagnostics} == {
        "external_coordinate",
        "malformed_pom",
    }


def test_unresolved_properties_and_modules_are_diagnostic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(
        repo,
        "pom.xml",
        """<project>
  <groupId>com.acme</groupId><artifactId>root</artifactId>
  <version>${missing.version}</version>
  <modules><module>${missing.module}</module></modules>
  <dependencies><dependency>
    <groupId>${missing.group}</groupId><artifactId>shared</artifactId>
    <version>${missing.dependency.version}</version>
  </dependency></dependencies>
</project>""",
    )

    deps, diagnostics, _total = detect_package_dependencies_with_diagnostics({"repo": repo})

    assert deps == []
    assert {diagnostic.code for diagnostic in diagnostics} >= {
        "unresolved_project_version",
        "unresolved_module",
        "unresolved_dependency_coordinate",
        "unresolved_dependency_version",
    }


def test_local_parent_cycle_is_bounded_and_diagnostic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(
        repo,
        "one/pom.xml",
        """<project><parent><groupId>com.acme</groupId><artifactId>two</artifactId>
  <version>1</version><relativePath>../two/pom.xml</relativePath></parent>
  <artifactId>one</artifactId></project>""",
    )
    _write(
        repo,
        "two/pom.xml",
        """<project><parent><groupId>com.acme</groupId><artifactId>one</artifactId>
  <version>1</version><relativePath>../one/pom.xml</relativePath></parent>
  <artifactId>two</artifactId></project>""",
    )

    manifests = sorted(repo.rglob("pom.xml"))
    reactor = load_maven_reactor(repo, manifests)
    deps, diagnostics, _total = detect_package_dependencies_with_diagnostics({"repo": repo})

    assert deps == []
    assert reactor.projects == ()
    assert "parent_cycle" in {diagnostic.code for diagnostic in diagnostics}


def test_declared_failed_module_keeps_parent_failure_diagnostic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(
        repo,
        "pom.xml",
        """<project><groupId>com.acme</groupId><artifactId>root</artifactId><version>1</version>
  <packaging>pom</packaging><modules><module>child</module></modules></project>""",
    )
    _write(
        repo,
        "child/pom.xml",
        """<project><parent><groupId>com.acme</groupId><artifactId>one</artifactId>
  <version>1</version><relativePath>../parents/one/pom.xml</relativePath></parent>
  <artifactId>child</artifactId></project>""",
    )
    _write(
        repo,
        "parents/one/pom.xml",
        """<project><parent><groupId>com.acme</groupId><artifactId>two</artifactId>
  <version>1</version><relativePath>../two/pom.xml</relativePath></parent>
  <groupId>com.acme</groupId><artifactId>one</artifactId><version>1</version></project>""",
    )
    _write(
        repo,
        "parents/two/pom.xml",
        """<project><parent><groupId>com.acme</groupId><artifactId>one</artifactId>
  <version>1</version><relativePath>../one/pom.xml</relativePath></parent>
  <groupId>com.acme</groupId><artifactId>two</artifactId><version>1</version></project>""",
    )

    _deps, diagnostics, _total = detect_package_dependencies_with_diagnostics({"repo": repo})

    assert any(
        diagnostic.source_manifest == "child/pom.xml" and diagnostic.code == "parent_cycle"
        for diagnostic in diagnostics
    )


def test_mismatched_local_parent_is_not_used(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(
        repo,
        "parent/pom.xml",
        _project("com.acme", "actual-parent", "1"),
    )
    _write(
        repo,
        "child/pom.xml",
        """<project><parent><groupId>com.acme</groupId>
  <artifactId>different-parent</artifactId><version>1</version>
  <relativePath>../parent/pom.xml</relativePath></parent>
  <artifactId>child</artifactId></project>""",
    )

    _deps, diagnostics, _total = detect_package_dependencies_with_diagnostics({"repo": repo})

    assert "parent_coordinate_mismatch" in {diagnostic.code for diagnostic in diagnostics}


def test_parent_depth_is_bounded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    for index in range(18):
        parent = ""
        if index < 17:
            parent = f"""<parent><groupId>com.acme</groupId>
  <artifactId>level-{index + 1}</artifactId><version>1</version>
  <relativePath>../level-{index + 1}/pom.xml</relativePath></parent>"""
        _write(
            repo,
            f"level-{index}/pom.xml",
            f"""<project>{parent}<groupId>com.acme</groupId>
  <artifactId>level-{index}</artifactId><version>1</version></project>""",
        )

    manifests = sorted(repo.rglob("pom.xml"))
    reactor = load_maven_reactor(repo, manifests)
    _deps, diagnostics, _total = detect_package_dependencies_with_diagnostics({"repo": repo})

    assert reactor.projects == ()
    assert "parent_depth_exceeded" in {diagnostic.code for diagnostic in diagnostics}


def test_external_parent_is_explicitly_diagnostic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(
        repo,
        "pom.xml",
        """<project><parent><groupId>org.example</groupId><artifactId>remote-parent</artifactId>
  <version>1</version><relativePath/></parent><artifactId>child</artifactId></project>""",
    )

    _deps, diagnostics, _total = detect_package_dependencies_with_diagnostics({"repo": repo})

    assert "external_parent_unsupported" in {diagnostic.code for diagnostic in diagnostics}


def test_external_diagnostics_are_capped_with_honest_total(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    dependencies = "".join(_dependency("org.example", f"library-{index}") for index in range(230))
    _write(
        repo,
        "pom.xml",
        _project(
            "com.acme",
            "consumer",
            dependencies=f"<dependencies>{dependencies}</dependencies>",
        ),
    )

    _deps, diagnostics, total = detect_package_dependencies_with_diagnostics({"repo": repo})

    assert len(diagnostics) == 200
    assert total == 230
    assert {diagnostic.code for diagnostic in diagnostics} == {"external_coordinate"}


def test_overlay_round_trip_preserves_maven_evidence_and_diagnostics(
    tmp_path: Path,
) -> None:
    overlay = CrossRepoOverlay(
        package_deps=[
            CrossRepoPackageDep(
                source_repo="consumer",
                target_repo="producer",
                source_manifest="app/pom.xml",
                kind="maven_coordinate",
                target_package="com.acme:shared",
                target_manifest="shared/pom.xml",
                requested_version="1.2.0",
                scope="runtime",
                resolution_basis="unique_workspace_coordinate",
            )
        ],
        package_diagnostics=[
            CrossRepoPackageDiagnostic(
                repo="consumer",
                source_manifest="pom.xml",
                code="external_coordinate",
                detail="org.example:outside",
            )
        ],
        total_package_diagnostics=3,
    )

    save_overlay(overlay, tmp_path)
    restored = load_overlay(tmp_path)

    assert restored is not None
    assert restored.package_deps == overlay.package_deps
    assert restored.package_diagnostics == overlay.package_diagnostics
    assert restored.total_package_diagnostics == 3


def test_path_dependency_uses_separator_aware_repo_containment(tmp_path: Path) -> None:
    app = tmp_path / "app"
    lib = tmp_path / "lib"
    lib_old = tmp_path / "lib-old"
    app.mkdir()
    lib.mkdir()
    lib_old.mkdir()
    (app / "package.json").write_text(
        json.dumps({"dependencies": {"legacy": "file:../lib-old"}}),
        encoding="utf-8",
    )

    deps = detect_package_dependencies({"app": app, "lib": lib, "lib-old": lib_old})

    assert len(deps) == 1
    assert deps[0].target_repo == "lib-old"
