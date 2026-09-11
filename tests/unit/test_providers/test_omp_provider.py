"""Unit tests for OmpProvider.

The subprocess is faked, but the event stream is not: the fixtures below are
shaped like real ``omp -p --mode json`` output -- a JSONL envelope of session,
agent and turn events wrapped around the assistant ``message_end`` that actually
carries the answer -- so the parser is exercised against the stream it meets in
production. No real ``omp`` process is ever spawned.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from repowise.core.providers.llm.base import ProviderError
from repowise.core.providers.llm.omp import (
    OmpProvider,
    _load_omp_model_catalog,
    _model_label,
    _normalize_model,
    _parse_events,
)
from repowise.core.reasoning import REASONING_MODES

# Usage exactly as a real run reports it: flat token counts alongside a nested
# per-bucket cost breakdown whose `total` is the only figure the provider keeps.
_USAGE = {
    "input": 2,
    "output": 9,
    "cacheRead": 59064,
    "cacheWrite": 1126,
    "totalTokens": 60201,
    "cost": {
        "input": 0.00001,
        "output": 0.000225,
        "cacheRead": 0.029532,
        "cacheWrite": 0.0070375,
        "total": 0.0368045,
    },
}


def _assistant_message(
    text: str,
    *,
    usage: dict[str, Any] | None = None,
    stop_reason: str = "stop",
    model: str = "claude-opus-5",
) -> dict[str, Any]:
    """A finished assistant message; ``usage=None`` omits the key entirely."""
    message: dict[str, Any] = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "provider": "ccs",
        "model": model,
        "stopReason": stop_reason,
    }
    if usage is not None:
        message["usage"] = usage
    return message


def _message_end(message: dict[str, Any]) -> dict[str, Any]:
    return {"type": "message_end", "message": message}


def _stream(*lines: dict[str, Any] | str) -> str:
    """Render *lines* as the JSONL omp writes; a ``str`` passes through raw."""
    return "\n".join(json.dumps(line) if isinstance(line, dict) else line for line in lines) + "\n"


class FakeOmpProcess:
    """Stands in for the ``omp -p`` subprocess.

    Records what was handed to ``communicate`` so a test can prove the prompt
    travelled on stdin rather than argv.
    """

    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.stdin_input: bytes | None = None
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_input = input
        return self._stdout.encode("utf-8"), self._stderr.encode("utf-8")

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture
def omp_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/omp" if cmd == "omp" else None)
    return "/usr/bin/omp"


def _spawn(monkeypatch, proc: FakeOmpProcess) -> FakeOmpProcess:
    """Hand *proc* to the provider in place of a real subprocess."""

    async def fake_exec(*_args: str, **_kwargs: Any) -> FakeOmpProcess:
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    return proc


# ---------------------------------------------------------------------------
# Model naming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # "let Oh My Pi choose" is the point of the provider, so every spelling
        # of the default resolves to "no --model at all".
        (None, None),
        ("omp/default", None),
        ("default", None),
        ("anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
        # A persisted label must shed its prefix, or the CLI is handed
        # "omp/anthropic/..." -- a selector no Oh My Pi install resolves.
        ("omp/anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
    ],
)
def test_model_normalization_strips_the_label_prefix(given, expected):
    assert _normalize_model(given) == expected


def test_a_persisted_label_round_trips_but_reaches_argv_unprefixed(omp_on_path, tmp_path):
    assert OmpProvider().model_name == "omp/default"
    assert OmpProvider(model="omp/default").model_name == "omp/default"
    assert _model_label("anthropic/claude-sonnet-4-5") == "omp/anthropic/claude-sonnet-4-5"

    provider = OmpProvider(model="omp/anthropic/claude-sonnet-4-5")
    assert provider.model_name == "omp/anthropic/claude-sonnet-4-5"

    cmd = provider._build_command(
        system_prompt_file=tmp_path / "system-prompt.md",
        config_file=tmp_path / "omp-config.yml",
    )
    assert cmd[cmd.index("--model") + 1] == "anthropic/claude-sonnet-4-5"


@pytest.mark.parametrize(
    "bad",
    [
        "anthropic/claude-sonnet-4-5; rm -rf /",
        "--config=/etc/passwd",
        "$(id)",
        "gpt 5.2",
    ],
)
def test_an_invalid_model_name_is_rejected_before_anything_is_spawned(
    omp_on_path, monkeypatch, bad
):
    """Model names reach argv, so they are validated against a safe charset."""

    async def never_spawn(*_args, **_kwargs):
        raise AssertionError("a rejected model must never reach a subprocess")

    monkeypatch.setattr("asyncio.create_subprocess_exec", never_spawn)

    with pytest.raises(ProviderError, match="Invalid model name"):
        OmpProvider(model=bad)


def test_a_role_selector_is_accepted(omp_on_path):
    """`@slow` is a real Oh My Pi role; the other CLI providers' charset drops it."""
    assert OmpProvider(model="@slow").model_name == "omp/@slow"


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_the_command_isolates_the_turn_from_the_users_own_omp_setup(omp_on_path, tmp_path):
    """Anything the CLI discovers for itself competes with the prompt and costs
    tokens; a leftover session per page is the other half of the bill."""
    system_prompt_file = tmp_path / "system-prompt.md"
    config_file = tmp_path / "omp-config.yml"
    cmd = OmpProvider()._build_command(
        system_prompt_file=system_prompt_file,
        config_file=config_file,
    )

    assert cmd[0] == omp_on_path
    # Print mode: answer the prompt on stdin, emit JSONL, exit.
    assert "-p" in cmd
    assert cmd[cmd.index("--mode") + 1] == "json"
    for flag in ("--no-session", "--no-tools", "--no-extensions", "--no-skills", "--no-rules"):
        assert flag in cmd, f"{flag} missing from {cmd}"

    # The isolation config overrides global settings a documentation run has no
    # business inheriting, so it has to actually be passed.
    assert cmd[cmd.index("--config") + 1] == str(config_file)
    # A system prompt carrying file context can exceed the per-argument length
    # limit, so it is handed over as a path rather than inline text.
    assert cmd[cmd.index("--system-prompt") + 1] == str(system_prompt_file)

    # No --model means "whatever Oh My Pi is configured to use".
    assert "--model" not in cmd


