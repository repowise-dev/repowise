"""One resolved capture policy for the decisions layer.

Everything that decides whether a capture source runs, or whether it may call a
model, reads a :class:`DecisionPolicy` built here. CLI, server, the index
pipeline and the session miner share this module so a source cannot be enabled
in one surface and invisible in another, which is how the three hand-copied
source lists in ``provenance`` already drifted.

Resolution is pure: dict in, frozen dataclass out, no I/O and no provider
knowledge. Provider availability enters only at :meth:`DecisionPolicy.runtime`,
because the same policy resolves identically on a machine with no API key.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from repowise.core.analysis.decisions.provenance import RETIRED_SOURCES

__all__ = [
    "CAPTURE_SOURCE_KEYS",
    "DISCOVERY_BOUNDS",
    "INDEX_SOURCE_KEYS",
    "PRESETS",
    "PRESET_NAMES",
    "SOURCE_SPECS",
    "DecisionPolicy",
    "DiscoveryBudget",
    "PolicyResolution",
    "SourceRuntime",
    "SourceSetting",
    "SourceSpec",
    "preset_policy",
    "resolve_policy",
]

Authority = Literal["machine", "human"]
SourceStatus = Literal[
    "enabled",
    "disabled",
    "deterministic_only",
    "skipped_no_provider",
    "always_on",
]


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Static capability metadata for one capture source.

    ``key`` is the wire name, and it is the name already stored on every
    ``decision_records`` row and already accepted by ``decisions.sources`` in
    ``.repowise/config.yaml``. Renaming it would orphan stored rows, most
    sharply for ``comment``: ``code_comment`` is a *retired* source at rank 2
    with legacy rows still carrying it.
    """

    key: str
    label: str
    description: str
    #: Produces something with no provider configured.
    deterministic: bool
    #: Has a stage that calls a model.
    llm: bool
    authority: Authority
    default_enabled: bool
    #: False for authority-bearing entry routes, which are not machine capture
    #: and so have nothing to switch off.
    togglable: bool = True


SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        key="inline_marker",
        label="Inline markers",
        description="WHY:/DECISION:/TRADEOFF: comment markers in source files.",
        deterministic=True,
        llm=True,
        authority="machine",
        default_enabled=True,
    ),
    SourceSpec(
        key="git_archaeology",
        label="Git archaeology",
        description="Commit messages carrying one of the decision verbs.",
        deterministic=False,
        llm=True,
        authority="machine",
        default_enabled=True,
    ),
    SourceSpec(
        key="adr",
        label="ADR files",
        description="Nygard and MADR architecture decision records.",
        deterministic=True,
        llm=True,
        authority="machine",
        default_enabled=True,
    ),
    SourceSpec(
        key="pr",
        label="Pull request bodies",
        description="Squash-merge and PR bodies that read as a description.",
        deterministic=False,
        llm=True,
        authority="machine",
        default_enabled=True,
    ),
    SourceSpec(
        key="comment",
        label="Comment archaeology",
        description="Rationale prose in comments on high-centrality files.",
        deterministic=False,
        llm=True,
        authority="machine",
        default_enabled=True,
    ),
    SourceSpec(
        key="session",
        label="Agent sessions",
        description="Durable choices mined from local coding-agent transcripts.",
        deterministic=True,
        llm=True,
        authority="machine",
        default_enabled=True,
    ),
    SourceSpec(
        key="session_discovery",
        label="Session discovery",
        description="One broad model pass over new transcript prose each update.",
        deterministic=False,
        llm=True,
        authority="machine",
        default_enabled=False,
    ),
    SourceSpec(
        key="cli",
        label="Manual entry",
        description="Decisions you recorded yourself. Always available.",
        deterministic=True,
        llm=False,
        authority="human",
        default_enabled=True,
        togglable=False,
    ),
)

_SPECS_BY_KEY: dict[str, SourceSpec] = {spec.key: spec for spec in SOURCE_SPECS}

#: Machine capture sources, in extraction order. ``cli`` is excluded: manual
#: entry is an authority route, not something a capture run performs.
CAPTURE_SOURCE_KEYS: tuple[str, ...] = tuple(
    spec.key for spec in SOURCE_SPECS if spec.authority == "machine"
)

