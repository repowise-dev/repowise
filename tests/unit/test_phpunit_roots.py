"""Where PHPUnit collects tests from, read from its XML config files."""

from __future__ import annotations

import pytest

from repowise.core.phpunit_roots import (
    PhpunitConfigUnreadableError,
    parse_phpunit_options,
    phpunit_options,
    read_phpunit_roots,
)

_LARAVEL_CONFIG = """
<phpunit>
    <testsuites>
        <testsuite name="Suite">
            <directory suffix="Test.php">./tests</directory>
        </testsuite>
    </testsuites>
</phpunit>
"""

_MULTI_SUITE_CONFIG = """
<phpunit>
    <testsuites>
        <testsuite name="Unit">
            <directory>tests/Unit</directory>
        </testsuite>
        <testsuite name="Feature">
            <directory>tests/Feature</directory>
        </testsuite>
    </testsuites>
</phpunit>
"""

_EXCLUDE_AND_FILE_CONFIG = """
<phpunit>
    <testsuites>
        <testsuite name="Suite">
            <directory suffix="Test.php">tests</directory>
            <file>custom/SpecialCase.php</file>
            <exclude>tests/Fixtures</exclude>
        </testsuite>
    </testsuites>
</phpunit>
"""


@pytest.mark.parametrize(
    ("files", "path", "expected"),
    [
        ({"phpunit.xml.dist": _LARAVEL_CONFIG}, "tests/RouterTest.php", True),
        ({"phpunit.xml.dist": _LARAVEL_CONFIG}, "tests/Sub/DeepTest.php", True),
        ({"phpunit.xml.dist": _LARAVEL_CONFIG}, "src/Testing/Attributes/UnitTest.php", False),
        ({"phpunit.xml.dist": _LARAVEL_CONFIG}, "src/Console/Concerns/CreatesMatchingTest.php", False),
        # Default suffix is Test.php when omitted
        ({"phpunit.xml": _MULTI_SUITE_CONFIG}, "tests/Unit/UserTest.php", True),
        ({"phpunit.xml": _MULTI_SUITE_CONFIG}, "tests/Feature/LoginTest.php", True),
        ({"phpunit.xml": _MULTI_SUITE_CONFIG}, "src/Support/SomeTest.php", False),
        # Explicit file and exclude
        ({"phpunit.xml": _EXCLUDE_AND_FILE_CONFIG}, "custom/SpecialCase.php", True),
        ({"phpunit.xml": _EXCLUDE_AND_FILE_CONFIG}, "tests/RouterTest.php", True),
        ({"phpunit.xml": _EXCLUDE_AND_FILE_CONFIG}, "tests/Fixtures/MockTest.php", False),
        # No testsuites or malformed XML yields None (fail-closed)
        ({"phpunit.xml": "<phpunit></phpunit>"}, "src/UnitTest.php", None),
        ({"phpunit.xml": "<phpunit><testsuites></testsuites></phpunit>"}, "src/UnitTest.php", None),
        ({"phpunit.xml": "not valid xml <"}, "src/UnitTest.php", None),
        ({}, "src/UnitTest.php", None),
    ],
)
def test_collects_follows_the_root_config(files, path, expected) -> None:
    assert read_phpunit_roots(files.items()).collects(path) is expected


def test_directory_with_custom_suffix_and_prefix() -> None:
    config = """
    <phpunit>
        <testsuites>
            <testsuite name="Custom">
                <directory suffix="Spec.php" prefix="test_">specs</directory>
            </testsuite>
        </testsuites>
    </phpunit>
    """
    roots = read_phpunit_roots([("phpunit.xml", config)])
    assert roots.collects("specs/test_authSpec.php") is True
    assert roots.collects("specs/authSpec.php") is False
    assert roots.collects("specs/test_authTest.php") is False


def test_directory_level_exclude_child_tag() -> None:
    config = """
    <phpunit>
        <testsuites>
            <testsuite name="Suite">
                <directory suffix="Test.php">
                    tests
                    <exclude>tests/Helpers</exclude>
                </directory>
            </testsuite>
        </testsuites>
    </phpunit>
    """
    roots = read_phpunit_roots([("phpunit.xml", config)])
    assert roots.collects("tests/RouterTest.php") is True
    assert roots.collects("tests/Helpers/MockTest.php") is False


def test_the_nearest_config_governs_its_own_directory() -> None:
    roots = read_phpunit_roots(
        [
            ("phpunit.xml.dist", _LARAVEL_CONFIG),
            (
                "packages/api/phpunit.xml",
                """
                <phpunit>
                    <testsuites>
                        <testsuite name="Api">
                            <directory>tests</directory>
                        </testsuite>
                    </testsuites>
                </phpunit>
                """,
            ),
        ]
    )
    assert roots.collects("packages/api/tests/ApiTest.php") is True
    assert roots.collects("packages/api/src/ApiHelperTest.php") is False
    assert roots.collects("tests/RouterTest.php") is True
    assert roots.collects("src/Testing/Attributes/UnitTest.php") is False


