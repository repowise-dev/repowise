"""What one setting is: its key, type, default, and where it is read from.

Stdlib only, and nothing heavier than ``dataclasses`` and ``enum``: hooks on
an agent's hot path may import this to read one flag.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

__all__ = [
    "FALSY",
    "TRUTHY",
    "Env",
    "Resolved",
    "Setting",
    "SettingError",
    "Source",
    "flag",
    "parse_flag",
]

SettingType = Literal["bool", "int", "float", "str", "list", "mapping"]
Scope = Literal["repo", "user", "env"]
#: When a change takes effect: on the next read, on the next ``repowise
#: update``, only after a re-embed, or after the server or MCP process restarts.
Reload = Literal["immediate", "update", "reindex", "restart"]
#: What a present but unusable value reads as: the default, an error, or
#: :attr:`Setting.fallback` (a gate that reads an unreadable value as closed).
OnInvalid = Literal["default", "error", "fallback"]

#: The common "is this env var on" spellings, compared after strip and lower.
TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})
#: The common "is this env var off" spellings, compared after strip and lower.
FALSY: frozenset[str] = frozenset({"0", "false", "no", "off"})



def parse_flag(raw: Any) -> bool:
    """A switch's value as a config file states it, or ``ValueError``.

    A YAML bool, ``0`` or ``1``, or a quoted :data:`TRUTHY` / :data:`FALSY`
    spelling: ``"false"`` is off, never on because the string is non-empty.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in TRUTHY:
            return True
        if text in FALSY:
            return False
    raise ValueError("must be true or false")


def flag(raw: Any, default: bool) -> bool:
    """:func:`parse_flag`, with *default* for an absent or unreadable value.

    The one reading every config switch shares, so a hook, the CLI and the
    settings listing cannot disagree about ``enabled: "false"`` or ``0``.
    """
    try:
        return parse_flag(raw)
    except ValueError:
        return default


#: The file each scope lives in, unless a setting names another.
_SCOPE_FILES: dict[str, str | None] = {
    "repo": ".repowise/config.yaml",
    "user": "~/.repowise/config.yaml",
    "env": None,
}


class SettingError(ValueError):
    """A value a setting declared ``on_invalid="error"`` for could not be read."""


class Source(StrEnum):
    """Where a resolved value came from.

    A plain string enum so it serialises as its value. Another layer (an
    organisation policy, a dashboard) is one more member.
    """

    DEFAULT = "default"
    ENV = "env"
    REPO_FILE = "repo_file"
    USER_FILE = "user_file"


@dataclass(frozen=True, slots=True)
class Env:
    """An environment variable that sets a setting.

    A bool is read one of two ways, after strip and lower: true exactly when
    the value is in ``true_values``, or true unless it is in ``false_values``.
    Set one of them; neither reads :data:`TRUTHY`.
    """

    name: str
    #: The variable beats the file value. Most settings let the file win.
    wins: bool = False
    true_values: frozenset[str] | None = None
    false_values: frozenset[str] | None = None
    #: ``name`` is a prefix and the code appends a suffix (``REPOWISE_SEAM_<NAME>``).
    prefix: bool = False

    def parse_bool(self, raw: str) -> bool:
        value = raw.strip().lower()
        if self.false_values is not None:
            return value not in self.false_values
        return value in (self.true_values if self.true_values is not None else TRUTHY)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "wins": self.wins, "prefix": self.prefix}


@dataclass(frozen=True, slots=True)
class Setting:
    """One setting, declared next to the code that reads it.

    ``key`` is the dotted path in the setting's file (``decisions.mode``,
    ``hooks.read_skeleton``). A setting that only an environment variable sets
    is keyed by that variable's name and has ``scope="env"``.
    """

    key: str
    type: SettingType
    default: Any
    #: Plain words for the person or agent changing it.
    description: str
    area: str
    scope: Scope = "repo"
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: ``minimum`` itself is not allowed ("a positive number" is 0, exclusive).
    exclusive_minimum: bool = False
    env: Env | None = None
    #: The file the key lives in when it is not the scope's usual one.
    file: str | None = None
    #: Test, debug or plumbing knobs: registered so nothing is unaccounted
    #: for, hidden from user listings.
    internal: bool = False
    reload: Reload = "immediate"
    on_invalid: OnInvalid = "default"
    #: The value an unusable one reads as, when ``on_invalid="fallback"``.
    fallback: Any = None
    #: ``"module:function"`` that parses a raw value the way its area does,
    #: raising ``ValueError`` when it cannot. For grammars a type cannot say
    #: (``mcp.tools``, ``folder_coverage``). Imported only when used.
    parser: str | None = None
    #: ``"module:function"`` called as ``writer(root, key, value)`` to write
    #: an already-validated value, ``None`` meaning "back to the default", for
    #: an area whose writes do more than set one key (decisions, the provider).
    #: ``None`` here means :func:`repowise.core.settings.writers.write_file_value`.
    writer: str | None = None
    #: Whether this repository would benefit from a non-default value.
    #: Takes facts about the repository, returns a reason or ``None``.
    suggest_when: Callable[[Any], str | None] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.choices and self.type not in ("str", "list"):
            raise ValueError(f"{self.key}: choices apply to str or list settings")
        if (self.minimum is not None or self.maximum is not None) and self.type not in (
            "int",
            "float",
        ):
            raise ValueError(f"{self.key}: bounds apply to int or float settings")
        if self.scope == "env" and (
            self.env is None
            or not (self.key == self.env.name or (self.env.prefix and self.key.startswith(self.env.name)))
        ):
            raise ValueError(f"{self.key}: an env-scoped setting is keyed by its variable")

    @property
    def path(self) -> str | None:
        """The file holding this key, ``None`` for an environment-only setting."""
        return self.file or _SCOPE_FILES[self.scope]

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready description, for listings, docs and the settings API."""
        return {
            "key": self.key,
            "type": self.type,
            "default": _jsonable(self.default),
            "description": self.description,
            "area": self.area,
            "scope": self.scope,
            "file": self.path,
            "choices": list(self.choices),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "exclusive_minimum": self.exclusive_minimum,
            "env": self.env.to_dict() if self.env else None,
            "internal": self.internal,
            "reload": self.reload,
        }


@dataclass(frozen=True, slots=True)
class Resolved:
    """A setting's effective value and where it came from."""

    value: Any
    source: Source
    #: Why a present value was not used, when it was not.
    problem: str | None = None
    #: Set by a layer that forbids changing the value here. Always false in
    #: a single repository; reserved for a layer above it.
    locked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": _jsonable(self.value),
            "source": self.source.value,
            "problem": self.problem,
            "locked": self.locked,
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (tuple, frozenset, set)):
        return sorted(value) if isinstance(value, (frozenset, set)) else list(value)
    return value