#: Index-time sources, in the order ``DecisionExtractor.extract_all`` runs them.
#: ``session`` is mined separately by the transcript miner.
INDEX_SOURCE_KEYS: tuple[str, ...] = (
    "inline_marker",
    "git_archaeology",
    "adr",
    "pr",
    "comment",
)


#: Inclusive ``(min, max, default)`` for each broad-discovery budget knob.
#: The upper bounds are what the probe actually measured at: roughly 8-12
#: session deltas and about 30,000 input tokens for one call. Bounds are
#: enforced on resolution so a typo'd config costs a warning, not a bill.
DISCOVERY_BOUNDS: dict[str, tuple[int, int, int]] = {
    "max_sessions": (1, 24, 12),
    "max_input_tokens": (2_000, 60_000, 30_000),
}


@dataclass(frozen=True, slots=True)
class DiscoveryBudget:
    """Per-update ceiling on the one broad session-discovery call."""

    max_sessions: int = DISCOVERY_BOUNDS["max_sessions"][2]
    max_input_tokens: int = DISCOVERY_BOUNDS["max_input_tokens"][2]

    def to_dict(self) -> dict[str, int]:
        return {
            "max_sessions": self.max_sessions,
            "max_input_tokens": self.max_input_tokens,
        }


_DEFAULT_DISCOVERY = DiscoveryBudget()


@dataclass(frozen=True, slots=True)
class SourceSetting:
    """Per-source configuration, already merged with defaults."""

    enabled: bool
    llm: bool


def _preset(**overrides: SourceSetting) -> dict[str, SourceSetting]:
    base = {
        spec.key: SourceSetting(enabled=spec.default_enabled, llm=spec.llm)
        for spec in SOURCE_SPECS
    }
    base.update(overrides)
    return base


_ON = SourceSetting(enabled=True, llm=True)
_LOCAL = SourceSetting(enabled=True, llm=False)
_OFF = SourceSetting(enabled=False, llm=False)

#: Named conveniences over the same policy. A preset is only a way to write
#: several settings at once; nothing downstream branches on the preset name.
PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "enabled": True,
        "llm": True,
        "sources": _preset(),
    },
    "off": {
        "enabled": False,
        "llm": True,
        "sources": _preset(**dict.fromkeys(CAPTURE_SOURCE_KEYS, _OFF)),
    },
    "local_only": {
        "enabled": True,
        "llm": False,
        "sources": _preset(
            inline_marker=_LOCAL,
            adr=_LOCAL,
            session=_LOCAL,
            git_archaeology=_OFF,
            pr=_OFF,
            comment=_OFF,
        ),
    },
    "balanced": {
        "enabled": True,
        "llm": True,
        "sources": _preset(comment=_OFF, session_discovery=_ON),
    },
    "full": {
        "enabled": True,
        "llm": True,
        "sources": _preset(**dict.fromkeys(CAPTURE_SOURCE_KEYS, _ON)),
    },
}

PRESET_NAMES: tuple[str, ...] = tuple(PRESETS)

#: What an absent ``decisions:`` block resolves to: every source whose spec
#: says it shipped on, with the model enabled, because that is what a repo
#: indexed before this module existed already did and a config-less repo must
#: not change behavior on upgrade. Deliberately *not* ``full``: ``full`` means
#: every source there is, so a source added later joins it, and reusing it here
#: would switch that source on for every repository that never asked for it.
#: New repos pick a preset explicitly instead of inheriting a hidden default.
_LEGACY_DEFAULT = PRESETS["default"]