@pytest.mark.parametrize(
    ("reasoning", "expected"),
    [
        # `auto` must omit the flag entirely, or it overrides the user's own
        # configured thinking level with ours.
        ("auto", None),
        ("none", "off"),
        ("high", "high"),
    ],
)
def test_reasoning_maps_onto_the_thinking_flag(omp_on_path, tmp_path, reasoning, expected):
    cmd = OmpProvider()._build_command(
        system_prompt_file=tmp_path / "system-prompt.md",
        config_file=tmp_path / "omp-config.yml",
        reasoning=reasoning,
    )
    if expected is None:
        assert "--thinking" not in cmd
    else:
        assert cmd[cmd.index("--thinking") + 1] == expected


# ---------------------------------------------------------------------------
# Event stream parsing
# ---------------------------------------------------------------------------


def test_a_full_event_stream_yields_only_the_assistant_message():
    """Print mode wraps the answer in session/agent/turn events and echoes the
    user's own turn back as a `message_end` of its own. Counting that echo as
    content prepends the prompt to every page it writes."""
    stdout = _stream(
        {"type": "session", "session": {"id": "01JQZ", "title": "repowise"}},
        {"type": "agent_start", "agent": "main"},
        {"type": "message_start", "message": {"role": "assistant"}},
        _message_end({"role": "user", "content": [{"type": "text", "text": "user context"}]}),
        _message_end(
            _assistant_message("# Overview\n\nThe parser reads message_end.", usage=_USAGE)
        ),
        {"type": "turn_end", "turnId": "t-1"},
        {"type": "agent_end", "isTerminal": True},
    )

    content, usage = _parse_events(stdout)

    assert content == "# Overview\n\nThe parser reads message_end."
    assert (usage["input"], usage["output"]) == (2, 9)
    assert (usage["cacheRead"], usage["cacheWrite"]) == (59064, 1126)
    # Only the `total` bucket; summing the per-bucket figures double-counts it.
    assert usage["cost"] == pytest.approx(0.0368045)
    assert usage["resolved_model"] == "ccs/claude-opus-5"
    assert usage["stop_reason"] == "stop"
    assert usage["observed"] is True


