"""A fixture-tree namer stands for the tests that read its fixture root."""

from __future__ import annotations

from repowise.core.analysis.fixture_readers import fixture_root, with_fixture_readers


def test_a_fixture_root_is_the_directory_under_the_fixtures_directory() -> None:
    assert fixture_root("tests/fixtures/ts_sample/packages/lib/src/orphan.ts") == (
        "tests/fixtures/ts_sample"
    )
    assert fixture_root("pkg/__fixtures__/app/a.ts") == "pkg/__fixtures__/app"
    assert fixture_root("tests/fixtures/data.json") is None
    assert fixture_root("src/app.ts") is None


_TEXTS = {
    "tests/test_ts.py": 'ROOT = FIXTURES / "ts_sample"\n',
    "tests/test_all.py": "for d in (HERE / 'fixtures').iterdir():\n    index(d)\n",
    "tests/test_named.py": 'FIXTURES = HERE / "fixtures"\n',  # names the directory, walks nothing
    "tests/test_other.py": 'ROOT = FIXTURES / "ts_sample_extra"\n',
}


def _read(path: str) -> str | None:
    return _TEXTS.get(path)


def test_a_fixture_namer_becomes_the_tests_that_read_its_root() -> None:
    namers = {
        "package.json": [
            "src/cli.py",
            "tests/fixtures/ts_sample/packages/lib/src/orphan.ts",
            "tests/fixtures/ts_sample/packages/lib/src/en.ts",
        ]
    }
    out = with_fixture_readers(namers, sorted(_TEXTS), _read)
    assert out == {"package.json": ["src/cli.py", "tests/test_all.py", "tests/test_ts.py"]}


def test_a_fixture_tree_no_test_reads_keeps_its_file_as_the_namer() -> None:
    namers = {"package.json": ["tests/fixtures/go_sample/main.go"]}
    assert with_fixture_readers(namers, ["tests/test_named.py"], _read) == namers


def test_grep_narrowing_gives_the_same_readers() -> None:
    namers = {"package.json": ["tests/fixtures/ts_sample/a.ts"]}

    def holding(names):
        return {p for p, t in _TEXTS.items() if any(n in t for n in names)}

    tests = sorted(_TEXTS)
    assert with_fixture_readers(namers, tests, _read, holding) == with_fixture_readers(
        namers, tests, _read
    )