def test_a_file_an_ancestor_config_collects_stays_collected() -> None:
    """If an ancestor config collects a path, it stays collected."""
    roots = read_phpunit_roots(
        [
            ("phpunit.xml", _LARAVEL_CONFIG),
            (
                "tests/sub/phpunit.xml",
                """
                <phpunit>
                    <testsuites>
                        <testsuite name="Sub">
                            <directory>other</directory>
                        </testsuite>
                    </testsuites>
                </phpunit>
                """,
            ),
        ]
    )
    # The root config collects tests/sub/RouterTest.php, so it remains collected
    assert roots.collects("tests/sub/RouterTest.php") is True


def test_precedence_in_same_directory() -> None:
    """phpunit.xml takes precedence over phpunit.xml.dist."""
    roots = read_phpunit_roots(
        [
            ("phpunit.xml.dist", _LARAVEL_CONFIG),
            (
                "phpunit.xml",
                """
                <phpunit>
                    <testsuites>
                        <testsuite name="Overridden">
                            <directory>custom_tests</directory>
                        </testsuite>
                    </testsuites>
                </phpunit>
                """,
            ),
        ]
    )
    assert roots.collects("custom_tests/CustomTest.php") is True
    assert roots.collects("tests/RouterTest.php") is False


def test_a_config_that_does_not_parse_is_recorded_as_unknown() -> None:
    with pytest.raises(PhpunitConfigUnreadableError):
        parse_phpunit_options("<phpunit><testsuites>")
    assert phpunit_options("<phpunit><testsuites>") is None
    roots = read_phpunit_roots([("phpunit.xml", "<phpunit><testsuites>")])
    assert roots.collects("src/UnitTest.php") is None


def test_malformed_primary_config_does_not_fall_back_to_dist() -> None:
    """A broken phpunit.xml does not fall back to phpunit.xml.dist (fail-closed)."""
    roots = read_phpunit_roots(
        [
            ("phpunit.xml", "malformed <xml"),
            ("phpunit.xml.dist", _LARAVEL_CONFIG),
        ]
    )
    assert roots.collects("src/UnitTest.php") is None
    assert roots.collects("tests/RouterTest.php") is None


def test_no_testsuites_primary_config_does_not_fall_back_to_dist() -> None:
    """A phpunit.xml without testsuites does not fall back to phpunit.xml.dist."""
    roots = read_phpunit_roots(
        [
            ("phpunit.xml", "<phpunit></phpunit>"),
            ("phpunit.xml.dist", _LARAVEL_CONFIG),
        ]
    )
    assert roots.collects("src/UnitTest.php") is None
    assert roots.collects("tests/RouterTest.php") is None


def test_valid_primary_config_ignores_malformed_dist() -> None:
    """A valid phpunit.xml wins precedence over a malformed phpunit.xml.dist."""
    roots = read_phpunit_roots(
        [
            ("phpunit.xml", _LARAVEL_CONFIG),
            ("phpunit.xml.dist", "malformed <xml"),
        ]
    )
    assert roots.collects("tests/RouterTest.php") is True
    assert roots.collects("src/UnitTest.php") is False


def test_unreadable_primary_config_in_subdirectory_does_not_fall_back() -> None:
    roots = read_phpunit_roots(
        [
            ("phpunit.xml", _LARAVEL_CONFIG),
            ("packages/api/phpunit.xml", "malformed <xml"),
            ("packages/api/phpunit.xml.dist", _LARAVEL_CONFIG),
        ]
    )
    assert roots.collects("packages/api/src/ApiTest.php") is None
    assert roots.collects("tests/RouterTest.php") is True


def test_precedence_dist_files_order() -> None:
    """phpunit.dist.xml takes precedence over phpunit.xml.dist when both are present."""
    roots = read_phpunit_roots(
        [
            ("phpunit.xml.dist", _LARAVEL_CONFIG),
            (
                "phpunit.dist.xml",
                """
                <phpunit>
                    <testsuites>
                        <testsuite name="DistXml">
                            <directory>dist_tests</directory>
                        </testsuite>
                    </testsuites>
                </phpunit>
                """,
            ),
        ]
    )
    assert roots.collects("dist_tests/SomeTest.php") is True
    assert roots.collects("tests/RouterTest.php") is False


def test_valid_dist_xml_ignores_malformed_xml_dist() -> None:
    """A valid phpunit.dist.xml wins precedence over a malformed phpunit.xml.dist."""
    roots = read_phpunit_roots(
        [
            ("phpunit.dist.xml", _LARAVEL_CONFIG),
            ("phpunit.xml.dist", "malformed <xml"),
        ]
    )
    assert roots.collects("tests/RouterTest.php") is True
    assert roots.collects("src/UnitTest.php") is False


def test_malformed_dist_xml_does_not_fall_back_to_xml_dist() -> None:
    """A broken phpunit.dist.xml does not fall back to phpunit.xml.dist (fail-closed)."""
    roots = read_phpunit_roots(
        [
            ("phpunit.dist.xml", "malformed <xml"),
            ("phpunit.xml.dist", _LARAVEL_CONFIG),
        ]
    )
    assert roots.collects("tests/RouterTest.php") is None
    assert roots.collects("src/UnitTest.php") is None


