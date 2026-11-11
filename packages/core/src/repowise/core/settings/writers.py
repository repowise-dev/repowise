"""Changing a setting: validate it through the registry, then hand it to its writer.

Every writer has one shape, ``writer(root, key, value) -> None``, where *value*
is already validated and ``None`` means "back to the default". A setting names
its area's writer in :attr:`Setting.writer` when the area owns one (decisions
re-resolve a whole policy, the LLM selection keeps provider and model together);
every other file setting is one key in its YAML file, written by
:func:`write_file_value`.

A file that does not parse is never written: the writer would start from an
empty mapping and replace every other key with nothing.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from repowise.core.settings import registry
from repowise.core.settings.model import Resolved, Setting, SettingError
from repowise.core.settings.resolve import resolve, validate

__all__ = [
    "Change",
    "EnvOnlyError",
    "current",
    "parse_text",
    "read_config",
    "reset_value",
    "set_value",
    "write_file_value",
]


class EnvOnlyError(SettingError):
    """The setting lives in an environment variable; no file holds it."""


@dataclass(frozen=True, slots=True)
class Change:
    """What one ``set`` or ``reset`` did: the setting before and after.

    ``also`` lists other settings in the same area the write moved with it
    (a decisions mode resets the switches under it; a new provider drops a
    model meant for the old one).
    """

    setting: Setting
    before: Resolved
    after: Resolved
    also: tuple[tuple[Setting, Resolved, Resolved], ...] = field(default=())

    @property
    def changed(self) -> bool:
        return self.before.value != self.after.value or bool(self.also)


def _file_path(root: Path, setting: Setting) -> Path:
    path = setting.path
    assert path is not None
    if path.startswith("~/"):
        return Path.home() / path[2:]
    return Path(root) / path


def read_config(root: Path | str, setting: Setting) -> dict[str, Any]:
    """The mapping *setting*'s file holds, ``{}`` when it is absent.

    Raises :class:`~repowise.core.repo_config.RepoConfigError` for a repo
    config that does not parse.
    """
    if setting.path is None:
        return {}
    path = _file_path(Path(root), setting)
    if path.suffix == ".json":
        # The per-machine state file: its own reader treats a damaged file as
        # empty, so this does too.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    from repowise.core.repo_config import load_repo_config

    return load_repo_config(root)


def current(
    root: Path | str, setting: Setting | str, *, config: dict[str, Any] | None = None
) -> Resolved:
    """*setting*'s effective value in *root*, reading its file unless given."""
    s = registry.get(setting) if isinstance(setting, str) else setting
    return resolve(s, read_config(root, s) if config is None else config)


def parse_text(setting: Setting, text: str) -> Any:
    """A command-line value as the setting's type, before validation.

    Text settings take the text as it is. Lists take ``a,b`` or a YAML list;
    numbers, switches and mappings are read as YAML (``true``, ``12``,
    ``{a: 1}``). The registry's check then decides.
    """
    if setting.type == "str":
        return text
    if setting.type == "list" and not text.lstrip().startswith("["):
        return [item.strip() for item in text.split(",") if item.strip()]
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def _refuse_env_only(setting: Setting) -> None:
    if setting.scope != "env":
        return
    raise EnvOnlyError(
        f"{setting.key} is read from the environment only; no file holds it. "
        f"Set it in the shell that starts repowise: `export {setting.key}=<value>` "
        f'(PowerShell: `$env:{setting.key} = "<value>"`), and unset it to go back '
        "to the default."
    )


def _writer(setting: Setting):
    if setting.writer is None:
        return write_file_value
    module_name, _, attr = setting.writer.partition(":")
    return getattr(import_module(module_name), attr)


def _write(root: Path, setting: Setting, value: Any) -> Change:
    neighbours = [s for s in registry.settings_in(setting.area) if s.scope != "env"]

    def snapshot() -> dict[str, Resolved]:
        files: dict[str | None, dict[str, Any]] = {}
        out = {}
        for s in neighbours:
            if s.path not in files:
                files[s.path] = read_config(root, s)
            out[s.key] = resolve(s, files[s.path])
        return out

    before = snapshot()
    _writer(setting)(root, setting.key, value)
    after = snapshot()
    also = tuple(
        (s, before[s.key], after[s.key])
        for s in neighbours
        if s.key != setting.key and before[s.key].value != after[s.key].value
    )
    return Change(setting, before[setting.key], after[setting.key], also)


def set_value(root: Path | str, key: str, raw: Any) -> Change:
    """Validate *raw* for *key* and write it. ``ValueError`` says why not.

    Raises :class:`EnvOnlyError` for a setting only the environment sets,
    ``KeyError`` for an unknown key and ``RepoConfigError`` when the file it
    would change does not parse.
    """
    setting = registry.get(key)
    _refuse_env_only(setting)
    if raw is None:
        raise ValueError(f"{key}: give a value, or use reset to go back to the default")
    try:
        value = validate(setting, raw)
    except ValueError as exc:
        raise ValueError(f"{key}: {exc}") from None
    return _write(Path(root), setting, value)


def reset_value(root: Path | str, key: str) -> Change:
    """Put *key* back to its default by removing what its file says."""
    setting = registry.get(key)
    _refuse_env_only(setting)
    return _write(Path(root), setting, None)


def write_file_value(root: Path | str, key: str, value: Any) -> None:
    """Set dotted *key* to *value* in ``.repowise/config.yaml``, or remove it for ``None``.

    Every other key is kept. Removing the last key of a block drops the block,
    so a reset leaves the file as it was before the set. A file that does not
    parse raises and is left as it is.
    """
    from repowise.core.repo_config import load_repo_config, save_repo_config

    config = load_repo_config(root)
    updated = copy.deepcopy(config)
    parts = key.split(".")
    if value is None:
        _remove(updated, parts)
    else:
        node = updated
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    if updated != config:
        save_repo_config(root, updated)


def _remove(node: dict[str, Any], parts: list[str]) -> None:
    head, *rest = parts
    if not rest:
        node.pop(head, None)
        return
    child = node.get(head)
    if isinstance(child, dict):
        _remove(child, rest)
        if not child:
            node.pop(head)
