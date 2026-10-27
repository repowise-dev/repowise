"""Which modules a JavaScript or TypeScript test swaps for a closed factory's stand-in."""

from __future__ import annotations

import pytest

from repowise.core.analysis.test_reach import replaced_files
from repowise.core.analysis.test_reach.mocks_js import replaced_specifiers

_T = "src/x.test.ts"
_VI = 'import { vi } from "vitest"\n'


@pytest.mark.parametrize(
    "source",
    [
        _VI + 'vi.mock("./a", () => ({ run: vi.fn(), n: 1, s: "x", l: [null, undefined], o: { b: true } }))\n',
        'import { vi as v } from "vitest"\nv.mock("./a", async () => { return { default: v.fn(() => 1) } })\n',
        'import * as vt from "vitest"\nvt.vi.mock("./a", () => ({ a: vt.vi.fn() }))\n',
        'import { jest } from "@jest/globals"\njest.mock("./a", () => ({ a: jest.fn() }))\n',
        # ``jest`` is also the runner's global, when the file binds no other.
        'jest.mock("./a", function () { return { a: jest.fn(), C: () => null } })\n',
    ],
)
def test_a_closed_factory_swaps_the_module(source: str) -> None:
    assert replaced_specifiers(_T, source) == {"./a"}


@pytest.mark.parametrize(
    "source",
    [
        # Automock, ``doMock``, and a mock the file undoes.
        _VI + 'vi.mock("./a")\n',
        _VI + 'vi.doMock("./a", () => ({}))\n',
        _VI + 'vi.mock("./a", () => ({}))\nvi.unmock("./a")\n',
        'jest.mock("./a", () => ({}))\njest.doUnmock("./a")\n',
        # A parameter can receive the original module.
        _VI + 'vi.mock("./a", (importOriginal) => ({ a: vi.fn() }))\n',
        _VI + 'vi.mock("./a", async (orig) => ({ ...(await orig()), a: vi.fn() }))\n',
        # The original, in any form.
        _VI + 'vi.mock("./a", async () => ({ ...(await vi.importActual("./a")), b: vi.fn() }))\n',
        'jest.mock("./a", () => { const real = jest.requireActual("./a"); return { ...real } })\n',
        # Any other call, a free identifier, a dynamic import, a ``new``.
        _VI + 'vi.mock("./a", () => ({ a: vi.fn().mockReturnValue(1) }))\n',
        _VI + 'const m = vi.fn()\nvi.mock("./a", () => ({ a: m }))\n',
        _VI + 'vi.mock("./a", () => ({ m }))\n',
        _VI + 'vi.mock("./a", () => import("./b"))\n',
        _VI + 'vi.mock("./a", () => ({ a: new Map() }))\n',
        _VI + 'vi.mock("./a", () => ({ [key]: 1 }))\n',
        # A receiver not proven to be the runner's.
        'vi.mock("./a", () => ({ a: vi.fn() }))\n',
        'const jest = require("x")\njest.mock("./a", () => ({}))\n',
        'import jest from "jest-mock"\njest.mock("./a", () => ({}))\n',
        'const { jest } = require("./shim")\njest.mock("./a", () => ({}))\n',
        'function jest() {}\njest.mock("./a", () => ({}))\n',
        'try {} catch (jest) {}\njest.mock("./a", () => ({}))\n',
        'const f = jest => 1\njest.mock("./a", () => ({}))\n',
        _VI + 'setup(vi)\nvi.mock("./a", () => ({}))\n',
        'import * as vt from "vitest"\nvt.jest.mock("./a", () => ({}))\n',
        # Any unmock may name the same module by another specifier.
        _VI + 'vi.mock("./a", () => ({}))\nvi.unmock("./a.ts")\n',
        # A specifier built at run time, or a third argument.
        _VI + 'vi.mock(`./${name}`, () => ({}))\n',
        _VI + 'vi.mock(path, () => ({}))\n',
        'jest.mock("./a", () => ({}), { virtual: true })\n',
    ],
)
def test_anything_unproven_swaps_nothing(source: str) -> None:
    assert replaced_specifiers(_T, source) == frozenset()


def test_a_dynamic_unmock_abstains_for_the_whole_file() -> None:
    source = _VI + 'vi.mock("./a", () => ({}))\nvi.unmock(name)\n'
    assert replaced_specifiers(_T, source) == frozenset()


def test_only_a_relative_specifier_resolving_to_a_repo_file_masks() -> None:
    files = {"src/a.ts", "lib/b/index.ts", "src/c.js", _T, "src/y.test.js"}
    texts = {
        _T: _VI + 'vi.mock("./a", () => ({}))\nvi.mock("lodash", () => ({}))\n',
        "src/y.test.js": (
            'jest.mock("../lib/b", () => ({}))\njest.mock("./c", () => ({}))\n'
            'jest.mock("./missing", () => ({}))\njest.mock("@app/c", () => ({}))\n'
        ),
    }
    assert replaced_files(texts, files) == {
        _T: frozenset({"src/a.ts"}),
        "src/y.test.js": frozenset({"lib/b/index.ts", "src/c.js"}),
    }
