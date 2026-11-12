"""The Settings rows: config.yaml and the environment, read through the settings registry.

Every value is resolved the way repowise reads it (``repowise.core.settings``),
so a value doctor calls invalid is one the code ignores, and the fix is a
``repowise config`` command. One passing row when nothing is wrong; otherwise
a row per invalid value (fail), per key the registry does not know (warn) and
per environment variable that decides against the file (ok, for information).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

from .._types import DoctorCheck, _check
from .context import CheckContext

if TYPE_CHECKING:
    from repowise.core.settings import Resolved, Setting

NAME = "Settings"
_FILE = ".repowise/config.yaml"


def _file_settings() -> list[Setting]:
    """Every setting kept in config.yaml, with each ``<provider>`` family spelled out."""
    from repowise.core.settings import all_settings, get
    from repowise.core.settings.registry import family_members

    out = []
    for s in all_settings():
        if s.path != _FILE:
            continue
        if s.members is None:
            out.append(s)
            continue
        slot = next(p for p in s.key.split(".") if p.startswith("<"))
        out.extend(get(s.key.replace(slot, m)) for m in family_members(s))
    return out


def _row(
    subject: str, ok: bool, detail: str, *, warn: bool = False, fix: str | None = None
) -> DoctorCheck:
    """A row whose id names the key or variable it is about: ``setting-decisions-mode``."""
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-") or "value"
    return _check(NAME, ok, detail, check_id=f"setting-{slug}", warn=warn, fix=fix)


def _problem(
    setting: Setting, config: Mapping[str, Any], environ: Mapping[str, str]
) -> tuple[Resolved | None, str | None]:
    """``(resolved, problem)`` for one layer, a setting that raises read as its problem."""
    from repowise.core.settings import SettingError, resolve

    try:
        resolved = resolve(setting, config, environ=environ)
    except SettingError as exc:
        return None, str(exc)
    return resolved, resolved.problem


def _shown(value: Any) -> str:
    return json.dumps(value, default=str)


def _reason(setting: Setting, problem: str) -> str:
    return problem.removeprefix(f"{setting.key}: ").rstrip(".")


def _file_row(setting: Setting, raw: Any, problem: str) -> tuple[DoctorCheck, str | None]:
    """A fail row for a config.yaml value the code does not use; the key repair may reset."""
    from repowise.core.settings.resolve import MISSING

    if raw is MISSING:
        # A whole block that is unreadable (``decisions.sources: 3``): no one
        # key to reset, so the file is fixed by hand.
        return _row(setting.key, False, f"{problem} Fix it in {_FILE}."), None
    key = setting.key
    detail = (
        f"{key}: {_shown(raw)} in {_FILE} is not valid: {_reason(setting, problem)}. "
        f"Accepts {setting.accepts}. Run 'repowise config set {key} <value>' or "
        f"'repowise config reset {key}'"
    )
    return _row(key, False, detail, fix=f"repowise config reset {key}"), key


def _env_row(setting: Setting, name: str, raw: str, problem: str) -> DoctorCheck:
    detail = (
        f"{name}={_shown(raw)} in the environment is not valid: {_reason(setting, problem)}. "
        f"Accepts {setting.accepts}. Unset {name} or set it to a value it accepts"
    )
    return _row(name, False, detail)


def _override_row(setting: Setting, file_value: Any, env_value: Any) -> DoctorCheck:
    assert setting.env is not None
    name = setting.env.name
    if setting.env.wins:
        detail = (
            f"{name}={_shown(env_value)} overrides {setting.key}: {_shown(file_value)} "
            f"in {_FILE} until it is unset"
        )
    else:
        detail = (
            f"{name}={_shown(env_value)} is set, but {setting.key}: {_shown(file_value)} "
            f"in {_FILE} wins over it"
        )
    return _row(name, True, detail)


def _value_rows(
    settings: list[Setting], config: Mapping[str, Any], environ: Mapping[str, str]
) -> tuple[list[DoctorCheck], list[str]]:
    """Rows for invalid file and env values and env overrides, and the keys to reset."""
    from repowise.core.settings import Source
    from repowise.core.settings.resolve import MISSING, lookup

    rows: list[DoctorCheck] = []
    resettable: list[str] = []
    seen: set[str] = set()
    for s in settings:
        raw = lookup(config, s.key)
        from_file, file_problem = _problem(s, config, {})
        # The decisions area names a block-level problem on every key in the block.
        if file_problem and file_problem not in seen:
            seen.add(file_problem)
            row, key = _file_row(s, raw, file_problem)
            rows.append(row)
            if key:
                resettable.append(key)
        if s.env is None or s.env.prefix or s.env.name not in environ:
            continue
        env_raw = environ[s.env.name]
        from_env, env_problem = _problem(s, {}, {s.env.name: env_raw})
        if env_problem:
            rows.append(_env_row(s, s.env.name, env_raw, env_problem))
        elif (
            raw is not MISSING
            and not file_problem
            and from_env is not None
            and from_env.source is Source.ENV
            and from_file is not None
            and from_env.value != from_file.value
        ):
            rows.append(_override_row(s, from_file.value, from_env.value))
    return rows, resettable


def _env_only_rows(environ: Mapping[str, str]) -> list[DoctorCheck]:
    """Invalid values of the settings only an environment variable sets."""
    from repowise.core.settings import all_settings

    rows = []
    for s in all_settings():
        if s.scope != "env" or s.env is None or s.env.prefix or s.env.name not in environ:
            continue
        _resolved, problem = _problem(s, {}, {s.env.name: environ[s.env.name]})
        if problem:
            rows.append(_env_row(s, s.env.name, environ[s.env.name], problem))
    return rows


def _unknown_keys(
    node: Mapping[str, Any], prefix: str, known: set[str], blocks: set[str]
) -> list[tuple[str, bool]]:
    """``(path, is_block_scalar)`` for each config.yaml key no setting reads.

    A known key is not descended into: a mapping setting's keys are its value.
    A block written as one value (``events: true``) is reported as such.
    """
    out: list[tuple[str, bool]] = []
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if path in known:
            continue
        if path not in blocks:
            out.append((path, False))
        elif isinstance(value, Mapping):
            out.extend(_unknown_keys(value, path, known, blocks))
        elif value is not None:
            out.append((path, True))
    return out


def _judges_itself(path: str, settings: list[Setting]) -> bool:
    """Whether the block at *path* belongs to an area with its own resolver.

    That area reads its whole block and names a malformed one itself
    (``decisions.sources.pr: true`` is its short form; ``decisions: 3`` its
    problem), so the value rows already carry what is wrong there.
    """
    from repowise.core.settings.registry import area_resolver

    area = next(s.area for s in settings if s.key.startswith(f"{path}."))
    return area_resolver(area) is not None


def _unknown_rows(config: Mapping[str, Any], settings: list[Setting]) -> list[DoctorCheck]:
    known = {s.key for s in settings}
    blocks = {k.rsplit(".", i)[0] for k in known for i in range(1, k.count(".") + 1)}
    rows = []
    for path, block_scalar in _unknown_keys(config, "", known, blocks):
        if block_scalar:
            if _judges_itself(path, settings):
                continue
            under = sorted(k for k in known if k.startswith(f"{path}."))
            listed = ", ".join(under[:3]) + (", ..." if len(under) > 3 else "")
            detail = (
                f"{path} in {_FILE} is a block of settings ({listed}), "
                "not one value, so the settings under it keep their defaults"
            )
        else:
            # Never the block the key sits in: ``events.kinds.nope`` is not a typo of ``events.kinds``.
            options = sorted(k for k in known | blocks if not path.startswith(f"{k}."))
            near = get_close_matches(path, options, n=3, cutoff=0.8)
            hint = f"; did you mean {', '.join(near)}?" if near else ""
            detail = (
                f"{path} in {_FILE} is not a setting repowise reads, so it does nothing{hint}"
            )
        rows.append(_row(path, True, detail, warn=True))
    return rows


def _passing_row(config: Mapping[str, Any], settings: list[Setting]) -> DoctorCheck:
    from repowise.core.settings.resolve import MISSING, lookup

    written = sum(lookup(config, s.key) is not MISSING for s in settings)
    detail = f"{written} set in {_FILE}, all valid" if written else "every setting at its default"
    return _check(NAME, True, detail, check_id="settings")


def _unique(rows: list[DoctorCheck]) -> list[DoctorCheck]:
    """Ids kept unique within the run when two subjects share a slug (``a.b``, ``a_b``)."""
    used: set[str] = set()
    out = []
    for row in rows:
        check_id, n = row.id, 1
        while check_id in used:
            n += 1
            check_id = f"{row.id}-{n}"
        used.add(check_id)
        out.append(row._replace(id=check_id))
    return out


def settings_rows(ctx: CheckContext) -> list[DoctorCheck]:
    """The Settings rows; fills ``ctx.invalid_settings`` with the keys ``--repair`` may reset."""
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        config = load_repo_config(ctx.repo_path)
    except RepoConfigError as exc:
        return [_check(NAME, False, f"{exc}; fix the file by hand", check_id="settings")]
    try:
        settings = _file_settings()
        environ = dict(os.environ)
        rows, ctx.invalid_settings = _value_rows(settings, config, environ)
        rows += _env_only_rows(environ)
        rows += _unknown_rows(config, settings)
    except Exception as exc:
        return [
            _check(NAME, True, f"could not check them ({exc})", check_id="settings", warn=True)
        ]
    if any(r.level != "ok" for r in rows):
        return _unique(rows)
    return [_passing_row(config, settings), *_unique(rows)]

