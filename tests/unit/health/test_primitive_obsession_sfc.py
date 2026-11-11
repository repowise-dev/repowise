"""primitive_obsession in a Vue or Svelte component's ``<script>``.

An untyped ``<script>`` is JavaScript, so a wholly untyped signature there is
held to the two extra parameters a ``.js`` file gets.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.primitive_obsession import (
    PrimitiveObsessionDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity, walk_file_complexity

_FILLER = FunctionComplexity("filler", 900, 990, ccn=1, max_nesting=0, cognitive=0, nloc=70)

_SCRIPT = """
function handle(req, res, params, context, query) {}
function draw(a, b, c, d, e, f, g) {}
"""
_COMPONENTS = {
    "vue": f'<template><div /></template>\n<script>{_SCRIPT}</script>\n',
    "svelte": f'<script>{_SCRIPT}</script>\n<div />\n',
}


@pytest.mark.parametrize("language", ["vue", "svelte"])
def test_untyped_component_script_is_held_like_javascript(language: str) -> None:
    path = f"src/components/Panel.{language}"
    fns = walk_file_complexity(path, language, _COMPONENTS[language].encode())
    ctx = FileContext(
        file_path=path, language=language, nloc=80, has_test_file=False, module=None,
        is_test=False, all_functions=(*fns, _FILLER),
    )
    assert {f.function_name for f in PrimitiveObsessionDetector().detect(ctx)} == {"draw"}
