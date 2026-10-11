"""Which files code can move between: one language family."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.refactoring.language_family import same_language_family


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ui/App.tsx", "ui/querier.ts"),
        ("ui/App.vue", "ui/util.js"),
        ("src/net.c", "include/net.h"),
        ("src/view.mm", "src/model.cpp"),
        ("Pages/Index.razor", "Pages/Index.razor.cs"),
        ("pkg/a.py", "pkg/b.pyi"),
    ],
)
def test_one_build_mixes_these(a: str, b: str) -> None:
    assert same_language_family(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ios/NodeAppModel.swift", "android/OpenClawCanvasA2UIAction.kt"),
        ("pkg/a.py", "ui/c.ts"),
        # JVM interop is real, but a move across it is left to a person.
        ("src/A.java", "src/B.kt"),
        ("src/A.scala", "src/B.java"),
        # No extension: the whole name is the key, so two unknowns never pair.
        ("Makefile", "Dockerfile"),
    ],
)
def test_code_never_moves_across_these(a: str, b: str) -> None:
    assert not same_language_family(a, b)
