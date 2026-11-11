"""``decisions:`` in ``.repowise/config.yaml``.

Built from the tables ``resolve_policy`` reads, so a new source, preset, mode
or switch appears here with no edit beyond its description. Values resolve
through ``resolve_policy`` itself: a switch's default depends on the mode, and
a source's on the preset.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.decisions.policy import (
    DEFAULT_HARNESSES,
    DISCOVERY_BOUNDS,
    MODE_DEFAULTS,
    MODES,
    PRESET_NAMES,
    PRESETS,
    SOURCE_SPECS,
    UNREADABLE_AS_ON,
    DecisionPolicy,
    resolve_policy,
)
from repowise.core.settings.model import Resolved, Setting, Source

AREA = "decisions"
_WRITER = "repowise.core.analysis.decisions.policy_store:write_policy"

#: Plain-words descriptions for the switches ``MODE_DEFAULTS`` lists.
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
            parser="repowise.core.settings.parsers:decisions_mode",
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
    out += _switch_settings()
    out += _source_settings()
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


def _switch_settings() -> list[Setting]:
    out: list[Setting] = []
    for name, default in MODE_DEFAULTS["full"].items():
        unreadable_on = name in UNREADABLE_AS_ON
        by_mode = {mode: MODE_DEFAULTS[mode][name] for mode in MODES}
        varies = len(set(by_mode.values())) > 1
        out.append(
            _top(
                name,
                "bool",
                None if varies else default,
                _SWITCHES[name],
                default_note=_mode_note(by_mode) if varies else None,
                on_invalid="fallback" if unreadable_on != default else "default",
                fallback=unreadable_on,
            )
        )
    out.append(
        _top(
            "update_mining",
            "bool",
            None,
            "Let repowise update call a model to find decisions in new commits and PRs.",
            default_note="Unset: off, unless the decisions block names a preset or "
            "lists sources, which keeps an older setup mining.",
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
    return out


def _source_settings() -> list[Setting]:
    out: list[Setting] = []
    for spec in SOURCE_SPECS:
        if not spec.togglable:
            continue
        out.append(
            _top(
                f"sources.{spec.key}.enabled",
                "bool",
                spec.default_enabled,
                f"Capture from {spec.label.lower()}: {spec.description} "
                "A preset may change the default.",
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
    return out


def _mode_note(by_mode: dict[str, bool]) -> str:
    """``"Unset: on under decisions.mode full; off under read_only, off."``"""
    groups: dict[bool, list[str]] = {}
    for mode, value in by_mode.items():
        groups.setdefault(value, []).append(mode)
    parts = [f"{'on' if v else 'off'} under {', '.join(m)}" for v, m in groups.items()]
    return f"Unset: depends on decisions.mode: {'; '.join(parts)}."


def _value(policy: DecisionPolicy, config: dict[str, Any], parts: list[str]) -> Any:
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


def _named_in(warning: str, setting: Setting) -> bool:
    """Whether a policy warning is about *setting*, its source or its block."""
    parts = setting.key.split(".")
    paths = {".".join(parts[:i]) for i in range(1, len(parts) + 1)}
    if setting.key == "decisions.preset" and warning.startswith("Unknown preset"):
        return True
    # A warning about a block (`decisions.sources:`) belongs to every key in it.
    return any(f"`{p}`" in warning or f"`{p}:" in warning for p in paths)


def resolve_setting(setting: Setting, config: dict[str, Any]) -> Resolved:
    """What ``resolve_policy`` gives *setting* for this config, and from where.

    The source is the file when the value differs from what an empty config
    resolves to, or the key is written there; a preset or the mode set
    through the file counts as the file. A warning ``resolve_policy`` raised
    about this key is its problem.
    """
    parts = setting.key.split(".")[1:]
    resolution = resolve_policy(config)
    value = _value(resolution.policy, config, parts)
    baseline = _value(resolve_policy({}).policy, {}, parts)
    problems = [w for w in resolution.warnings if _named_in(w, setting)]
    written = _written(config, parts)
    from_file = written or value != baseline
    if problems and setting.on_invalid != "fallback" and value == baseline:
        from_file = False
    return Resolved(
        value,
        Source.REPO_FILE if from_file else Source.DEFAULT,
        "; ".join(problems) or None,
    )


def _written(config: dict[str, Any], parts: list[str]) -> bool:
    node: Any = config.get("decisions")
    for i, part in enumerate(parts):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
        # ``sources.pr: true`` is the short form of ``sources.pr.enabled: true``.
        if isinstance(node, bool) and parts[i + 1 :] == ["enabled"]:
            return True
    return node is not None


SETTINGS: tuple[Setting, ...] = _settings()
