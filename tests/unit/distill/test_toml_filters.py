"""CI validation for the data-driven TOML filters.

There is no build step in this project, so the inline ``[[tests.<name>]]``
cases each ``filters_toml/*.toml`` ships are validated here instead. For every
built-in filter definition this module:

- runs its inline cases (``input`` -> ``expected``),
- asserts zero error-line loss on every case,
- asserts a per-file median savings floor (``[meta] savings_floor``),

so a data-authored filter is held to the same bar as the hand-written ones.
It also pins the errors-first invariant (a hostile definition cannot drop an
error line) and guards against routing regressions as new filters are added.
"""

from __future__ import annotations

import statistics
import tomllib
from pathlib import Path

import pytest

from repowise.core.distill.budget import estimate_tokens, savings_pct
from repowise.core.distill.filters.base import is_error_line
from repowise.core.distill.registry import filter_registry
from repowise.core.distill.router import select_filter
from repowise.core.distill.toml_filter import _BUILTIN_DIR, TomlFilter, parse_toml_filters

# Default floor when a file omits ``[meta] savings_floor``.
_DEFAULT_SAVINGS_FLOOR = 60.0


def _toml_files() -> list[Path]:
    return sorted(_BUILTIN_DIR.glob("*.toml"))


def _load(path: Path) -> tuple[dict[str, TomlFilter], dict, list[tuple[TomlFilter, dict]]]:
    """Return ``(filters_by_name, meta, [(filter, case), ...])`` for one file."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    filters = {f.name: f for f in parse_toml_filters(path)}
    meta = data.get("meta") or {}
    cases: list[tuple[TomlFilter, dict]] = []
    for name, entries in (data.get("tests") or {}).items():
        assert name in filters, f"{path.name}: tests for unknown filter {name!r}"
        cases.extend((filters[name], case) for case in entries)
    return filters, meta, cases


def _all_cases() -> list[tuple[str, TomlFilter, dict]]:
    out: list[tuple[str, TomlFilter, dict]] = []
    for path in _toml_files():
        _filters, _meta, cases = _load(path)
        out.extend((path.name, f, case) for f, case in cases)
    return out


def _case_id(param: tuple[str, TomlFilter, dict]) -> str:
    file, _f, case = param
    return f"{file}::{case['name']}"


_CASES = _all_cases()


@pytest.mark.parametrize("param", _CASES, ids=[_case_id(p) for p in _CASES])
def test_inline_case_matches_expected(param: tuple[str, TomlFilter, dict]) -> None:
    _file, f, case = param
    got = f.distill(case["input"].strip("\n"))
    assert got.strip() == case["expected"].strip()


@pytest.mark.parametrize("param", _CASES, ids=[_case_id(p) for p in _CASES])
def test_inline_case_preserves_error_lines(param: tuple[str, TomlFilter, dict]) -> None:
    _file, f, case = param
    raw = case["input"].strip("\n")
    got = f.distill(raw)
    for line in raw.splitlines():
        if is_error_line(line):
            assert line in got, f"error line dropped: {line!r}"


@pytest.mark.parametrize("path", _toml_files(), ids=lambda p: p.name)
def test_per_file_savings_floor(path: Path) -> None:
    _filters, meta, cases = _load(path)
    assert cases, f"{path.name}: no inline tests declared"
    floor = float(meta.get("savings_floor", _DEFAULT_SAVINGS_FLOOR))
    pcts = [
        savings_pct(
            estimate_tokens(raw := case["input"].strip("\n")), estimate_tokens(f.distill(raw))
        )
        for f, case in cases
    ]
    median = statistics.median(pcts)
    assert median >= floor, f"{path.name}: median savings {median:.1f}% < floor {floor:.0f}%"


def test_hostile_filter_cannot_drop_error_line() -> None:
    """A definition that tries to strip everything still cannot lose an error."""
    hostile = TomlFilter(
        {
            "name": "hostile",
            "strip_lines_matching": [".*"],  # strip every line
            "match_output": [{"pattern": ".*", "message": "all clean"}],  # and short-circuit
            "on_empty": "nothing here",
        }
    )
    out = hostile.distill("ok line 1\nERROR: could not build wheel for numpy\nok line 2")
    assert "ERROR: could not build wheel for numpy" in out
    assert out != "all clean"  # short-circuit is gated on error-free output


def test_builtin_filters_registered() -> None:
    names = {f.name for f in filter_registry.filters()}
    assert {"install_output", "infra_plan"} <= names


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # new data filters route
        ("uv pip install requests", "install_output"),
        ("pip install flask", "install_output"),
        ("npm ci", "install_output"),
        ("poetry install", "install_output"),
        ("terraform plan", "infra_plan"),
        ("tofu plan -out plan.bin", "infra_plan"),
        # existing families must not regress with the data filters loaded
        ("pytest -x", "test_output"),
        ("ruff check .", "lint_output"),
        ("npm run build", "build_output"),
        ("git status", "git_status"),
        ("git log --oneline -5", "git_log"),
        ("rg TODO src", "search_results"),
    ],
)
def test_routing_no_regression(command: str, expected: str) -> None:
    chosen = select_filter(command, "")
    assert chosen is not None and chosen.name == expected
