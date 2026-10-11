"""Unit tests for JVM build source set detection and classification."""

from __future__ import annotations

import pytest

from repowise.core.jvm_source_sets import (
    JvmBuildUnreadableError,
    jvm_build_options,
    read_jvm_source_sets,
)


def test_jvm_build_options_direct() -> None:
    assert jvm_build_options("unknown.txt", "") is None
    with pytest.raises(JvmBuildUnreadableError):
        jvm_build_options("pom.xml", "<invalid")


def test_maven_defaults() -> None:
    pom = "<project><modelVersion>4.0.0</modelVersion></project>"
    roots = read_jvm_source_sets([("pom.xml", pom)])

    # Standard main source sets are not tests
    assert roots.collects("src/main/java/com/example/DynamicTest.java") is False
    assert roots.collects("src/main/kotlin/com/example/MySpec.kt") is False
    assert roots.collects("src/main/scala/com/example/MySuite.scala") is False

    # Standard test source sets are tests
    assert roots.collects("src/test/java/com/example/RealTest.java") is True
    assert roots.collects("src/test/kotlin/com/example/RealTest.kt") is True
    assert roots.collects("src/it/java/com/example/IntegrationTest.java") is True

    # Outside recognized source sets is None
    assert roots.collects("scripts/Generate.kt") is None


def test_maven_custom_source_directories() -> None:
    pom = """<project>
        <build>
            <sourceDirectory>custom/src</sourceDirectory>
            <testSourceDirectory>custom/tests</testSourceDirectory>
        </build>
    </project>"""
    roots = read_jvm_source_sets([("pom.xml", pom)])

    assert roots.collects("custom/src/com/example/TestRunner.java") is False
    assert roots.collects("custom/tests/com/example/RealTest.java") is True


def test_maven_dynamic_source_directory_fails_closed() -> None:
    pom = """<project>
        <build>
            <sourceDirectory>${project.basedir}/src/main/java</sourceDirectory>
        </build>
    </project>"""
    roots = read_jvm_source_sets([("pom.xml", pom)])

    # Fails closed to None (unknown)
    assert roots.collects("src/main/java/com/example/DynamicTest.java") is None


def test_maven_invalid_xml_fails_closed() -> None:
    pom = "<project><unclosed>"
    roots = read_jvm_source_sets([("pom.xml", pom)])
    assert roots.collects("src/main/java/com/example/DynamicTest.java") is None


def test_gradle_defaults_and_kmp() -> None:
    build = "plugins { id('org.jetbrains.kotlin.multiplatform') }\n"
    roots = read_jvm_source_sets([("engine/build.gradle.kts", build)])

    # KMP main source sets are main (collects is False)
    assert roots.collects("engine/src/commonMain/kotlin/io/x/core/DynamicRootTest.kt") is False
    assert roots.collects("engine/src/jvmMain/kotlin/io/x/core/JvmTest.kt") is False
    assert roots.collects("engine/src/jsMain/kotlin/io/x/core/JsTest.kt") is False
    assert roots.collects("engine/src/androidMain/kotlin/io/x/core/AndroidTest.kt") is False

    # KMP test source sets are tests (collects is True)
    assert roots.collects("engine/src/commonTest/kotlin/io/x/core/EngineTest.kt") is True
    assert roots.collects("engine/src/jvmTest/kotlin/io/x/core/EngineTest.kt") is True
    assert roots.collects("engine/src/test/kotlin/io/x/core/EngineTest.kt") is True
    assert roots.collects("engine/src/testFixtures/kotlin/io/x/core/Fixture.kt") is True


def test_gradle_literal_custom_srcdir() -> None:
    build = """
    sourceSets {
        main {
            java {
                srcDir 'src/custom/java'
            }
        }
        test {
            java {
                srcDir 'src/custom/test'
            }
        }
    }
    """
    roots = read_jvm_source_sets([("build.gradle", build)])

    assert roots.collects("src/custom/java/com/example/HelperTest.java") is False
    assert roots.collects("src/custom/test/com/example/RealTest.java") is True


def test_gradle_dynamic_expression_fails_closed() -> None:
    build = """
    sourceSets {
        main {
            java {
                srcDir layout.buildDirectory.dir("generated")
            }
        }
    }
    """
    roots = read_jvm_source_sets([("build.gradle.kts", build)])

    # Dynamic layout.buildDirectory marks file unreadable -> collects returns None
    assert roots.collects("src/main/java/com/example/FooTest.java") is None


def test_sbt_defaults() -> None:
    sbt = 'name := "my-project"\nscalaVersion := "3.3.0"\n'
    roots = read_jvm_source_sets([("build.sbt", sbt)])

    assert roots.collects("src/main/scala/org/scalatest/AsyncTestSuite.scala") is False
    assert roots.collects("src/test/scala/org/scalatest/RealTest.scala") is True


def test_nearest_governing_build_file_precedence() -> None:
    root_gradle = "// root build file\n"
    core_pom = """<project>
        <build>
            <sourceDirectory>custom_core_src</sourceDirectory>
        </build>
    </project>"""

    roots = read_jvm_source_sets([
        ("build.gradle.kts", root_gradle),
        ("core/pom.xml", core_pom),
    ])

    # Core uses core/pom.xml
    assert roots.collects("core/custom_core_src/com/example/MyTest.java") is False
    assert roots.collects("core/src/main/java/com/example/MyTest.java") is None

    # Other modules governed by root build.gradle.kts follow standard conventions
    assert roots.collects("util/src/main/java/com/example/MyTest.java") is False
    assert roots.collects("util/src/test/java/com/example/MyTest.java") is True


def test_valid_higher_precedence_shadows_unreadable_lower_precedence() -> None:
    valid_gradle_kts = "// standard gradle build\n"
    unreadable_pom = "<project><build><sourceDirectory>${dynamic.dir}</sourceDirectory></build></project>"

    roots = read_jvm_source_sets([
        ("build.gradle.kts", valid_gradle_kts),
        ("pom.xml", unreadable_pom),
    ])

    # The higher-precedence valid build.gradle.kts wins; unreadable pom.xml is shadowed
    assert roots.collects("src/main/kotlin/com/example/App.kt") is False
    assert roots.collects("src/test/kotlin/com/example/AppTest.kt") is True


def test_unreadable_higher_precedence_invalidates_directory() -> None:
    dynamic_gradle_kts = """
    sourceSets {
        main {
            java { srcDir layout.buildDirectory.dir("generated") }
        }
    }
    """
    valid_pom = "<project></project>"

    roots = read_jvm_source_sets([
        ("build.gradle.kts", dynamic_gradle_kts),
        ("pom.xml", valid_pom),
    ])

    # The higher-precedence build.gradle.kts is unreadable, so directory fails closed
    assert roots.collects("src/main/java/com/example/App.java") is None

