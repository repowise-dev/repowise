"""The Voyage AI embedder has to be selectable, not just registered.

``_BUILTIN_EMBEDDERS`` is what the resolver builds from, but the two
interactive pickers spell their own choices out, which is how ``edenai``
ended up registered and documented yet unselectable (see
``test_edenai_reachability.py``). Both lists are hand-typed, so a test holds
them to the registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.init_cmd.command import init_command
from repowise.cli.commands.reindex_cmd import reindex_command
from repowise.cli.ui import mode_selection

_DETECTION_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_EMBEDDING_MODEL",
    "EDENAI_API_KEY",
    "VOYAGE_API_KEY",
)


@pytest.fixture(autouse=True)
def _home_this_test_owns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The last detection tier reads ``~/.repowise/config.yaml``.

    Without this the assertions below read the author's real one.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))


class _NullConsole:
    """Same stand-in the existing UI tests use: the prompts are what matter."""

    def print(self, *_args: object, **_kwargs: object) -> None:
        pass


def _clear_detection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _DETECTION_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    ("command", "param"),
    [(init_command, "embedder_name"), (reindex_command, "embedder")],
    ids=lambda value: getattr(value, "name", value),
)
def test_the_flag_accepts_voyage(command, param: str) -> None:
    ctx = command.make_context(command.name or "cmd", ["--embedder", "voyage"])
    assert ctx.params[param] == "voyage"


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(
            lambda: mode_selection.interactive_advanced_config(_NullConsole()),
            id="advanced-config",
        ),
        pytest.param(
            lambda: mode_selection._prompt_index_only_search(_NullConsole(), {}),
            id="index-only-search",
        ),
    ],
)
def test_both_pickers_offer_voyage(monkeypatch: pytest.MonkeyPatch, run) -> None:
    offered: list[str] = []

    def _capture(_text, *_a, **kwargs):
        choice_type = kwargs.get("type")
        if choice_type is not None and hasattr(choice_type, "choices"):
            offered.extend(choice_type.choices)
        return kwargs.get("default", "")

    monkeypatch.setattr(mode_selection.click, "prompt", _capture)
    monkeypatch.setattr(mode_selection.click, "confirm", lambda *_a, **k: k.get("default", False))
    _clear_detection_env(monkeypatch)
    run()

    assert "voyage" in offered


def test_the_key_alone_resolves_to_voyage(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_detection_env(monkeypatch)
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-key")
    assert mode_selection._resolve_embedder_from_env() == "voyage"


def test_no_key_still_resolves_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The new key must not change the default for a machine without it."""
    _clear_detection_env(monkeypatch)
    assert mode_selection._resolve_embedder_from_env() == "mock"