def test_two_assistant_messages_accumulate_rather_than_overwrite():
    """MCP tools stay callable however this provider is configured, so a turn
    can end one message to call a tool and resume prose in the next. Keeping
    only the last silently truncates the page; keeping only the first drops the
    answer entirely."""
    stdout = _stream(
        _message_end(
            _assistant_message(
                "First half.",
                usage={"input": 10, "output": 4, "cost": {"total": 0.25}},
                stop_reason="tool_use",
            )
        ),
        _message_end(
            _assistant_message(
                "Second half.",
                usage={"input": 7, "output": 6, "cost": {"total": 0.75}},
                model="claude-sonnet-4-5",
            )
        ),
    )

    content, usage = _parse_events(stdout)

    assert content == "First half.\nSecond half."
    assert (usage["input"], usage["output"]) == (17, 10)
    assert usage["cost"] == pytest.approx(1.0)
    # The message that finished the turn is the one that describes the turn.
    assert usage["stop_reason"] == "stop"
    assert usage["resolved_model"] == "ccs/claude-sonnet-4-5"


def test_a_non_json_line_on_stdout_does_not_abort_the_parse():
    """The JS host occasionally writes a runtime warning to stdout, and a line
    can arrive truncated. Treating the stream as strictly JSONL would throw away
    a finished page over one stray line."""
    stdout = _stream(
        "warning: something",
        '{"type":"message_end","message":{"role":"assist',
        _message_end(_assistant_message("The page survived.", usage=_USAGE)),
    )

    content, usage = _parse_events(stdout)

    assert content == "The page survived."
    assert usage["observed"] is True


async def test_a_message_without_usage_is_reported_as_estimated(omp_on_path, monkeypatch):
    """Oh My Pi omits usage when it cannot attribute the turn. The counts are
    then zeroes, and the run's cost report has to say so rather than record a
    free page."""
    stdout = _stream(_message_end(_assistant_message("Body without accounting.")))

    content, usage = _parse_events(stdout)
    assert content == "Body without accounting."
    assert usage["observed"] is False

    _spawn(monkeypatch, FakeOmpProcess(stdout=stdout))
    result = await OmpProvider().generate("system rules", "user context")

    assert result.usage["estimated"] is True
    assert result.input_tokens == 0


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


async def test_a_successful_run_returns_the_page_and_sends_the_prompt_on_stdin(
    omp_on_path, monkeypatch
):
    page = "# Overview\n\nThe module parses events."
    proc = _spawn(
        monkeypatch,
        FakeOmpProcess(stdout=_stream(_message_end(_assistant_message(page, usage=_USAGE)))),
    )

    result = await OmpProvider().generate("system rules", "user context")

    assert result.content == page
    assert result.input_tokens == 2
    assert result.output_tokens == 9
    # cached_tokens is the *read* half; writes are priced differently and are
    # recorded under their own key.
    assert result.cached_tokens == 59064
    assert result.usage["cache_creation_input_tokens"] == 1126
    # Usage was reported, so the counts are not a guess.
    assert "estimated" not in result.usage
    assert result.stop_reason == "end_turn"
    assert result.provider_stop_reason == "stop"
    # With `omp/default` this is the only record of what actually wrote the page.
    assert result.usage["model"] == "omp/default"
    assert result.usage["resolved_model"] == "ccs/claude-opus-5"

    # argv has a per-argument length limit that a rendered page prompt can
    # exceed, so the prompt must travel on stdin.
    assert proc.stdin_input == b"user context"


async def test_a_non_zero_exit_raises_with_the_reason_omp_printed(omp_on_path, monkeypatch):
    """The CLI reports why it failed on stderr. Surfacing only the exit code
    leaves a 68-page run's log with nothing to act on."""
    _spawn(monkeypatch, FakeOmpProcess(returncode=1, stderr='Model "nope" not found'))

    with pytest.raises(ProviderError, match='Model "nope" not found'):
        await OmpProvider().generate("system rules", "user context")


