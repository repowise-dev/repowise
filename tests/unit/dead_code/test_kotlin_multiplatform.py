"""Kotlin multiplatform ``expect`` / ``actual`` declarations in dead code.

An ``actual`` is the platform body of an ``expect``: it is never reported, and
neither is a file declaring one at top level. An ``expect`` named by a file
other than its own and its actuals' falls below the review floor; named only
by those, it stays at the review tier. Other languages and plain Kotlin
declarations are untouched.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code.kotlin_multiplatform import (
    EXPECT_NAMED_ELSEWHERE_CONFIDENCE,
    settle_platform_declarations,
)
from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE

_COMMON = "lib/common/src/io/x/Utils.kt"
_JVM = "lib/jvm/src/io/x/UtilsJvm.kt"
_NATIVE = "lib/posix/src/io/x/UtilsNative.kt"
_CALLER = "lib/common/src/io/x/Caller.kt"

_EXPECT_SRC = """package io.x

public expect fun Throwable.unwrapCause(): Throwable

public fun plainHelper(): Int = 1
"""
_JVM_SRC = """package io.x

/** Doc. */
@Suppress("UNUSED")
public actual fun Throwable.unwrapCause(): Throwable = cause ?: this
"""
_NATIVE_SRC = """package io.x

public actual fun Throwable.unwrapCause(): Throwable = this
"""


def _finding(
    path: str, name: str | None, line: int | None, confidence: float = 0.4
) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=DeadCodeKind.UNUSED_EXPORT if name else DeadCodeKind.UNREACHABLE_FILE,
        file_path=path,
        symbol_name=name,
        symbol_kind="function" if name else None,
        confidence=confidence,
        reason="Public symbol has no importers",
        last_commit_at=None,
        commit_count_90d=0,
        lines=1,
        evidence=[],
        safe_to_delete=False,
        primary_owner=None,
        age_days=None,
        start_line=line,
        end_line=line,
    )


def _settle(findings, source):
    return settle_platform_declarations(findings, {p: s.encode() for p, s in source.items()})


def _source(**extra: str) -> dict[str, str]:
    return {_COMMON: _EXPECT_SRC, _JVM: _JVM_SRC, _NATIVE: _NATIVE_SRC, **extra}


def test_actual_declarations_are_not_reported():
    findings = [_finding(_JVM, "unwrapCause", 4), _finding(_NATIVE, "unwrapCause", 3)]
    assert _settle(findings, _source()) == []


def test_a_file_declaring_a_top_level_actual_is_not_unreachable():
    assert _settle([_finding(_NATIVE, None, None)], _source()) == []


def test_an_expect_used_elsewhere_drops_below_the_floor():
    expect = _finding(_COMMON, "unwrapCause", 3, confidence=1.0)
    kept = _settle([expect], _source(**{_CALLER: "package io.x\nval c = e.unwrapCause()\n"}))
    assert kept == [expect]
    assert expect.confidence == EXPECT_NAMED_ELSEWHERE_CONFIDENCE < RISK_CAP_CONFIDENCE
    assert _CALLER in expect.evidence[-1]


def test_an_expect_named_only_by_its_actuals_stays_at_the_review_tier():
    expect = _finding(_COMMON, "unwrapCause", 3, confidence=1.0)
    assert _settle([expect], _source()) == [expect]
    assert expect.confidence == RISK_CAP_CONFIDENCE
    assert "only by its own declaration and its platform actuals" in expect.reason


def test_plain_kotlin_and_other_languages_are_untouched():
    plain = _finding(_COMMON, "plainHelper", 5)
    java = _finding("src/Main.java", "actual", 1)
    unreachable = _finding(_CALLER, None, None)
    source = _source(**{"src/Main.java": "public actual fun x() {}\n", _CALLER: "package io.x\n"})
    kept = _settle([plain, java, unreachable], source)
    assert kept == [plain, java, unreachable]
    assert plain.reason == "Public symbol has no importers"


def test_the_word_actual_in_a_string_or_member_is_not_a_modifier():
    source = {
        _CALLER: (
            'package io.x\n@Deprecated("use the actual one")\npublic fun legacy() = 1\n'
            "class Box {\n    actual fun inner() = 2\n}\n"
        )
    }
    legacy = _finding(_CALLER, "legacy", 2)
    unreachable = _finding(_CALLER, None, None)
    assert _settle([legacy, unreachable], source) == [legacy, unreachable]
