"""The setting tables in docs/reference/CONFIG.md stay true to the registry.

The golden is the file on disk, rendered by ``scripts/gen_config_docs.py``.
The other tests hold the generator to its promises: a user-facing setting no
table shows is an error, internal settings stay out, and a variable named in
the prose is one repowise reads.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from repowise.core.settings import AREAS, all_settings, by_env

ROOT = Path(__file__).resolve().parents[3]
FIX = "Run: python scripts/gen_config_docs.py"


def _generator():
    """Load the generator by path; ``scripts/`` is not an importable package."""
    path = ROOT / "scripts" / "gen_config_docs.py"
    spec = importlib.util.spec_from_file_location("gen_config_docs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_config_docs"] = module
    spec.loader.exec_module(module)
    return module


GEN = _generator()


def _on_disk() -> str:
    return GEN.OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_config_md_matches_the_registry() -> None:
    assert GEN.render(_on_disk()) == _on_disk(), (
        f"docs/reference/CONFIG.md disagrees with the settings registry. {FIX}"
    )


def test_check_mode_passes_on_a_clean_tree() -> None:
    assert GEN.main(["--check"]) == 0


def test_check_mode_fails_and_names_the_fix_when_a_table_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = _on_disk().replace("| `enabled` | `true` |", "| `enabled` | `false` |", 1)
    copy = tmp_path / "CONFIG.md"
    copy.write_text(stale, encoding="utf-8")
    monkeypatch.setattr(GEN, "OUTPUT", copy)
    monkeypatch.setattr(GEN, "ROOT", tmp_path)

    assert GEN.main(["--check"]) == 1
    assert FIX in capsys.readouterr().err
    assert copy.read_text(encoding="utf-8") == stale

    assert GEN.main([]) == 0
    assert copy.read_text(encoding="utf-8") == _on_disk()


def _generated_regions(text: str) -> str:
    found = re.findall(r"<!-- BEGIN GENERATED settings:.*?<!-- END GENERATED", text, re.S)
    return "\n".join(found)


def test_no_internal_setting_is_listed() -> None:
    regions = _generated_regions(_on_disk())
    for setting in all_settings():
        if not setting.internal:
            continue
        label = setting.key if setting.scope == "env" else setting.key.rsplit(".", 1)[-1]
        assert f"| `{label}` |" not in regions, f"{setting.key} is internal and must not be listed"


def test_a_setting_no_table_shows_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.settings import Setting

    orphan = Setting(key="nowhere.knob", type="bool", default=False, description="x", area="health")
    monkeypatch.setattr(GEN, "user_facing", lambda: (orphan,))
    with pytest.raises(ValueError, match=r"nowhere.knob"):
        GEN.rendered_blocks()


def test_every_area_has_a_variable_table_slot() -> None:
    assert set(GEN._ENV_AREA_ORDER) == set(AREAS)


def test_every_repowise_variable_named_in_config_md_is_registered() -> None:
    """A renamed or removed variable cannot linger in the hand-written prose."""
    names = set(re.findall(r"\bREPOWISE_[A-Z0-9_]*[A-Z0-9]\b", _on_disk()))
    unknown = sorted(n for n in names if by_env(n) is None)
    assert not unknown, f"CONFIG.md names variables repowise does not read: {unknown}"