async def test_a_clean_exit_with_no_assistant_text_raises(omp_on_path, monkeypatch):
    """An empty page is worse than a failed one: it gets written to the wiki."""
    _spawn(
        monkeypatch,
        FakeOmpProcess(
            stdout=_stream(
                {"type": "turn_end", "turnId": "t-1"},
                {"type": "agent_end", "isTerminal": True},
            ),
            stderr="the model called a tool and stopped",
        ),
    )

    with pytest.raises(ProviderError, match="returned no result text"):
        await OmpProvider().generate("system rules", "user context")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registered_as_keyless_provider():
    from repowise.core.providers.llm.registry import KEYLESS_PROVIDERS, provider_is_usable

    assert "omp" in KEYLESS_PROVIDERS
    # Keyless: never rejected for a "missing" key.
    assert provider_is_usable("omp", lambda _name: None) is True


def test_subscription_usage_is_priced_at_zero():
    """Oh My Pi bills against its own account, not a repowise API key."""
    from repowise.core.cost_estimator.pricing import _lookup_cost

    assert _lookup_cost("omp/default") == (0.0, 0.0)
    assert _lookup_cost("omp/anthropic/claude-sonnet-4-5") == (0.0, 0.0)
    # The keyed API path for the same underlying model is unaffected.
    assert _lookup_cost("claude-sonnet-4-5") != (0.0, 0.0)


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------


def test_a_catalog_with_undecodable_bytes_still_loads(tmp_path):
    """Real bytes through a real subprocess, because the decode is the contract.

    `text=True` alone decodes with the process locale and with `errors="strict"`,
    so one non-ASCII byte in that JSON -- a model's display name is enough --
    raises UnicodeDecodeError. That is a ValueError, which neither OSError nor
    SubprocessError catches, so it escaped the loader's own degradation and took
    the provider picker down with it (#2186, same shape in codex_cli).

    Mocking `subprocess.run` cannot test this: the bug lives in how `run` itself
    decodes the child's bytes, so the child has to be real.
    """
    fake = tmp_path / "omp"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        # Lone 0xe9 -- latin-1 'é', invalid as UTF-8.
        'sys.stdout.buffer.write(b\'{"models":[{"selector":"x/y","name":"caf\\xe9"}]}\')\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)

    catalog = _load_omp_model_catalog(str(fake))

    assert catalog is not None, "undecodable bytes must not empty the catalog"
    assert catalog[0]["selector"] == "x/y"
    # The unreadable byte is replaced, not fatal.
    assert "caf" in catalog[0]["name"]


def test_a_catalog_listing_becomes_picker_options(omp_on_path, monkeypatch):
    """A reasoning-capable model offers every level; a non-reasoning one only auto."""
    payload = json.dumps(
        {
            "models": [
                {"selector": "ccs/claude-opus-5", "name": "Claude Opus 5", "reasoning": True},
                {"selector": "x/plain-1", "name": "Plain", "reasoning": False},
            ]
        }
    )

    class _Done:
        returncode = 0
        stdout = payload

    _load_omp_model_catalog.cache_clear()
    monkeypatch.setattr("repowise.core.providers.llm.omp.subprocess.run", lambda *a, **k: _Done())
    try:
        by_model = {o.model: o for o in OmpProvider().available_model_options()}
    finally:
        _load_omp_model_catalog.cache_clear()

    assert by_model["omp/default"].recommended is True
    assert by_model["omp/ccs/claude-opus-5"].reasoning_modes == REASONING_MODES
    assert by_model["omp/x/plain-1"].reasoning_modes == ("auto",)


def test_an_aborted_turn_is_a_failure_not_a_half_written_page(omp_on_path, monkeypatch):
    """Print mode exits 0 on a deadline abort by design (oh-my-pi#7635): the
    outcome is in the stream's stop reason, not the exit status. The prose that
    arrived before the abort is a fragment, so returning it would file a
    half-written page as a finished one.
    """
    aborted = _assistant_message("# Overview\n\nThe module par", stop_reason="aborted")
    aborted["errorMessage"] = "Deadline exceeded"
    _spawn(
        monkeypatch,
        FakeOmpProcess(stdout=_stream({"type": "session", "version": 3}, _message_end(aborted))),
    )

    with pytest.raises(ProviderError, match=r"aborted the turn.*Deadline exceeded"):
        asyncio.run(OmpProvider().generate("system rules", "user context"))
