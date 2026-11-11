"""The settings registry: shape, uniqueness, serialisation and import cost."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from repowise.core import settings
from repowise.core.settings import registry
from repowise.core.settings.model import Env, Setting

AREAS_DIR = Path(registry.__file__).parent / "areas"


def test_every_area_file_is_listed():
    files = {p.stem for p in AREAS_DIR.glob("*.py") if not p.stem.startswith("_")}
    assert files == set(registry.AREAS), (
        "Each module in core/settings/areas/ must be named in registry.AREAS "
        f"(missing: {sorted(files - set(registry.AREAS))})"
    )


def test_keys_and_env_names_are_unique_and_well_formed():
    seen_env: set[str] = set()
    for s in settings.all_settings():
        assert s.description.strip(), f"{s.key} needs a description"
        if s.env is not None:
            assert s.env.name.startswith("REPOWISE_"), s.key
            assert s.env.name not in seen_env, s.env.name
            seen_env.add(s.env.name)
        if s.scope == "env":
            assert s.path is None
        else:
            assert s.path, s.key
        if s.choices and s.default is not None and s.type == "str":
            assert s.default in s.choices, f"{s.key}: default not among its choices"
        if s.on_invalid == "fallback":
            assert s.fallback != s.default, f"{s.key}: a fallback equal to the default is 'default'"


def test_to_dict_is_json_ready():
    payload = [s.to_dict() for s in settings.all_settings()]
    json.dumps(payload)
    resolved = settings.resolve("decisions.mode", {})
    json.dumps(resolved.to_dict())
    assert resolved.to_dict()["source"] == "default"


def test_get_and_lookup_helpers():
    assert settings.get("decisions.mode").area == "decisions"
    with pytest.raises(KeyError, match=r"no\.such"):
        settings.get("no.such")
    assert settings.by_env("REPOWISE_HOOK_READ_SKELETON").key == "hooks.read_skeleton"
    assert settings.by_env("REPOWISE_SEAM_AUTHENTICATOR").key == "REPOWISE_SEAM_<NAME>"
    assert settings.by_env("REPOWISE_NOT_A_THING") is None
    assert {s.area for s in settings.settings_in("hooks")} == {"hooks"}
    user_facing = settings.all_settings(include_internal=False)
    assert user_facing and not any(s.internal for s in user_facing)


def test_setting_rejects_contradictions():
    with pytest.raises(ValueError, match="choices"):
        Setting("x", "bool", False, "X.", "a", choices=("a",))
    with pytest.raises(ValueError, match="bounds"):
        Setting("x", "str", "", "X.", "a", minimum=1)
    with pytest.raises(ValueError, match="keyed by its variable"):
        Setting("x", "str", "", "X.", "a", scope="env", env=Env("REPOWISE_X"))


def test_importing_the_package_loads_no_area_and_nothing_heavy():
    """Hooks may import this; it must not drag yaml, structlog or the areas in."""
    code = (
        "import sys; import repowise.core.settings as s; "
        "heavy = [m for m in ('yaml', 'structlog', 'sqlalchemy', "
        "'repowise.core.settings.areas.decisions') if m in sys.modules]; "
        "print(','.join(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == ""


def test_loading_every_area_stays_light():
    """Collecting all settings must not import a logging stack or a database."""
    code = (
        "import sys; import repowise.core.settings as s; s.all_settings(); "
        "heavy = [m for m in ('structlog', 'sqlalchemy', 'yaml') if m in sys.modules]; "
        "print(','.join(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == ""


SETTING_KEYS = {
    "schema_version": int,
    "key": str,
    "type": str,
    "default": object,
    "default_note": (str, type(None)),
    "description": str,
    "area": str,
    "scope": str,
    "file": (str, type(None)),
    "choices": list,
    "minimum": (int, float, type(None)),
    "maximum": (int, float, type(None)),
    "exclusive_minimum": bool,
    "out_of_range": str,
    "env": (dict, type(None)),
    "internal": bool,
    "reload": str,
}
RESOLVED_KEYS = {"schema_version": int, "value": object, "source": str, "problem": (str, type(None)), "locked": bool}


def test_to_dict_shapes_are_pinned():
    """The settings API and docs read these; removing or retyping a key bumps SCHEMA_VERSION."""
    assert settings.SCHEMA_VERSION == 1
    for s in settings.all_settings():
        d = s.to_dict()
        assert set(d) == set(SETTING_KEYS), s.key
        for key, kind in SETTING_KEYS.items():
            assert isinstance(d[key], kind), (s.key, key)
        if s.type == "list" and d["default"] is not None:
            assert isinstance(d["default"], list), s.key
    r = settings.resolve("editor_files.sections", {}).to_dict()
    assert set(r) == set(RESOLVED_KEYS)
    assert isinstance(r["value"], list)
    r = settings.resolve("decisions.harnesses", {}).to_dict()
    assert isinstance(r["value"], list)


def test_an_area_with_its_own_resolver_cannot_declare_env(monkeypatch):
    import types

    fake = types.SimpleNamespace(
        SETTINGS=(Setting("x.y", "bool", False, "X.", "fake", env=Env("REPOWISE_X")),),
        resolve_setting=lambda s, c: None,
    )
    monkeypatch.setattr(registry, "AREAS", ("fake",))
    monkeypatch.setattr(registry, "import_module", lambda name: fake)
    with pytest.raises(ValueError, match="cannot declare an env var"):
        registry._collect()


def test_factory_scope_is_explicit():
    from repowise.core.settings.areas._common import factory

    env = factory("server", scope="env")("REPOWISE_X", "bool", False, "X.", env_true=frozenset({"y"}))
    assert (env.scope, env.env.name, env.reload) == ("env", "REPOWISE_X", "restart")
    with pytest.raises(ValueError, match="env-scoped"):
        factory("server")("x", "bool", False, "X.", env_true=frozenset({"y"}))
    repo = factory("indexing")("REPOWISE_LOOKS_LIKE_ENV", "str", None, "X.")
    assert repo.scope == "repo" and repo.env is None
