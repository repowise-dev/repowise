"""One way to read a setting: its file value, its env var, or its default.

Pure: the caller passes the loaded mapping of the setting's file and,
optionally, the environment. Layers are tried in precedence order (file then
env, or env then file when :attr:`Env.wins`), then the default. A value that
fails the setting's type, bounds, choices or parser is reported in
:attr:`Resolved.problem` and handled by :attr:`Setting.on_invalid`: skipped
for the next layer, raised, or read as :attr:`Setting.fallback`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from importlib import import_module
from typing import Any

from repowise.core.settings import registry
from repowise.core.settings.model import Resolved, Setting, SettingError, Source

__all__ = ["MISSING", "lookup", "resolve", "validate"]


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


#: A key that is absent, as distinct from one set to ``None``.
MISSING: Any = _Missing()

_FILE_SOURCE = {"repo": Source.REPO_FILE, "user": Source.USER_FILE}


def lookup(config: Mapping[str, Any] | None, key: str) -> Any:
    """The value at dotted *key* in *config*, or :data:`MISSING`.

    A bare ``key:`` (null) counts as absent, as it does for the file settings
    the web UI writes.
    """
    node: Any = config
    for part in key.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return MISSING
        node = node[part]
    return MISSING if node is None else node


def resolve(
    setting: Setting | str,
    config: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Resolved:
    """The effective value of *setting* given its file's *config* mapping.

    *environ* defaults to ``os.environ``. Raises :class:`SettingError` only for
    a setting declared ``on_invalid="error"``.
    """
    s = registry.get(setting) if isinstance(setting, str) else setting
    area_resolve = registry.area_resolver(s.area)
    if area_resolve is not None:
        # The registry refuses an env-backed setting in such an area, so the
        # file is the only layer the area has to weigh.
        return area_resolve(s, config or {})

    layers = _layers(s, config, os.environ if environ is None else environ)
    problems: list[str] = []
    for source, raw in layers:
        try:
            value = validate(s, raw, from_env=source is Source.ENV)
        except ValueError as exc:
            problem = f"{s.key}: {exc}"
            if s.on_invalid == "error":
                raise SettingError(problem) from None
            if s.on_invalid == "fallback":
                return Resolved(s.fallback, source, problem)
            problems.append(problem)
            continue
        return Resolved(value, source, "; ".join(problems) or None)
    return Resolved(s.default, Source.DEFAULT, "; ".join(problems) or None)


def _layers(
    s: Setting, config: Mapping[str, Any] | None, environ: Mapping[str, str]
) -> list[tuple[Source, Any]]:
    layers: list[tuple[Source, Any]] = []
    if s.scope != "env":
        raw = lookup(config, s.key)
        if raw is not MISSING:
            layers.append((_FILE_SOURCE[s.scope], raw))
    if s.env is not None and not s.env.prefix:
        raw_env = environ.get(s.env.name)
        # A blank value is unset, except for a switch: any value set there
        # is a statement, and the switch's own rule reads it.
        if raw_env is not None and (s.type == "bool" or raw_env.strip()):
            entry = (Source.ENV, raw_env)
            if s.env.wins:
                layers.insert(0, entry)
            else:
                layers.append(entry)
    return layers


def validate(s: Setting, raw: Any, *, from_env: bool = False) -> Any:
    """*raw* as a value of *s*, or ``ValueError`` saying why it is not one.

    Environment values arrive as text and are converted first. A setting's
    parser runs before the typed check, and anything it raises is a problem
    with the value, never a crash.
    """
    if s.type == "list" and not isinstance(raw, (str, list, tuple)):
        raise ValueError("must be a list")
    if s.parser is not None:
        module_name, _, attr = s.parser.partition(":")
        parse = getattr(import_module(module_name), attr)
        try:
            raw = parse(raw)
        except Exception as exc:  # a reader's own parser may raise anything
            raise ValueError(str(exc) or type(exc).__name__) from None
    elif from_env:
        raw = _from_text(s, raw)
    return _CHECKS[s.type](s, raw)


def _bool(s: Setting, raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise ValueError("must be true or false")
    return raw


def _str(s: Setting, raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("must be text")
    if s.choices and raw not in s.choices:
        raise ValueError(f"must be one of {', '.join(s.choices)}")
    return raw


def _list(s: Setting, raw: Any) -> list[Any]:
    # One bare name reads as a list of one, as most list readers allow.
    items = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, (list, tuple)):
        raise ValueError("must be a list")
    unknown = [str(v) for v in items if s.choices and v not in s.choices]
    if unknown:
        raise ValueError(f"unknown {', '.join(unknown)}; known: {', '.join(s.choices)}")
    return list(items)


def _mapping(s: Setting, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("must be a mapping")
    return dict(raw)


def _from_text(s: Setting, text: str) -> Any:
    if s.type == "bool":
        assert s.env is not None
        return s.env.parse_bool(text)
    text = text.strip()
    if s.type == "int":
        try:
            return int(text)
        except ValueError:
            raise ValueError("must be a whole number") from None
    if s.type == "float":
        try:
            return float(text)
        except ValueError:
            raise ValueError("must be a number") from None
    if s.type == "list":
        return [item.strip() for item in text.split(",") if item.strip()]
    return text


def _number(s: Setting, raw: Any) -> int | float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("must be a number")
    if s.type == "int" and not isinstance(raw, int):
        raise ValueError("must be a whole number")
    if raw != raw:
        raise ValueError("must be a finite number")
    low, high = s.minimum, s.maximum
    if low is not None and s.exclusive_minimum and raw <= low:
        raise ValueError(f"must be more than {_fmt(low)}")
    if s.out_of_range == "clamp":
        raw = _clamp(raw, low, high)
    if raw in (float("inf"), float("-inf")):
        raise ValueError("must be a finite number")
    if low is not None and raw < low:
        raise ValueError(f"must be at least {_fmt(low)}")
    if high is not None and raw > high:
        raise ValueError(f"must be at most {_fmt(high)}")
    return raw


def _clamp(raw: int | float, low: float | None, high: float | None) -> int | float:
    """*raw* moved to the nearest bound it is past, keeping its type."""
    if low is not None and raw < low:
        return type(raw)(low)
    if high is not None and raw > high:
        return type(raw)(high)
    return raw


def _fmt(bound: float) -> str:
    return str(int(bound)) if float(bound).is_integer() else str(bound)


#: The typed check for each setting type.
_CHECKS = {
    "bool": _bool,
    "int": _number,
    "float": _number,
    "str": _str,
    "list": _list,
    "mapping": _mapping,
}
