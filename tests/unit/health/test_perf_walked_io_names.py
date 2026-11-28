"""The later perf passes read the import map the complexity walk already collected."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.dataflow.parsing import parse_source
from repowise.core.analysis.health.perf.io_boundaries import collect_io_names, walked_io_names

SOURCES = [
    ("a.py", "python", b"import requests\nfrom os import path as p\nimport json\n\ndef f(xs):\n"
                       b"    import httpx\n    for x in xs:\n        requests.get(x)\n"),
    ("a.ts", "typescript", b"import fs from 'fs';\nimport axios from 'axios';\n"
                           b"export function f(xs: string[]) { for (const x of xs) { fs.readFileSync(x); } }\n"),
    ("a.go", "go", b"package a\n\nimport (\n\t\"net/http\"\n\t\"os\"\n)\n\n"
                   b"func F(xs []string) {\n\tfor _, x := range xs {\n\t\thttp.Get(x)\n\t\tos.Open(x)\n\t}\n}\n"),
    ("a.rs", "rust", b"use std::{fs, collections::HashMap};\nuse reqwest::Client;\nfn f() {}\n"),
    ("A.java", "java", b"import java.io.File;\nimport java.net.http.HttpClient;\n"
                       b"class A { void f() { new File(\"x\"); } }\n"),
]


@pytest.mark.parametrize(("path", "language", "source"), SOURCES)
def test_the_walked_map_is_the_one_a_fresh_walk_collects(path, language, source) -> None:
    fcx = walk_file(path, language, source)
    parsed = parse_source(path, language, source)
    assert parsed is not None

    walked = walked_io_names(fcx)

    assert walked is not None and walked == collect_io_names(parsed[0], language)


def test_a_file_the_walk_found_no_functions_in_collects_its_own() -> None:
    fcx = walk_file("a.py", "python", b"import requests\n")

    assert walked_io_names(fcx) is None


def test_every_perf_dialect_runs_the_walks_perf_pass() -> None:
    # A dialect whose language map has no call kinds would skip the walk's perf
    # pass and leave an empty map that the passes would read as "no I/O imports".
    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.complexity.perf_walk import perf_pass_runs
    from repowise.core.analysis.health.perf.dialects import PERF_DIALECTS

    assert all(perf_pass_runs(lang, get_language_map(lang)) for lang in PERF_DIALECTS)
