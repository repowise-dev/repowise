"""AI attribution settings and the honest share arithmetic every surface uses.

Settings live under ``attribution:`` in the repo's ``.repowise/config.yaml``
and in the user's ``~/.repowise/config.yaml``; per key the stricter value wins:

* ``enabled`` (default true): read public git markers (bot authors, trailers,
  footers, git-ai notes, Agent Trace files already in the repo). ``false`` in
  either file means nothing about AI is stored or shown.
* ``capture``: ``off`` (default) < ``local`` < ``notes``; the lower one wins and
  ``DO_NOT_TRACK`` forces ``off``.
* ``agents``: which agents are captured; the intersection of both lists.

An unset key takes its default before the comparison, so capture needs both the
repo and the user to opt in. An invalid value counts as the strictest value.

Commits carry a three-state label: ``agent``, ``human_attested`` (only from an
explicit human record) or ``unknown``. Unknown is never counted as human: an AI
share is the agent share of *covered* commits (agent plus human-attested), shown
with its coverage and withheld below :data:`COVERAGE_FLOOR`.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

AGENT = "agent"
HUMAN_ATTESTED = "human_attested"
UNKNOWN = "unknown"

CAPTURE_LEVELS: tuple[str, ...] = ("off", "local", "notes")

#: Below this share of commits with a known source, an AI share is withheld.
COVERAGE_FLOOR = 0.30

SUPPRESSED_NO_COMMITS = "no_commits"
SUPPRESSED_LOW_COVERAGE = "coverage_below_floor"

_SECTION = "attribution"

# Each config problem is logged once per process, not on every request.
_warned: set[str] = set()


@dataclass(frozen=True)
class AttributionLayer:
    """What one config file says. ``None`` means the key is not set there."""

    enabled: bool | None = None
    capture: str | None = None
    agents: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AttributionSettings:
    """The effective settings, with the two layers they came from."""

    enabled: bool = True
    capture: str = "off"
    agents: tuple[str, ...] = ()
    repo: AttributionLayer = AttributionLayer()
    user: AttributionLayer = AttributionLayer()
    do_not_track: bool = False
    errors: tuple[str, ...] = ()


def user_config_path() -> Path:
    """Resolved per call so tests can redirect ``Path.home``."""
    return Path.home() / ".repowise" / "config.yaml"


def _read_yaml(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.is_file():
        return {}, None
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{path}: could not be read ({exc})"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return {}, f"{path}: expected a YAML mapping"
    return data, None


def _parse_layer(
    config: dict[str, Any], source: str, known_agents: Iterable[str] | None
) -> tuple[AttributionLayer, list[str]]:
    block = config.get(_SECTION)
    if block is None:
        return AttributionLayer(), []
    if not isinstance(block, dict):
        return AttributionLayer(enabled=False), [f"{source}: attribution must be a mapping"]
    errors: list[str] = []
    enabled = block.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append(f"{source}: attribution.enabled must be true or false")
        enabled = False
    capture = block.get("capture")
    # YAML reads a bare off/no/false as False (the documented ``capture: off``).
    if capture is False:
        capture = "off"
    if capture is True:
        errors.append(f"{source}: attribution.capture: true names no level; use local or notes")
        capture = "off"
    if capture is not None:
        capture = str(capture).strip().lower()
        if capture not in CAPTURE_LEVELS:
            levels = ", ".join(CAPTURE_LEVELS)
            errors.append(f"{source}: attribution.capture must be one of {levels}")
            capture = "off"
    agents: tuple[str, ...] | None = None
    raw_agents = block.get("agents")
    if raw_agents is not None:
        if isinstance(raw_agents, str):
            raw_agents = [raw_agents]
        if not isinstance(raw_agents, list):
            errors.append(f"{source}: attribution.agents must be a list")
            raw_agents = []
        known = set(known_agents) if known_agents is not None else None
        kept: list[str] = []
        for name in raw_agents:
            agent = str(name).strip()
            if known is not None and agent not in known:
                errors.append(f"{source}: unknown agent {agent!r} in attribution.agents")
                continue
            if agent and agent not in kept:
                kept.append(agent)
        agents = tuple(kept)
    return AttributionLayer(enabled=enabled, capture=capture, agents=agents), errors


def _do_not_track() -> bool:
    from repowise.core.platform.telemetry import _env_truthy

    return _env_truthy("DO_NOT_TRACK")


def load_attribution_settings(
    repo_path: Path | str | None, *, known_agents: Iterable[str] | None = None
) -> AttributionSettings:
    """Resolve the effective settings for *repo_path* (``None``: user file only).

    *known_agents* validates ``attribution.agents``; the CLI passes the agent
    target ids. Without it any name is accepted.
    """
    errors: list[str] = []
    repo_layer = AttributionLayer()
    if repo_path is not None:
        repo_file = Path(repo_path) / ".repowise" / "config.yaml"
        data, err = _read_yaml(repo_file)
        if err:
            errors.append(err)
            repo_layer = AttributionLayer(enabled=False)
        else:
            repo_layer, layer_errors = _parse_layer(data, str(repo_file), known_agents)
            errors.extend(layer_errors)
    user_file = user_config_path()
    data, err = _read_yaml(user_file)
    if err:
        errors.append(err)
        user_layer = AttributionLayer(enabled=False)
    else:
        user_layer, layer_errors = _parse_layer(data, str(user_file), known_agents)
        errors.extend(layer_errors)

    layers = (repo_layer, user_layer)
    enabled = all(layer.enabled is not False for layer in layers)
    capture = min((layer.capture or "off" for layer in layers), key=CAPTURE_LEVELS.index)
    agent_sets = [layer.agents or () for layer in layers]
    agents = tuple(a for a in agent_sets[0] if a in agent_sets[1])
    for error in errors:
        if error not in _warned:
            _warned.add(error)
            import structlog

            structlog.get_logger(__name__).warning(
                "attribution_config_invalid", detail=error, treated_as="strictest value"
            )
    dnt = _do_not_track()
    if dnt or not enabled:
        capture = "off"
    return AttributionSettings(
        enabled=enabled,
        capture=capture,
        agents=agents if capture != "off" else (),
        repo=repo_layer,
        user=user_layer,
        do_not_track=dnt,
        errors=tuple(errors),
    )


def _stamp(path: Path) -> tuple[int, int]:
    """``(mtime_ns, size)``: changes on any rewrite, even one inside a second."""
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return 0, -1


@functools.lru_cache(maxsize=64)
def _enabled_cached(
    repo_path: str | None,
    _repo_stamp: tuple[int, int],
    _user_file: str,
    _user_stamp: tuple[int, int],
) -> bool:
    return load_attribution_settings(repo_path).enabled


def attribution_enabled(repo_path: Path | str | None) -> bool:
    """Whether AI attribution may be stored or shown for *repo_path*.

    Cached on both files' (mtime_ns, size), so per-request callers pay a stat,
    not a parse.
    A value that is not a path (no checkout) reads the user file only.
    """
    if not isinstance(repo_path, (str, Path)) or not str(repo_path):
        repo_path = None
    repo_stamp = (
        _stamp(Path(repo_path) / ".repowise" / "config.yaml") if repo_path is not None else (0, -1)
    )
    key = str(repo_path) if repo_path is not None else None
    user_file = user_config_path()
    return _enabled_cached(key, repo_stamp, str(user_file), _stamp(user_file))


def commit_label(attribution: str | None, agent_name: str | None) -> str:
    """A stored commit's three-state label; rows written before the label are derived."""
    if attribution in (AGENT, HUMAN_ATTESTED, UNKNOWN):
        return attribution
    return AGENT if agent_name else UNKNOWN