@dataclass(frozen=True, slots=True)
class SourceRuntime:
    """A source's resolved state, with provider availability folded in."""

    key: str
    label: str
    description: str
    authority: Authority
    deterministic: bool
    supports_llm: bool
    togglable: bool
    enabled: bool
    llm_enabled: bool
    status: SourceStatus
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "authority": self.authority,
            "deterministic": self.deterministic,
            "supports_llm": self.supports_llm,
            "togglable": self.togglable,
            "enabled": self.enabled,
            "llm_enabled": self.llm_enabled,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    """The resolved capture policy for one repository."""

    enabled: bool
    llm: bool
    sources: dict[str, SourceSetting]
    discovery: DiscoveryBudget = _DEFAULT_DISCOVERY

    # -- queries ---------------------------------------------------------

    def source_enabled(self, key: str) -> bool:
        """Whether *key* may capture at all."""
        spec = _SPECS_BY_KEY.get(key)
        if spec is None:
            return False
        if not spec.togglable:
            return True
        if not self.enabled:
            return False
        setting = self.sources.get(key)
        return bool(setting and setting.enabled)

    def llm_allowed(self, key: str) -> bool:
        """Whether *key* may make a decision-extraction model call.

        The global switch wins over the per-source one, so ``llm: false`` is a
        single provable statement about the whole layer rather than a default
        several sources can each opt back out of.
        """
        if not self.llm or not self.source_enabled(key):
            return False
        spec = _SPECS_BY_KEY.get(key)
        if spec is None or not spec.llm:
            return False
        setting = self.sources.get(key)
        return bool(setting and setting.llm)

    def enabled_index_sources(self) -> tuple[str, ...]:
        """Enabled sources for ``DecisionExtractor.extract_all``, in run order.

        A source with no deterministic stage is dropped when the model is off:
        it would run, call nothing and return zero, which reads on screen as a
        repo with no PRs rather than a switch the user set.
        """
        return tuple(
            key
            for key in INDEX_SOURCE_KEYS
            if self.source_enabled(key)
            and (self.llm_allowed(key) or _SPECS_BY_KEY[key].deterministic)
        )

    def any_llm_allowed(self) -> bool:
        return any(self.llm_allowed(key) for key in CAPTURE_SOURCE_KEYS)

    # -- projections -----------------------------------------------------

    def preset_name(self) -> str:
        """The preset this policy equals, or ``custom``."""
        for name, spec in PRESETS.items():
            if (
                self.enabled == spec["enabled"]
                and self.llm == spec["llm"]
                and self.sources == spec["sources"]
                and self.discovery == _DEFAULT_DISCOVERY
            ):
                return name
        return "custom"

    def runtime(self, *, provider_available: bool) -> tuple[SourceRuntime, ...]:
        """Per-source state for reporting, with a reason for every non-run."""
        out: list[SourceRuntime] = []
        for spec in SOURCE_SPECS:
            enabled = self.source_enabled(spec.key)
            llm_enabled = self.llm_allowed(spec.key)
            status, reason = self._status_for(spec, enabled, llm_enabled, provider_available)
            out.append(
                SourceRuntime(
                    key=spec.key,
                    label=spec.label,
                    description=spec.description,
                    authority=spec.authority,
                    deterministic=spec.deterministic,
                    supports_llm=spec.llm,
                    togglable=spec.togglable,
                    enabled=enabled,
                    llm_enabled=llm_enabled and provider_available,
                    status=status,
                    reason=reason,
                )
            )
        return tuple(out)

    def _status_for(
        self,
        spec: SourceSpec,
        enabled: bool,
        llm_enabled: bool,
        provider_available: bool,
    ) -> tuple[SourceStatus, str]:
        if not spec.togglable:
            return "always_on", "Manual entry is always available."
        if not self.enabled:
            return "disabled", "Decision capture is off for this repository."
        if not enabled:
            return "disabled", "This source is switched off."
        if not spec.llm:
            return "enabled", "Runs without a model."
        if not llm_enabled:
            if not self.llm:
                reason = "Decision LLM extraction is off."
            else:
                reason = "Model structuring is off for this source."
            if spec.deterministic:
                return "deterministic_only", f"{reason} The deterministic stage still runs."
            return "disabled", f"{reason} This source has no deterministic stage."
        if not provider_available:
            if spec.deterministic:
                return "deterministic_only", (
                    "No LLM provider is configured. The deterministic stage still runs."
                )
            return "skipped_no_provider", "No LLM provider is configured."
        return "enabled", "Runs with model structuring."

    def to_config_block(self) -> dict[str, Any]:
        """The ``decisions:`` mapping to write back to ``config.yaml``.

        A source whose setting equals its capability default is written as a
        bare bool, so an untouched config stays as short as the user left it.
        """
        sources: dict[str, Any] = {}
        for spec in SOURCE_SPECS:
            if not spec.togglable:
                continue
            setting = self.sources.get(spec.key, SourceSetting(spec.default_enabled, spec.llm))
            if setting.llm == spec.llm:
                sources[spec.key] = setting.enabled
            else:
                sources[spec.key] = {"enabled": setting.enabled, "llm": setting.llm}
        block: dict[str, Any] = {
            "enabled": self.enabled,
            "llm": self.llm,
            "sources": sources,
        }
        if self.discovery != _DEFAULT_DISCOVERY:
            block["discovery"] = self.discovery.to_dict()
        return block

    def to_dict(self, *, provider_available: bool = True) -> dict[str, Any]:
        """The JSON payload shared by ``decision config show`` and the API."""
        return {
            "enabled": self.enabled,
            "llm": self.llm,
            "preset": self.preset_name(),
            "discovery": self.discovery.to_dict(),
            "sources": [rt.to_dict() for rt in self.runtime(provider_available=provider_available)],
        }

    # -- mutations (all return a new policy) -----------------------------

    def with_source(self, key: str, *, enabled: bool | None = None, llm: bool | None = None):
        spec = _SPECS_BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"Unknown decision source: {key}")
        if not spec.togglable:
            raise ValueError(f"{key} is an authority route and cannot be switched off")
        current = self.sources.get(key, SourceSetting(spec.default_enabled, spec.llm))
        merged = SourceSetting(
            enabled=current.enabled if enabled is None else enabled,
            llm=current.llm if llm is None else llm,
        )
        sources = dict(self.sources)
        sources[key] = merged
        return replace(self, sources=sources)

    def with_discovery(self, **fields: int) -> DecisionPolicy:
        """A copy with the named discovery budget fields replaced."""
        for name, value in fields.items():
            low, high, _ = DISCOVERY_BOUNDS[name]
            if not low <= value <= high:
                raise ValueError(f"decisions.discovery.{name} must be between {low} and {high}")
        return replace(self, discovery=replace(self.discovery, **fields))

    def with_llm(self, value: bool) -> DecisionPolicy:
        return replace(self, llm=value)

    def with_enabled(self, value: bool) -> DecisionPolicy:
        return replace(self, enabled=value)


