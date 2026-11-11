"""``decisions:`` in ``.repowise/config.yaml``.

Built from the tables ``resolve_policy`` reads, so a new source, preset, mode
or switch appears here with no edit beyond its description. Values resolve
through ``resolve_policy`` itself: a switch's default depends on the mode, and
a source's on the preset.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.decisions.policy import (
    _MODE_DEFAULTS,
    _UNREADABLE_AS_ON,
    DEFAULT_HARNESSES,
    DISCOVERY_BOUNDS,
    MODES,
    PRESET_NAMES,
    PRESETS,
    SOURCE_SPECS,
    DecisionPolicy,
    resolve_policy,
)
from repowise.core.settings.model import Setting

AREA = "decisions"
_WRITER = "repowise.core.analysis.decisions.policy_store:write_policy"

#: Plain-words descriptions for the switches ``_MODE_DEFAULTS`` lists.
_SWITCHES: dict[str, str] = {
    "agent_acceptance": "Let a coding agent accept a decision, signed as the agent's.",
    "capture_prompt": "Ask the agent to record a decision when a commit states a choice.",
    "require_approval": "Make every new decision wait for a person before it applies.",
    "sync": "Send this repo's decisions to and from its copy on repowise.dev on update.",
    "session_start": "Open each agent session with the decisions relevant to its work.",
    "upkeep_prompt": "After a commit, ask the agent to settle the decisions it touched.",
}

_UPDATE_REASON = "update"


def _top(key: str, type_: Any, default: Any, description: str, **kw: Any) -> Setting:
    return Setting(
        key=f"decisions.{key}",
        type=type_,
        default=default,
        description=description,
        area=AREA,
        writer=_WRITER,
        **kw,
    )


def _settings() -> tuple[Setting, ...]:
    default_preset = PRESETS["default"]
    out: list[Setting] = [
        _top(
            "mode",
            "str",
            DecisionPolicy.__dataclass_fields__["mode"].default,
            "The one switch for decisions: full (capture and show), read_only "
            "(show what exists, capture nothing) or off.",
            choices=MODES,
            on_invalid="fallback",
            fallback="read_only",
        ),
        _top(
            "preset",
            "str",
            None,
            "A named set of capture sources to start from.",
            choices=PRESET_NAMES,
            reload=_UPDATE_REASON,
        ),
        _top(
            "enabled",
            "bool",
            default_preset["enabled"],
            "Capture decisions from code and history at all.",
            reload=_UPDATE_REASON,
        ),
        _top(
            "llm",
            "bool",
            default_preset["llm"],
            "Allow a model call while capturing decisions. Off keeps capture local.",
            reload=_UPDATE_REASON,
        ),
    ]
    for name, default in _MODE_DEFAULTS["full"].items():
        unreadable_on = name in _UNREADABLE_AS_ON
        out.append(
            _top(
                name,
                "bool",
                default,
                _SWITCHES[name],
                on_invalid="fallback" if unreadable_on != default else "default",
                fallback=unreadable_on,
            )
        )
    out.append(
        _top(
            "update_mining",
            "bool",
            DecisionPolicy.__dataclass_fields__["update_mining"].default,
            "Let repowise update call a model to find decisions in new commits and PRs.",
            reload=_UPDATE_REASON,
        )
    )
    for name, (low, high, default) in DISCOVERY_BOUNDS.items():
        out.append(
            _top(
                f"discovery.{name}",
                "int",
                default,
                "Most sessions read in one discovery pass."
                if name == "max_sessions"
                else "Most input tokens one discovery pass may send.",
                minimum=low,
                maximum=high,
                reload=_UPDATE_REASON,
            )
        )
    out.append(
        _top(
            "harnesses",
            "list",
            list(DEFAULT_HARNESSES),
            "Which coding agents' local session transcripts may be read.",
            reload=_UPDATE_REASON,
        )
    )
    for spec in SOURCE_SPECS:
        if not spec.togglable:
            continue
        out.append(
            _top(
                f"sources.{spec.key}.enabled",
                "bool",
                spec.default_enabled,
                f"Capture from {spec.label.lower()}: {spec.description}",
                reload=_UPDATE_REASON,
            )
        )
        if spec.llm:
            out.append(
                _top(
                    f"sources.{spec.key}.llm",
                    "bool",
                    spec.llm,
                    f"Allow a model call when capturing from {spec.label.lower()}.",
                    reload=_UPDATE_REASON,
                )
            )
    out.append(
        _top(
            "session_mining",
            "bool",
            None,
            "Older spelling of sources.session.enabled; still read.",
            internal=True,
            reload=_UPDATE_REASON,
        )
    )
    return tuple(out)


SETTINGS: tuple[Setting, ...] = _settings()


def resolve_setting(setting: Setting, config: dict[str, Any]) -> Any:
    """The value ``resolve_policy`` gives *setting* for this config."""
    policy = resolve_policy(config).policy
    parts = setting.key.split(".")[1:]
    raw = config.get("decisions") if isinstance(config.get("decisions"), dict) else {}
    if parts[0] == "sources":
        return getattr(policy.sources[parts[1]], parts[2])
    if parts[0] == "discovery":
        return getattr(policy.discovery, parts[1])
    if parts[0] == "harnesses":
        return list(policy.harnesses)
    if parts[0] == "preset":
        return raw.get("preset") if raw.get("preset") in PRESETS else None
    if parts[0] == "session_mining":
        value = raw.get("session_mining")
        return value if isinstance(value, bool) else None
    return getattr(policy, parts[0])
