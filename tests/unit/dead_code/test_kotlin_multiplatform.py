"""Kotlin multiplatform ``expect`` / ``actual`` declarations in dead code.

An ``actual`` is the platform body of an ``expect``: it is never reported, and
neither is a file declaring one at top level. An ``expect`` named by a file
other than its own and its actuals' falls below the review floor; named only
by those, it stays at the review tier. Other languages and plain Kotlin
declarations are untouched.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code.kotlin_multiplatform import (
    EXPECT_USED_CONFIDENCE,
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
    assert expect.confidence == EXPECT_USED_CONFIDENCE < RISK_CAP_CONFIDENCE
    assert _CALLER in expect.evidence[-1]


def test_an_unused_pair_is_reported_once_as_its_expect():
    expect = _finding(_COMMON, "unwrapCause", 3, confidence=1.0)
    actuals = [_finding(_JVM, "unwrapCause", 4), _finding(_NATIVE, "unwrapCause", 3)]
    assert _settle([expect, *actuals], _source()) == [expect]
    assert expect.confidence == RISK_CAP_CONFIDENCE
    assert "no use outside its 2 platform actual(s)" in expect.reason


def test_an_expect_with_no_actual_and_no_use_stays_at_the_review_tier():
    expect = _finding(_COMMON, "unwrapCause", 3, confidence=1.0)
    assert _settle([expect], {_COMMON: _EXPECT_SRC}) == [expect]
    assert expect.confidence == RISK_CAP_CONFIDENCE
    assert "no actual and no use" in expect.reason


def test_an_actual_with_no_expect_in_view_is_left_alone():
    actual = _finding(_JVM, "unwrapCause", 4)
    unreachable = _finding(_JVM, None, None)
    assert _settle([actual, unreachable], {_JVM: _JVM_SRC}) == [actual, unreachable]


def test_the_same_name_in_another_package_a_comment_or_a_string_is_no_use():
    other = 'package io.other\nfun unwrapCause() = 1\nval s = "unwrapCause"\n'
    comment = 'package io.x\n// we unwrapCause here later\nval t = "unwrapCause"\n'
    expect = _finding(_COMMON, "unwrapCause", 3, confidence=1.0)
    _settle([expect], _source(**{"lib/other/Other.kt": other, _CALLER: comment}))
    assert expect.confidence == RISK_CAP_CONFIDENCE


def test_an_import_of_the_name_or_the_package_is_a_use():
    for imported in ("io.x.unwrapCause", "io.x.*"):
        user = f"package io.app\nimport {imported}\nval c = e.unwrapCause()\n"
        expect = _finding(_COMMON, "unwrapCause", 3, confidence=1.0)
        _settle([expect], _source(**{"app/User.kt": user}))
        assert expect.confidence == EXPECT_USED_CONFIDENCE, imported


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
