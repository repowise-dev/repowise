"""Receiver capture for PHP and Luau, whose queries recorded none at all.

Before this, `php.scm` and `luau.scm` carried no `@call.receiver`, so every
call arrived receiver-less and was resolved by bare name against a flat
per-file index — `$request->withHeader()` binding to whichever class in the
repo happened to declare `withHeader`.

The PHP half is a normalisation test as much as a capture test. PHP spells the
implicit receiver `$this` and aliases it as `self::` / `static::`, while the
resolver's self/this strategy tests `in ("self", "this")`. Capture without
normalisation therefore turns the whole implicit-receiver population — the
largest single group of correct PHP edges — into misses.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _parse(tmp_path: Path, rel: str, lang: str, content: str) -> dict[str, ParsedFile]:
    abs_ = tmp_path / rel
    abs_.parent.mkdir(parents=True, exist_ok=True)
    abs_.write_text(content)
    fi = FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return {rel: parse_file(fi, content.encode("utf-8"))}


PHP_SRC = """<?php
class A {
    function caller() {
        $this->one();
        self::two();
        static::three();
        parent::four();
        $obj->five();
        B::six();
        seven();
    }
    function one() {}
    function two() {}
    function three() {}
}
"""

LUAU_SRC = """local M = {}
function M.new() return setmetatable({}, M) end
function M:run()
    self:helper()
    M.new()
    obj.other(1)
    plain(2)
end
function M:helper() end
return M
"""


@pytest.mark.parametrize(
    ("target", "receiver"),
    [
        ("one", "this"),  # $this-> — the sigil must be stripped
        ("two", "this"),  # self:: is same-class dispatch
        ("three", "this"),  # static:: likewise
        ("four", "parent"),  # needs the heritage walk; left to miss, not guessed
        ("five", "obj"),  # ordinary variable, sigil stripped
        ("six", "B"),  # explicit class receiver
        ("seven", None),  # a genuine free call keeps no receiver
    ],
)
def test_php_receiver_is_captured_and_normalised(
    tmp_path: Path, target: str, receiver: str | None
) -> None:
    parsed = _parse(tmp_path, "src/A.php", "php", PHP_SRC)
    got = {c.receiver_name for c in parsed["src/A.php"].calls if c.target_name == target}
    assert got == {receiver}, f"{target}() should carry receiver {receiver!r}, got {got}"


def test_php_this_call_resolves_against_the_callers_own_class(tmp_path: Path) -> None:
    """The normalisation's whole purpose: `$this->one()` must reach strategy 3."""
    parsed = _parse(tmp_path, "src/A.php", "php", PHP_SRC)
    resolver = CallResolver(parsed, {}, repo_path=str(tmp_path))
    edges = [
        (rc.callee_id, rc.confidence, rc.origin)
        for rc in resolver.resolve_file("src/A.php", parsed["src/A.php"].calls)
    ]
    hits = [e for e in edges if e[0].endswith("::A::one")]
    assert hits, f"$this->one() must resolve to A::one; edges: {edges}"
    assert hits[0][1:] == (0.95, "self_scope")


@pytest.mark.parametrize(
    ("target", "receiver"),
    [
        ("helper", "self"),  # obj:method() — Luau spells self plainly
        ("new", "M"),
        ("other", "obj"),  # obj.method()
        ("plain", None),
    ],
)
def test_luau_receiver_is_captured(tmp_path: Path, target: str, receiver: str | None) -> None:
    parsed = _parse(tmp_path, "src/m.luau", "luau", LUAU_SRC)
    got = {c.receiver_name for c in parsed["src/m.luau"].calls if c.target_name == target}
    assert got == {receiver}, f"{target}() should carry receiver {receiver!r}, got {got}"


def test_luau_capture_does_not_duplicate_a_call_site(tmp_path: Path) -> None:
    """The new patterns must not double up with the bare-call pattern.

    A grammar that matches one call twice — once with a receiver, once without —
    resolves the receiver-less copy by bare name and mints a wrong edge beside
    the right one.
    """
    parsed = _parse(tmp_path, "src/m.luau", "luau", LUAU_SRC)
    sites = [(c.line, c.target_name) for c in parsed["src/m.luau"].calls]
    assert len(sites) == len(set(sites)), f"duplicate call sites: {sites}"
