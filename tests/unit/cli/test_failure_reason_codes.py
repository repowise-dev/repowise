"""Every failure `init` can die on names a reason, not just a class.

`ClickException` carries a message and nothing else, so a failed `init` recorded
one indistinguishable outcome for a bad path, a cost gate with no terminal to
confirm on, and a hand-edited editor config that will not parse. These pin the
reason on each site.

The reason travels on the exception as an attribute, deliberately not as a
subclass, so `error_type` keeps reporting `ClickException` and the histogram this
feeds stays comparable across the change. The root group is what records it, and
only when the exception actually ends the command - see `test_telemetry.py` for
that half.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import click
import pytest


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(click.ClickException) as excinfo:
        fn(*args, **kwargs)
    # Not a subclass: the class name is what telemetry reports, and it must not
    # move just because a site gained a reason.
    assert type(excinfo.value) is click.ClickException
    return excinfo.value.reason


def test_a_cost_gate_with_no_terminal_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scripted-agent case: no tty to confirm on and no --yes.

    A coding agent running plain `repowise init` on a repo over the gate hits
    this, and it is indistinguishable from every other init failure today.
    """
    from repowise.cli import cost_gate

    monkeypatch.setattr(cost_gate.sys.stdin, "isatty", lambda: False, raising=False)
    est = SimpleNamespace(estimated_cost_usd=99.0, cost_range=None)

    assert _reason(cost_gate.cost_gate_blocks, est, yes=False, message="?") == "cost_gate_no_tty"


def test_the_cost_gate_still_lets_a_cheap_or_approved_run_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: instrumenting the refusal must not create one."""
    from repowise.cli import cost_gate

    monkeypatch.setattr(cost_gate.sys.stdin, "isatty", lambda: False, raising=False)
    cheap = SimpleNamespace(estimated_cost_usd=0.01, cost_range=None)
    dear = SimpleNamespace(estimated_cost_usd=99.0, cost_range=None)

    assert cost_gate.cost_gate_blocks(cheap, yes=False, message="?") is False
    assert cost_gate.cost_gate_blocks(dear, yes=True, message="?") is False


def test_an_unknown_reasoning_mode_says_so() -> None:
    from repowise.cli.helpers import resolve_reasoning

    assert _reason(resolve_reasoning, "not-a-mode") == "invalid_reasoning"


def test_a_model_that_does_not_offer_the_mode_says_so_differently() -> None:
    """Distinct from an unparseable mode: this one parsed, the model lacks it.

    Worth its own code because the fix differs - drop the flag, versus pick a
    different model.
    """
    from repowise.cli.ui.provider_selection import _select_reasoning_mode

    selected = SimpleNamespace(model="tiny", reasoning_modes=("auto",))

    assert _reason(_select_reasoning_mode, None, selected, "high") == "unsupported_reasoning"


def test_an_unparseable_mode_says_so_from_the_selection_path_too() -> None:
    """The same failure as `resolve_reasoning`, so it carries the same code."""
    from repowise.cli.ui.provider_selection import _select_reasoning_mode

    selected = SimpleNamespace(model="tiny", reasoning_modes=("auto", "high"))

    assert _reason(_select_reasoning_mode, None, selected, "not-a-mode") == "invalid_reasoning"


def test_an_unparseable_editor_config_says_so(tmp_path: Path) -> None:
    """A hand-edited `.mcp.json` that will not parse is a real init failure."""
    from repowise.cli.agent_targets.formats.json_merge import load_json_object

    config = tmp_path / ".mcp.json"
    config.write_text("{not json", encoding="utf-8")

    assert _reason(load_json_object, config) == "editor_config_malformed"


def test_an_editor_config_of_the_wrong_shape_says_so(tmp_path: Path) -> None:
    from repowise.cli.agent_targets.formats.json_merge import load_json_object

    config = tmp_path / ".mcp.json"
    config.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert _reason(load_json_object, config) == "editor_config_malformed"


def test_an_unreadable_editor_config_says_so_differently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permissions, not content. The user's fix differs, so the code does too."""
    from repowise.cli.agent_targets.formats.json_merge import load_json_object

    config = tmp_path / ".mcp.json"
    config.write_text("{}", encoding="utf-8")

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", denied)

    assert _reason(load_json_object, config) == "editor_config_unreadable"


def test_an_unparseable_toml_config_says_so(tmp_path: Path) -> None:
    from repowise.cli.agent_targets.formats.toml_merge import load_toml_document

    assert _reason(load_toml_document, tmp_path / "config.toml", "= broken") == (
        "editor_config_malformed"
    )


def test_a_merge_that_would_corrupt_toml_says_so(tmp_path: Path) -> None:
    """Distinct from a malformed file: theirs parses, ours would not.

    Worth its own code because the fix is different - the user's file is fine
    and repowise is the one that cannot write it.
    """
    from repowise.cli.agent_targets.formats.toml_merge import ensure_valid_toml

    assert _reason(ensure_valid_toml, "= broken", tmp_path / "config.toml") == (
        "editor_config_unmergeable"
    )


def test_a_config_table_of_the_wrong_type_says_so(tmp_path: Path) -> None:
    from repowise.cli.agent_targets.formats.toml_merge import require_table

    assert (
        _reason(require_table, {"mcp_servers": "not a table"}, "mcp_servers", tmp_path, "mcp")
        == "editor_config_malformed"
    )


def test_a_hooks_file_of_the_wrong_shape_says_so(tmp_path: Path) -> None:
    """Reached from `init` when the editor is codex and hooks.json is hand-edited."""
    from repowise.cli.agent_targets.targets.codex import project_hooks_path, write_hooks_config

    hooks = project_hooks_path(tmp_path)
    hooks.parent.mkdir(parents=True)
    hooks.write_text(json.dumps({"hooks": "not an object"}), encoding="utf-8")

    assert _reason(write_hooks_config, tmp_path) == "editor_config_malformed"


def test_a_hooks_event_of_the_wrong_shape_says_so(tmp_path: Path) -> None:
    from repowise.cli.agent_targets.targets.codex import (
        hooks_config,
        project_hooks_path,
        write_hooks_config,
    )

    event = next(iter(hooks_config()["hooks"]))
    hooks = project_hooks_path(tmp_path)
    hooks.parent.mkdir(parents=True)
    hooks.write_text(json.dumps({"hooks": {event: "not a list"}}), encoding="utf-8")

    assert _reason(write_hooks_config, tmp_path) == "editor_config_malformed"


# --- coverage of the sweep itself -------------------------------------------


#: Paths the sweep covered end to end. Every `init`-reachable raise in these now
#: names a reason, so a new bare one is a regression rather than an omission.
_SWEPT = (
    "packages/cli/src/repowise/cli/agent_targets",
    "packages/cli/src/repowise/cli/cost_gate.py",
    "packages/cli/src/repowise/cli/ui/provider_selection.py",
)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return next(p for p in here.parents if (p / "packages").is_dir())


def _bare_click_exception_raises(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "ClickException":
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_no_bare_click_exception_is_left_on_the_swept_paths() -> None:
    """The sweep as a property, rather than a list I remembered to update.

    Pinning each site by hand cannot say a NEW raise was added without a reason,
    which is exactly how two sites in `codex.py` went missing the first time
    through. This fails on the next one.
    """
    root = _repo_root()
    offenders: list[str] = []
    for entry in _SWEPT:
        target = root / entry
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in files:
            offenders.extend(_bare_click_exception_raises(path))

    assert offenders == []