@dataclass(frozen=True)
class AttributionShare:
    """Agent share of covered commits, with coverage and the unknown band.

    ``coverage`` and ``agent_share`` are fractions in 0..1. ``agent_share`` is
    ``None`` with a ``suppressed_reason`` when coverage is below the floor.
    """

    total: int
    agent: int
    human_attested: int
    unknown: int
    coverage: float | None
    agent_share: float | None
    suppressed_reason: str | None

    def summary(self) -> str:
        """One line for compact surfaces (MCP, CLI)."""
        cov = f"{round((self.coverage or 0.0) * 100)}%"
        if self.agent_share is None:
            return f"withheld: coverage {cov} is below {round(COVERAGE_FLOOR * 100)}%"
        return f"at least {round(self.agent_share * 100)}% of covered commits, coverage {cov}"


def attribution_share(agent: int, human_attested: int, total: int) -> AttributionShare:
    """The honest AI share over *total* commits.

    Known sources never exceed *total*; a caller with inconsistent counts (rows
    from an older index) gets them clamped rather than a share above 100%.
    """
    total = max(int(total or 0), 0)
    agent = min(max(int(agent or 0), 0), total)
    human_attested = min(max(int(human_attested or 0), 0), total - agent)
    known = agent + human_attested
    if total == 0:
        return AttributionShare(0, 0, 0, 0, None, None, SUPPRESSED_NO_COMMITS)
    coverage = known / total
    share: float | None = agent / known if known else None
    reason = None
    if coverage < COVERAGE_FLOOR:
        share, reason = None, SUPPRESSED_LOW_COVERAGE
    return AttributionShare(
        total=total,
        agent=agent,
        human_attested=human_attested,
        unknown=total - known,
        coverage=coverage,
        agent_share=share,
        suppressed_reason=reason,
    )


def file_share(
    agent_commit_count: int | None, known_count: int | None, total: int | None
) -> AttributionShare:
    """:func:`attribution_share` from a ``git_metadata`` row's stored counts.

    A row indexed before the known count existed has it at 0; its agent commits
    are still known, so the known count never reads below them.
    """
    agent = int(agent_commit_count or 0)
    known = max(int(known_count or 0), agent)
    return attribution_share(agent, known - agent, int(total or 0))
