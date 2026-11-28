"""Gradle build scripts feed the same ``maven`` package store as ``pom.xml``."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.change_risk.fix_history import is_dependency_file
from repowise.core.ingestion.external_systems import extract_external_systems, is_manifest_path


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _by_name(root: Path) -> dict[tuple[str, str], object]:
    return {(r.declared_in, r.name): r for r in extract_external_systems(root)}


def test_groovy_forms_and_managed_versions(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "buildSrc/src/main/groovy/conventions.gradle",
        """
        dependencyManagement {
            imports { mavenBom 'org.springframework.boot:spring-boot-dependencies:4.1.1' }
            dependencies { dependency 'com.google.guava:guava:33.6.0-jre' }
        }
        """,
    )
    _write(tmp_path, "buildSrc/build.gradle", "dependencies { implementation 'org.freemarker:freemarker' }")
    _write(tmp_path, "core/build.gradle", "apply from: 'dependencies.gradle'")
    _write(
        tmp_path,
        "core/dependencies.gradle",
        """
        dependencies {
            // implementation 'commented:out'
            implementation(project(path: ':fineract-command'))
            implementation(
                    'org.springframework.boot:spring-boot-starter-webmvc',
                    'com.google.guava:guava',
                    )
            implementation ('org.dom4j:dom4j') {
                exclude group: 'javax.xml.bind'
            }
            implementation platform('io.micrometer:micrometer-bom:1.16.5')
            compileOnly 'org.projectlombok:lombok:1.18.46'
            testImplementation group: 'org.mockito', name: 'mockito-core', version: '5.0.0'
        }
        """,
    )
    found = _by_name(tmp_path)
    file = "core/dependencies.gradle"
    assert set(found) == {
        (file, "org.springframework.boot:spring-boot-starter-webmvc"),
        (file, "com.google.guava:guava"),
        (file, "org.dom4j:dom4j"),
        (file, "org.projectlombok:lombok"),
        (file, "org.mockito:mockito-core"),
    }
    guava = found[(file, "com.google.guava:guava")]
    assert guava.ecosystem == "maven" and guava.version == "33.6.0-jre" and not guava.is_dev_dep
    assert found[(file, "org.springframework.boot:spring-boot-starter-webmvc")].version is None
    assert found[(file, "org.projectlombok:lombok")].is_dev_dep
    assert found[(file, "org.mockito:mockito-core")].version == "5.0.0"


def test_kotlin_dsl_catalog_properties_and_platforms(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "gradle/libs.versions.toml",
        """
        [versions]
        okhttp = "5.0.0"
        [libraries]
        okhttp-core = { module = "com.squareup.okhttp3:okhttp", version.ref = "okhttp" }
        junit_api = "org.junit.jupiter:junit-jupiter-api:5.10.0"
        compose-bom = { group = "androidx.compose", name = "compose-bom", version = "2025.01.01" }
        [bundles]
        testing = ["junit-api"]
        """,
    )
    _write(tmp_path, "app/gradle.properties", "logback_version=1.5.21\n")
    _write(
        tmp_path,
        "app/build.gradle.kts",
        """
        dependencies {
            implementation(libs.okhttp.core)
            implementation(platform(libs.compose.bom))
            implementation(project.dependencies.enforcedPlatform("io.ktor:ktor-bom:3.6.0"))
            implementation(project(":shared"))
            implementation("ch.qos.logback:logback-classic:$logback_version")
            implementation("io.ktor:ktor-server-core:${undefinedVersion}")
            testImplementation(libs.bundles.testing)
            testImplementation(kotlin("test"))
        }
        """,
    )
    found = _by_name(tmp_path)
    file = "app/build.gradle.kts"
    assert {name for _f, name in found} == {
        "com.squareup.okhttp3:okhttp",
        "ch.qos.logback:logback-classic",
        "io.ktor:ktor-server-core",
        "org.junit.jupiter:junit-jupiter-api",
        "org.jetbrains.kotlin:kotlin-test",
    }
    assert found[(file, "com.squareup.okhttp3:okhttp")].version == "5.0.0"
    assert found[(file, "ch.qos.logback:logback-classic")].version == "1.5.21"
    assert found[(file, "io.ktor:ktor-server-core")].version is None  # unresolved property
    assert found[(file, "org.junit.jupiter:junit-jupiter-api")].is_dev_dep


def test_gradle_scripts_are_manifests_but_build_logic_for_fix_history() -> None:
    for name in ("build.gradle", "build.gradle.kts", "core/dependencies.gradle", "gradle/libs.versions.toml"):
        assert is_manifest_path(name)
        assert not is_dependency_file(name)
    assert not is_manifest_path("settings.gradle.kts")


def test_interpolation_strings_and_assignments(tmp_path: Path) -> None:
    _write(tmp_path, "gradle.properties", "fooVersion=2\n")
    _write(
        tmp_path,
        "build.gradle",
        """
        def runtime = 'not:a-dependency'
        dependencies {
            implementation "org.foo:bar:${fooVersion}"
            implementation("c:d)", "e:f")
            implementation 'org.q:r:1' { exclude group: 'x:y' }
        }
        """,
    )
    found = {r.name: r for r in extract_external_systems(tmp_path)}
    assert set(found) == {"org.foo:bar", "e:f", "org.q:r"}  # "c:d)" is no coordinate
    assert found["org.foo:bar"].version == "2"
