"""The Eden AI embedder has to be reachable from `repowise init`, not just registered.

``docs/reference/CLI_REFERENCE.md`` documents ``--embedder edenai``, and the
embedder itself is in the registry, but the flag's ``click.Choice`` and both
interactive pickers were left with the older five-backend list. The flag was
rejected outright, the pickers never offered the option, and a machine whose
only key is ``EDENAI_API_KEY`` defaulted to the mock, so semantic search ran on
vectors that cannot match the index.

Precedence matters as much as presence: ``EDENAI_API_KEY`` is read last, so a
key sitting in the environment for something else cannot outrank a backend the
user was already resolving to.
"""

from __future__ import annotations

import click
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
)


def _clear_detection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _DETECTION_KEYS:
        monkeypatch.delenv(key, raising=False)


class _NullConsole:
    """Same stand-in the existing UI tests use: the prompts are what matter."""

    def print(self, *_args: object, **_kwargs: object) -> None:
        pass


class TestEmbedderFlag:
    def test_init_accepts_edenai(self) -> None:
        ctx = init_command.make_context("init", ["--embedder", "edenai"])
        assert ctx.params["embedder_name"] == "edenai"

    def test_reindex_accepts_edenai(self) -> None:
        """`reindex` documents the same set as `init`, so it has to accept it too."""
        ctx = reindex_command.make_context("reindex", ["--embedder", "edenai"])
        assert ctx.params["embedder"] == "edenai"

    @pytest.mark.parametrize("command", [init_command, reindex_command])
    def test_an_unknown_backend_is_still_rejected(self, command: click.Command) -> None:
        with pytest.raises(click.UsageError):
            command.make_context(command.name or "cmd", ["--embedder", "not-a-backend"])


class TestInteractivePickers:
    """Whichever prompt asks for an embedder must list every real backend.

    The two prompts are written separately, one in advanced config and one in
    the index-only Search section, which is how they drifted apart.
    """

    @staticmethod
    def _offered_choices(monkeypatch: pytest.MonkeyPatch, run) -> list[str]:
        offered: list[str] = []

        def _capture(_text, *_a, **kwargs):
            choice_type = kwargs.get("type")
            if isinstance(choice_type, click.Choice):
                offered.extend(choice_type.choices)
            # Answer every prompt with its own default, like the existing UI
            # tests do: returning a coerced value breaks the numeric prompts.
            return kwargs.get("default", "")

        monkeypatch.setattr(mode_selection.click, "prompt", _capture)
        monkeypatch.setattr(
            mode_selection.click, "confirm", lambda *_a, **k: k.get("default", False)
        )
        _clear_detection_env(monkeypatch)
        run()
        return offered

    def test_advanced_config_offers_edenai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        offered = self._offered_choices(
            monkeypatch,
            lambda: mode_selection.interactive_advanced_config(_NullConsole()),
        )
        assert "edenai" in offered

    def test_index_only_search_offers_edenai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        offered = self._offered_choices(
            monkeypatch,
            lambda: mode_selection._prompt_index_only_search(_NullConsole(), {}),
        )
        assert "edenai" in offered


class TestEnvDetection:
    def test_the_key_alone_resolves_to_edenai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_detection_env(monkeypatch)
        monkeypatch.setenv("EDENAI_API_KEY", "x")
        assert mode_selection._resolve_embedder_from_env() == "edenai"

    def test_no_key_still_resolves_to_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_detection_env(monkeypatch)
        assert mode_selection._resolve_embedder_from_env() == "mock"

    @pytest.mark.parametrize(
        ("other_key", "other_value", "expected"),
        [
            ("GEMINI_API_KEY", "x", "gemini"),
            ("OPENAI_API_KEY", "x", "openai"),
            ("OPENROUTER_API_KEY", "x", "openrouter"),
            ("OLLAMA_EMBEDDING_MODEL", "embeddinggemma", "ollama"),
        ],
    )
    def test_edenai_never_outranks_an_incumbent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        other_key: str,
        other_value: str,
        expected: str,
    ) -> None:
        _clear_detection_env(monkeypatch)
        monkeypatch.setenv("EDENAI_API_KEY", "x")
        monkeypatch.setenv(other_key, other_value)
        assert mode_selection._resolve_embedder_from_env() == expected