@dataclass(frozen=True, slots=True)
class PolicyResolution:
    """A resolved policy plus everything the user should be told about it."""

    policy: DecisionPolicy
    #: Recoverable problems: unknown keys, wrong types. Never discarded
    #: silently, because a typo'd source name reads as a working switch.
    warnings: tuple[str, ...] = ()
    #: Keys carried through from the legacy shape, for the migration notice.
    legacy_keys: tuple[str, ...] = ()


def _resolve_discovery(raw: Any, warnings: list[str]) -> DiscoveryBudget:
    """Bounds-checked discovery budget from the ``decisions.discovery`` block."""
    if raw is None:
        return _DEFAULT_DISCOVERY
    if not isinstance(raw, dict):
        warnings.append("`decisions.discovery:` is not a mapping; ignoring it.")
        return _DEFAULT_DISCOVERY
    fields: dict[str, int] = {}
    for name, (low, high, default) in DISCOVERY_BOUNDS.items():
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            warnings.append(f"`decisions.discovery.{name}` must be an integer; using {default}.")
            value = default
        elif not low <= value <= high:
            warnings.append(
                f"`decisions.discovery.{name}` must be between {low} and {high}; using {default}."
            )
            value = default
        fields[name] = value
    for field in set(raw) - set(DISCOVERY_BOUNDS):
        warnings.append(f"Unknown key `decisions.discovery.{field}`; ignoring it.")
    return DiscoveryBudget(**fields)


