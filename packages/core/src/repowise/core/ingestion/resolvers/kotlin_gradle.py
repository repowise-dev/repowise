"""Gradle multi-module / sourceSets index for Kotlin import resolution.

This module is a thin re-export facade over :mod:`.jvm_gradle`, which
provides the full Gradle index for both Java and Kotlin. All public
symbols and function signatures are preserved so existing callers
(e.g. ``kotlin.py``) continue to work unchanged.
"""

from __future__ import annotations

from .jvm_gradle import (
    KotlinProjectIndex,
    build_kotlin_index,
    get_or_build_kotlin_index,
    resolve_via_kotlin_index,
)

__all__ = [
    "KotlinProjectIndex",
    "build_kotlin_index",
    "get_or_build_kotlin_index",
    "resolve_via_kotlin_index",
]
