"""``duplicated_assertion_block`` per language: real checks against flag checks.

Each case walks a real test file, pairs it with an identical partner, and asks
whether the copied block clears the five-real-check floor.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.asserts.lexicon import is_flag_check
from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.duplicated_assertion_block import (
    DuplicatedAssertionBlockDetector,
)
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.duplication import ClonePair

# language -> (path, template, five real checks (the last spread over lines), flag checks)
_CASES = {
    "java": (
        "src/test/java/FooTest.java",
        "class FooTest {{\n  @Test void t() {{\n{body}\n  }}\n}}\n",
        [
            "assertEquals(5, getCount());",
            "assertEquals(expected, compute());",
            'assertEquals("a", r.name());',
            "assertThat(r.size(), equalTo(2));",
            "assertThat(\n        r.first(),\n        equalTo(1));",
        ],
        [
            "assertNotNull(r);",
            "assertThat(r, notNullValue());",
            'assertTrue("ok", done);',
            "assertThat(r).isNotNull();",
        ],
    ),
    "csharp": (
        "tests/FooTests.cs",
        "class FooTests {{\n  [Fact] public void T() {{\n{body}\n  }}\n}}\n",
        [
            "Assert.Equal(5, r.Count);",
            'Assert.Equal("a", r.Name);',
            "Assert.Equal(2, r.Size());",
            'Assert.Contains("x", r.Tags);',
            "Assert.Equal(\n        1,\n        r.First());",
        ],
        [
            "Assert.IsNotNull(r);",
            "Assert.True(done);",
            "r.Should().NotBeNull();",
            "Assert.Null(err);",
        ],
    ),
    "typescript": (
        "tests/foo.test.ts",
        "it('t', () => {{\n{body}\n}});\n",
        [
            "expect(r.count).toBe(5);",
            "expect(r.name).toEqual('a');",
            "expect(r.size()).toBe(2);",
            "expect(r.tags).toContain('x');",
            "expect(\n        r.first(),\n    ).toBe(1);",
        ],
        [
            "expect(r).toBeDefined();",
            "expect(done).toBe(true);",
            "expect(err).not.toBeNull();",
            "assert(done);",
        ],
    ),
    "python": (
        "tests/test_foo.py",
        "def test_t():\n{body}\n",
        [
            "assert r.count == 5",
            "assert r.name == 'a'",
            "assert r.size() == 2",
            "assert 'x' in r.tags",
            "assert r.first() == (\n        1\n    )",
        ],
        ["assert r is not None", "assert done", "assert not err", "assert ok, 'message'"],
    ),
    "php": (
        "tests/FooTest.php",
        "<?php\nclass FooTest {{\n  public function testIt() {{\n{body}\n  }}\n}}\n",
        [
            "$this->assertEquals(5, $r->count);",
            "$this->assertEquals('a', $r->name);",
            "$this->assertEquals(2, $r->size());",
            "$this->assertContains('x', $r->tags);",
            "$this->assertEquals(\n        1,\n        $r->first());",
        ],
        [
            "$this->assertNotNull($r);",
            "$this->assertTrue($done);",
            "$this->assertNull($err);",
            "self::assertFalse($failed);",
        ],
    ),
}


def _detect(language: str, lines: list[str]):
    path, template, _, _ = _CASES[language]
    source = template.format(body="\n".join("    " + line for line in lines))
    text = source.splitlines()
    partner = path.replace("oo", "ee")
    clone = ClonePair(path, partner, 1, len(text), 1, len(text), token_count=80)
    return DuplicatedAssertionBlockDetector().detect(
        FileContext(
            file_path=path,
            language=language,
            nloc=200,
            has_test_file=False,
            module=None,
            all_functions=tuple(walk_file(path, language, source.encode()).functions),
            clones=[clone],
            clone_sources={path: text, partner: text},
        )
    )


@pytest.mark.parametrize("language", sorted(_CASES))
def test_five_real_checks_are_reported_four_are_not(language: str):
    _, _, real, flags = _CASES[language]
    assert len(_detect(language, real)) == 1
    assert _detect(language, real[:4]) == []
    # Flag checks pad the block without making it five real checks.
    assert _detect(language, [*flags, *real[:4]]) == []


@pytest.mark.parametrize(
    ("language", "line"),
    [
        ("java", "assertEquals(5, getCount());"),
        ("java", "assertEquals(expected, compute());"),
        ("java", "assertNull(x.getY());"),
        ("csharp", "Assert.Equal(expected, Compute());"),
        ("python", "assert x == compute()"),
        ("typescript", "expect(x).toBe(5);"),
        ("php", "$this->assertEquals(5, getCount());"),
        ("php", "$this->assertNull($x->getY());"),
    ],
)
def test_a_value_check_is_not_a_flag_check(language: str, line: str):
    assert not is_flag_check(language, line)


@pytest.mark.parametrize(
    ("language", "line"),
    [
        ("java", '  assertNull(err, "message");'),
        ("kotlin", "assertTrue(done)"),
        ("csharp", "Assert.IsFalse(failed);"),
        ("python", "self.assertIsNone(err)"),
        ("javascript", "assert.ok(done);"),
        ("rust", "assert!(ok);"),
        ("go", "require.NotNil(t, err)"),
        ("php", "$this->assertNotNull($x);"),
        ("php", "self::assertTrue($done);"),
        ("php", "static::assertNull($err, 'custom msg');"),
    ],
)
def test_flag_checks_per_language(language: str, line: str):
    assert is_flag_check(language, line)
