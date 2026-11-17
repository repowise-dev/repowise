"""Constants, rank limits and item parts every Fix-first unit shares."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.health.refactoring.recipe import _strs
from repowise.core.analysis.health.rows import field
from repowise.core.analysis.health.worth import WORTH_MAGNITUDE

from . import text
from .model import FixItem, FixRisk, FixTest, FixVerify

Rows = Iterable[Any]

#: Measured size (CCN, lines or nesting alone, before the severity floor and
#: the hot-file bonus) from which an item leads as "break up", naming the whole
#: problem: CCN 40, 200 lines or nesting 6.
SIZE_BREAK_UP = WORTH_MAGNITUDE
#: A file in the top fifth of production files by dependents is central (one
#: value step); by churn it changes often (orders within a band only).
HOT_QUANTILE = 0.8
MAX_FACTS = 5
MAX_TESTS = 5
MAX_STEPS = 5
MAX_CONTEXT = 3
DEFAULT_LIMIT = 10
#: Class-level findings: a fix names member groups, which only a plan holds.
CLASS_MARKERS = frozenset({"low_cohesion", "god_class"})
#: Refactorings that change one function, whose own history orders them.
_FUNCTION_LEADS = frozenset({"extract_method", "move_method"})


@dataclass(frozen=True, slots=True)
class _Unit:
    """What ranking reads, and how to write the item once it is shown.

    Items are written only for the ranks a caller keeps: the copy, facts and
    verify block cost more than the ranking, and most units are never shown.
    """

    id: str
    kind: str
    tier: str
    value: int
    worth: float
    confidence: str
    effort: str
    improves: str
    write: Callable[[int], FixItem]
    #: False for a performance cause whose marker has not cleared the bar for
    #: leading (``opportunity_rank.may_lead``): listed, never first.
    may_lead: bool = True


def _tail(symbol: str | None) -> str | None:
    """A symbol's own name: ``a.py::Cls.run`` and ``Cls::run`` read ``run``."""
    return re.split(r"::|\.", symbol)[-1] if symbol else None


def _about(function: str | None, symbol: str | None) -> bool:
    """Whether a history row on ``function`` speaks to an item on ``symbol``:
    a file-level row or item, the same function, or a member of the item's
    class. ``Engine.run`` never keeps ``Other.run``; a bare name on either
    side matches by name, since it names no class to tell them apart."""
    if not function or not symbol:
        return True
    own = text.short_symbol(symbol) or ""
    if function == own or function.startswith(f"{own}."):
        return True
    return ("." not in function or "." not in own) and _tail(function) == _tail(own)


def symbol_key(path: str, symbol: str | None) -> str | None:
    """A function's ``git_function_blame`` key, ``path::name``; ``None`` for no symbol."""
    name = text.short_symbol(symbol)
    return f"{path}::{name}" if name else None


def _open(row: Any) -> bool:
    return (field(row, "status") or "open") == "open"


def _num(value: Any) -> float:
    return float(value or 0.0)


def _one_of(value: Any, known: Iterable[Any], default: str) -> Any:
    """*value* when it is one of *known*, else *default*."""
    return value if value in known else default


def hot_cut_offset(count: int) -> int:
    """Where the top fifth starts in ``count`` ascending values."""
    return min(int(count * HOT_QUANTILE), count - 1)


def hot_cut(value_at_offset: float | None) -> float:
    """The value at :func:`hot_cut_offset`, never below 1 (zero is not hot)."""
    return math.inf if value_at_offset is None else max(value_at_offset, 1)


def _quantile_cut(values: list[int]) -> float:
    """The smallest value in the top fifth of production files."""
    if not values:
        return math.inf
    return hot_cut(sorted(values)[hot_cut_offset(len(values))])


def _risk(files_touched: int, dependents: int | None, *, function: bool = False) -> FixRisk:
    deps = dependents or 0
    if files_touched >= 5 or deps >= 20:
        level = "high"
    elif files_touched >= 2 or deps >= 5:
        level = "medium"
    else:
        level = "low"
    parts = [f"touches {text.plural(files_touched, 'file')}"]
    if dependents:
        parts.append(text.imports_it(dependents, function=function))
    return FixRisk(level, dependents, files_touched, "; ".join(parts).capitalize() + ".")


#: Validation profile for a finding with no stored plan: (path, function, line start, line end).
Validate = Callable[[str, Any, Any, Any], Mapping[str, Any] | None]


def _verify(profile: Mapping[str, Any] | None) -> FixVerify:
    """The stored validation profile, shown short. Never recomputed here."""
    if not profile:
        return FixVerify((), 0, None, "unknown")
    via = profile.get("via")
    how = text.TEST_VIA.get(via or "")
    reason = f"reaches the changed code {how}" if how else "covers it"
    tests = tuple(FixTest(str(t), reason) for t in (profile.get("tests") or [])[:MAX_TESTS])
    commands = profile.get("commands") or []
    basis = profile.get("basis")
    return FixVerify(
        tests,
        int(profile.get("total") or len(tests)),
        commands[0] if commands else None,
        basis if basis in ("measured", "inferred") else "unknown",
        None if tests else profile.get("prerequisite"),
    )


def _step_command(check: Mapping[str, Any] | None, item_command: str | None) -> str | None:
    """A step's own command (a validation profile, or a plan step's ``verify``),
    kept only when it differs from the item's."""
    commands = (check or {}).get("commands") or []
    return commands[0] if commands and commands[0] != item_command else None


def shared_object(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The parameter object a staged plan's first stage takes, None when its
    helper takes plain values only."""
    obj = body.get("parameter_object")
    params = body.get("params")
    if not isinstance(obj, dict) or not isinstance(params, list):
        return None
    var = obj.get("var")
    return obj if isinstance(var, str) and var in params else None


def object_code(obj: Mapping[str, Any]) -> tuple[str, str] | None:
    """The object's declaration and the line building it, None when the plan
    lacks either."""
    declaration, construct = _text(obj.get("declaration_text")), _text(obj.get("construct_text"))
    return (declaration, construct) if declaration and construct else None


def _helper_code(body: Mapping[str, Any]) -> dict[str, Any]:
    """An Extract Method plan's rendered helper header and call, when it wrote
    them, and the plan's notes on what that code cannot say (a type a guard
    narrows, an import to repeat). A helper taking the shared parameter object
    comes after the object's declaration, and its call after the line
    building it: the call names that variable."""
    symbol = body.get("new_symbol")
    symbol = symbol if isinstance(symbol, dict) else {}
    site = body.get("call_site")
    signature = _text(symbol.get("signature_text"))
    call = _text(site.get("new_text")) if isinstance(site, dict) else None
    notes = _strs(symbol.get("notes"))
    obj = shared_object(body)
    code = object_code(obj) if obj is not None else None
    if obj is not None and code is not None:
        signature = "\n\n".join(t for t in (code[0], signature) if t)
        call = "\n".join(t for t in (code[1], call) if t)
        first = (
            f"Add the {obj.get('name')} declaration above the helper, and the line building "
            f"{obj.get('var')} just before the call, as shown."
        )
        notes = [first, *_strs(obj.get("notes")), *notes]
    return {"signature": signature, "call": call, "notes": tuple(dict.fromkeys(notes))}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
