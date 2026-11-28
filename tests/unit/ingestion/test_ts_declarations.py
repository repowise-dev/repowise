"""Which file names are TypeScript declaration files."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.ts_declarations import declaration_stem, is_declaration_file


@pytest.mark.parametrize("path", ["a/x.d.ts", "a/x.d.mts", "a/x.d.cts", "A/X.D.TS"])
def test_declaration_files(path):
    assert is_declaration_file(path)


@pytest.mark.parametrize("path", ["a/x.ts", "a/x.mts", "a/x.cts", "a/xd.ts", "a/x.d.tsx"])
def test_not_declaration_files(path):
    assert not is_declaration_file(path)
    assert declaration_stem(path) is None


def test_declaration_stem_keeps_the_case_of_the_path():
    assert declaration_stem("lib/Runtime.d.mts") == "lib/Runtime"
