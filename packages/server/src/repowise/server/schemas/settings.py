"""``/api/repos/{repo_id}/settings``: every setting, as ``repowise config`` gives it.

The fields mirror :meth:`Setting.to_dict` and the CLI's ``--format json``, so a
page and an agent read the same shape.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SettingType = Literal["bool", "int", "float", "str", "list", "mapping"]


class SettingEnvVar(BaseModel):
    """The environment variable that also sets a setting."""

    name: str
    #: The variable beats the file value.
    wins: bool
    #: ``name`` is a prefix the code appends a suffix to.
    prefix: bool


class SettingState(BaseModel):
    """Where one setting stands."""

    value: Any = None
    #: ``default``, ``env``, ``repo_file`` or ``user_file``.
    source: str
    #: Why a value that is present was not used.
    problem: str | None = None
    #: A layer above this repository decides the value; never true here.
    locked: bool = False
    default: Any = None
    #: Set somewhere, to something other than the default.
    changed: bool


class SettingView(SettingState):
    """One setting: what it is and where it stands."""

    key: str
    type: SettingType
    default_note: str | None = None
    description: str
    area: str
    #: ``repo``, ``user`` or ``env``.
    scope: Literal["repo", "user", "env"]
    #: The file that holds it, ``None`` for an environment-only setting.
    file: str | None = None
    choices: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    env: SettingEnvVar | None = None
    internal: bool = False
    #: ``immediate``, ``update``, ``reindex`` or ``restart``.
    reload: Literal["immediate", "update", "reindex", "restart"]
    #: What a value must be, in plain words.
    accepts: str
    #: When a change takes effect, in plain words.
    applies: str
    #: Whether this page may change it: a file setting in this repository.
    writable: bool


class SettingArea(BaseModel):
    area: str
    settings: list[SettingView]


class SettingSuggestion(BaseModel):
    """A value that fits this repository better than the current one."""

    key: str
    current_value: Any = None
    suggested_value: Any = None
    reason: str
    impact: str | None = None
    #: What the change saves or improves: ``tokens``, ``cost`` or ``quality``.
    benefit: str | None = None
    #: The command that applies it.
    command: str


class SettingsResponse(BaseModel):
    #: Areas in registry order, each with its settings in declaration order.
    areas: list[SettingArea]
    suggestions: list[SettingSuggestion] = Field(default_factory=list)


class SettingUpdate(BaseModel):
    """Set one setting to ``value``, or ``reset`` it to its default."""

    key: str = Field(..., min_length=1)
    value: Any = None
    reset: bool = False

    @model_validator(mode="after")
    def _one_of(self) -> SettingUpdate:
        if self.reset == ("value" in self.model_fields_set):
            raise ValueError("send a value, or reset: true, not both")
        return self


class SettingAlsoMoved(BaseModel):
    key: str
    before: SettingState
    after: SettingState


class SettingChangeResponse(BaseModel):
    """What one write did, as ``repowise config set --format json`` reports it."""

    action: Literal["set", "reset"]
    key: str
    changed: bool
    before: SettingState
    after: SettingState
    #: Other settings the write moved with it.
    also: list[SettingAlsoMoved]
    #: What the setting does and when the change applies.
    effect: str
    #: Commands that put each moved setting back.
    undo: list[str]
    #: The environment variable that still decides the value, if one does.
    env_override: str | None = None
