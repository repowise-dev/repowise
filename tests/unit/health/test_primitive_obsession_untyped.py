"""primitive_obsession where a signature carries no types.

PHP declares types, so its signatures are judged by how many are scalars.
JavaScript cannot, and its wide signatures mostly pass objects, so a wholly
untyped one is held to two more parameters. Ruby keeps the plain threshold.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.primitive_obsession import (
    PrimitiveObsessionDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity, walk_file_complexity

_FILLER = FunctionComplexity("filler", 900, 990, ccn=1, max_nesting=0, cognitive=0, nloc=70)


def _flagged(path: str, language: str, source: str) -> set[str]:
    fns = walk_file_complexity(path, language, source.encode())
    ctx = FileContext(
        file_path=path, language=language, nloc=80, has_test_file=False, module=None,
        is_test=False, all_functions=(*fns, _FILLER),
    )
    return {f.function_name for f in PrimitiveObsessionDetector().detect(ctx)}


def test_php_reads_its_declared_types() -> None:
    source = (
        "<?php\n"
        "function services(IOInterface $io, Config $config, Downloader $dl, string $dir, ?Cache $cache) {}\n"
        "function raw(bool $a, ?string $b, int|string $c, float $d, $e) {}\n"
        "function shout(String $a, String $b, INT $c, Bool $d, Config $e) {}\n"
    )
    assert _flagged("src/a.php", "php", source) == {"raw", "shout"}


@pytest.mark.parametrize(
    ("params", "flagged"),
    [("req, res, params, context, query", False), ("a, b, c, d, e, f, g", True)],
)
def test_javascript_untyped_signature_gets_two_more(params: str, flagged: bool) -> None:
    source = f"function handle({params}) {{}}\n"
    assert bool(_flagged("lib/route.js", "javascript", source)) is flagged


def test_ruby_keeps_the_plain_threshold() -> None:
    source = "def on_mime_head(mime_index, head, filename, content_type, name)\nend\n"
    assert _flagged("lib/parser.rb", "ruby", source) == {"on_mime_head"}
