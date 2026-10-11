"""Tests that file_namers returns deterministic namer lists.

Regression for #3297: the sort must be stable with respect to
PYTHONHASHSEED *and* must keep any doctest-glob configuration entry
at index 0, because doc_readers() always picks names[0].
"""

from repowise.core.analysis.test_selection import file_namers


def test_namers_are_sorted_deterministically_regardless_of_source_order() -> None:
    """Same files named by the same sources produce the same namer list."""
    docs = ["docs/guide.md"]
    sources_a = [
        ("pyproject.toml", '[tool.pytest]\ndoctest-glob = "*.md"\n'),
        ("src/alpha.py", 'GUIDE = "guide.md"\n'),
        ("src/beta.py", 'GUIDE = "guide.md"\n'),
    ]
    sources_b = [
        ("pyproject.toml", '[tool.pytest]\ndoctest-glob = "*.md"\n'),
        ("src/beta.py", 'GUIDE = "guide.md"\n'),
        ("src/alpha.py", 'GUIDE = "guide.md"\n'),
    ]
    result_a = file_namers(docs, sources_a)["docs/guide.md"]
    result_b = file_namers(docs, sources_b)["docs/guide.md"]
    assert result_a == result_b, (
        f"Namer order should be identical regardless of source order; "
        f"got {result_a!r} vs {result_b!r}"
    )


def test_config_entry_stays_first_when_other_namers_sort_before_it() -> None:
    """A doctest-glob config must remain at index 0 even if code paths sort earlier."""
    # "aaa/conftest.py" sorts before "zzz/pyproject.toml" alphabetically,
    # but the config that declares doctest-glob must stay at index 0.
    docs = ["README.md"]
    sources = [
        ("zzz/pyproject.toml", '[tool.pytest]\ndoctest-glob = "*.md"\n'),
        ("aaa/conftest.py", 'README = "README.md"\n'),
    ]
    namers = file_namers(docs, sources)["README.md"]
    assert namers[0] == "zzz/pyproject.toml", (
        f"Config entry must be first; got {namers!r}"
    )
    assert "aaa/conftest.py" in namers


def test_case_insensitive_matching() -> None:
    """A code file referencing 'readme.md' in lowercase must name 'README.md'."""
    files = ["README.md"]
    sources = [
        ("src/app.py", 'path = "readme.md"\n'),
    ]
    result = file_namers(files, sources)
    assert "README.md" in result, "Case-insensitive match should find README.md"
    assert "src/app.py" in result["README.md"]


def test_no_config_namers_are_sorted_whatever_the_source_order() -> None:
    """#3297's repro: package.json named only by code, no doctest-glob config.

    The first namer is the one a scope quotes, so it must not follow the
    order the sources arrive in.
    """
    files = ["package.json"]
    sources = [
        ("tests/test_pkg.py", 'Path("package.json")'),
        ("tests/helpers.py", 'json.load(open("package.json"))'),
        ("scripts/name.py", 'json.load(open("package.json"))'),
    ]
    expected = ["scripts/name.py", "tests/helpers.py", "tests/test_pkg.py"]
    assert file_namers(files, sources)["package.json"] == expected
    assert file_namers(files, reversed(sources))["package.json"] == expected