def preset_policy(name: str) -> DecisionPolicy:
    """The policy a named preset resolves to."""
    spec = PRESETS.get(name)
    if spec is None:
        raise ValueError(f"Unknown preset: {name}. Choose one of {', '.join(PRESET_NAMES)}.")
    return DecisionPolicy(
        enabled=bool(spec["enabled"]),
        llm=bool(spec["llm"]),
        sources=dict(spec["sources"]),
    )


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def resolve_policy(repo_config: dict[str, Any] | None) -> PolicyResolution:
    """Resolve a loaded ``.repowise/config.yaml`` dict into one policy.

    Accepts both the legacy shape (``session_mining: bool`` and
    ``sources.<name>: bool``) and the current one, and resolves the legacy
    shape to exactly the behavior it had before this module existed.
    """
    warnings: list[str] = []
    legacy: list[str] = []

    raw = (repo_config or {}).get("decisions")
    if raw is None:
        raw = {}
    elif not isinstance(raw, dict):
        warnings.append("`decisions:` is not a mapping; ignoring it and using defaults.")
        raw = {}

    preset_name = raw.get("preset")
    if preset_name is not None and preset_name not in PRESETS:
        warnings.append(f"Unknown preset {preset_name!r}; ignoring it.")
        preset_name = None
    base = PRESETS[preset_name] if preset_name else _LEGACY_DEFAULT

    enabled = _as_bool(raw.get("enabled"))
    if enabled is None:
        if "enabled" in raw:
            warnings.append("`decisions.enabled` is not a boolean; ignoring it.")
        enabled = bool(base["enabled"])

    llm = _as_bool(raw.get("llm"))
    if llm is None:
        if "llm" in raw:
            warnings.append("`decisions.llm` is not a boolean; ignoring it.")
        llm = bool(base["llm"])

    sources: dict[str, SourceSetting] = dict(base["sources"])

    raw_sources = raw.get("sources")
    enumerated = isinstance(raw_sources, dict)
    if raw_sources is None:
        raw_sources = {}
    elif not enumerated:
        warnings.append("`decisions.sources:` is not a mapping; ignoring it.")
        raw_sources = {}

    # A `sources:` block written next to a preset is the authoritative list of
    # the sources that preset covered when it was written, because every write
    # through this layer enumerates all of them. So a source missing from a
    # present enumeration is one that did not exist yet, and it stays off: a
    # stored `preset: balanced` must not silently acquire a model call the day
    # a new source joins that preset. A bare `preset:` with no enumeration is a
    # live declaration and does get the preset's current membership.
    if preset_name and enumerated:
        for spec in SOURCE_SPECS:
            if spec.togglable and not spec.default_enabled and spec.key not in raw_sources:
                sources[spec.key] = SourceSetting(enabled=False, llm=spec.llm)

    for key, value in raw_sources.items():
        spec = _SPECS_BY_KEY.get(str(key))
        if spec is None or not spec.togglable:
            if str(key) in RETIRED_SOURCES:
                # These were documented switches. Say they are gone rather than
                # implying the user typed something wrong.
                warnings.append(
                    f"`decisions.sources.{key}` names a retired source that no "
                    "longer extracts; it has no effect."
                )
            else:
                warnings.append(f"Unknown decision source {key!r} in config; ignoring it.")
            continue
        current = sources.get(spec.key, SourceSetting(spec.default_enabled, spec.llm))
        if isinstance(value, bool):
            sources[spec.key] = SourceSetting(enabled=value, llm=current.llm)
        elif isinstance(value, dict):
            src_enabled = _as_bool(value.get("enabled"))
            src_llm = _as_bool(value.get("llm"))
            for field in set(value) - {"enabled", "llm"}:
                warnings.append(f"Unknown key `decisions.sources.{spec.key}.{field}`; ignoring it.")
            sources[spec.key] = SourceSetting(
                enabled=current.enabled if src_enabled is None else src_enabled,
                llm=current.llm if src_llm is None else src_llm,
            )
        else:
            warnings.append(
                f"`decisions.sources.{spec.key}` must be a boolean or a mapping; ignoring it."
            )

    # Legacy: session_mining gated the whole transcript miner. It only narrows,
    # so it is ANDed with sources.session rather than shadowed by it. Letting an
    # explicit `sources.session: true` win would start reading transcripts on a
    # config that had switched them off, which is the one thing this resolver
    # must never do.
    session_mining = raw.get("session_mining")
    if session_mining is not None:
        legacy.append("session_mining")
        if isinstance(session_mining, bool):
            current = sources["session"]
            sources["session"] = SourceSetting(
                enabled=current.enabled and session_mining, llm=current.llm
            )
        else:
            warnings.append("`decisions.session_mining` is not a boolean; ignoring it.")

    discovery = _resolve_discovery(raw.get("discovery"), warnings)

    known = {"preset", "enabled", "llm", "sources", "session_mining", "discovery"}
    for field in set(raw) - known:
        warnings.append(f"Unknown key `decisions.{field}`; ignoring it.")

    return PolicyResolution(
        policy=DecisionPolicy(
            enabled=enabled, llm=llm, sources=sources, discovery=discovery
        ),
        warnings=tuple(warnings),
        legacy_keys=tuple(legacy),
    )
