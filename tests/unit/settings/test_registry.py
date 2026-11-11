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
