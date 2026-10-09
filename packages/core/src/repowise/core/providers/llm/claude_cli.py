"""Claude Code CLI provider for repowise.

Delegates generation to the authenticated local Claude Code CLI via ``claude -p``
(headless / print mode). Intended for users whose Claude subscription (Pro, Max,
Team or Enterprise seat) is already configured by ``claude login``; it needs no
ANTHROPIC_API_KEY. Shared subprocess handling lives in ``agent_cli``.

Two deliberate differences from the other agent-CLI providers:

- ``--bare`` is never passed. It reads like the right isolation flag, but it
  documents "Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper (OAuth
  and keychain are never read)", which would defeat the point of this provider.
  Isolation comes from a neutral cwd plus ``--strict-mcp-config``.
- The subprocess deliberately does *not* run in the repo, so this provider is
  absent from ``REPO_PATH_PROVIDERS``. Claude Code auto-discovers CLAUDE.md from
  its working directory, and on a large repo that injects the project's agent
  instructions into every page's prompt -- spending tokens and letting
  repo-specific rules bias documentation prose. Everything the generator wants is
  already in the prompt it passes.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import structlog

from repowise.core.providers.llm.agent_cli import (
    EXEC_TIMEOUT_SECONDS,
    AgentCliProvider,
    model_label,
    normalize_model,
    stderr_tail,
    tail,
)
from repowise.core.providers.llm.base import (
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    normalize_stop_reason,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

log = structlog.get_logger(__name__)

# Matches the anthropic provider's default, whose docstring calls haiku "ample
# for doc pages". Overridable with --model / REPOWISE_MODEL.
_DEFAULT_MODEL = "claude-haiku-5-5"
_EXEC_TIMEOUT_SECONDS = EXEC_TIMEOUT_SECONDS

# This is a one-shot text generation: the prompt already carries the context.
# Claude Code can still try a denied tool and spend the single turn before it
# emits prose, so both halves matter: remove the tool catalog with ``--tools
# ""`` and tell the model explicitly to answer in one response.
_TOOLLESS_SYSTEM_INSTRUCTION = (
    "You have no tools available for this task. Do not attempt to call tools. "
    "Answer the user directly in a single response."
)

_SUPPORTED_REASONING_MODES: tuple[ReasoningMode, ...] = (
    "auto",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def _normalize_model(model: str | None) -> str:
    return normalize_model(model, "claude_cli", _DEFAULT_MODEL) or _DEFAULT_MODEL


def _model_label(native_model: str) -> str:
    return model_label(native_model, "claude_cli")


def _is_failure(payload: dict[str, Any]) -> bool:
    return bool(payload.get("is_error")) or payload.get("subtype") != "success"


def _payload_failure(payload: dict[str, Any]) -> tuple[str, int | None]:
    """Return the useful Claude error text and its HTTP status, when present."""
    raw_status = payload.get("api_error_status")
    status_code = (
        raw_status if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
    )
    detail = raw_status or payload.get("subtype") or "unknown error"
    result_text = payload.get("result")
    if isinstance(result_text, str) and result_text.strip():
        detail = f"{detail}: {tail(result_text, max_chars=500)}"
    return f"claude -p reported failure ({detail}).", status_code


def _parse_result(stdout: str) -> dict[str, Any]:
    """Parse ``--output-format json`` output, tolerating leading noise.

    The CLI emits a single JSON object, but warnings can precede it on stdout,
    so fall back to a line scan before giving up.
    """
    text = stdout.strip()
    if not text:
        raise ProviderError("claude_cli", "claude -p produced no output.")

    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed

    for raw_line in reversed(text.splitlines()):
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                return parsed

    raise ProviderError(
        "claude_cli",
        f"could not parse claude -p JSON output: {tail(text, max_chars=500)}",
    )


class ClaudeCliProvider(AgentCliProvider):
    """LLM provider backed by ``claude -p`` (Claude Code headless mode).

    Args:
        model: Claude model slug (e.g. ``claude-haiku-5-5``,
            ``claude-sonnet-4-6``). Persisted labels like
            ``claude_cli/claude-haiku-5-5`` are accepted and normalized.
        rate_limiter: Accepted for interface consistency; the provider also
            bounds its own subprocess fan-out.
    """

    provider_name = "claude_cli"
    executable_name = "claude"
    command_label = "claude -p"
    concurrency_env = "REPOWISE_CLAUDE_CLI_CONCURRENCY"
    not_found_message = (
        "Claude Code CLI not found. Install it from https://claude.com/claude-code, "
        "then run 'claude login'."
    )
    default_model = _DEFAULT_MODEL
    # Booked under the prefixed label, which prices a seat at $0.00 while
    # ``repowise costs`` still shows the run's token volume (#2267).
    records_cost = True

    def __init__(
        self,
        model: str | None = None,
        rate_limiter: RateLimiter | None = None,
        **_ignored: Any,
    ) -> None:
        super().__init__(model, rate_limiter)

    def exec_timeout_seconds(self) -> float:
        return _EXEC_TIMEOUT_SECONDS  # a module global, so tests can patch it

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return _SUPPORTED_REASONING_MODES

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        # The CLI has no machine-readable model catalog, so this is curated.
        return tuple(
            ProviderModelOption(
                model=_model_label(slug),
                label=slug,
                reasoning_modes=_SUPPORTED_REASONING_MODES,
                recommended=recommended,
                source="fallback",
                notes=notes,
            )
            for slug, recommended, notes in (
                ("claude-haiku-5-5", True, "fastest; ample for doc pages"),
                ("claude-sonnet-4-6", False, "better prose, slower"),
                ("claude-opus-4-6", False, "highest quality; heaviest on subscription limits"),
            )
        )

    def build_command(self, system_prompt: str, reasoning: ReasoningMode) -> list[str]:
        prompt = system_prompt.strip()
        prompt = (
            f"{prompt}\n\n{_TOOLLESS_SYSTEM_INSTRUCTION}"
            if prompt
            else _TOOLLESS_SYSTEM_INSTRUCTION
        )
        # --system-prompt replaces Claude Code's agent preamble rather than
        # appending to it: repowise's prompt is the whole instruction set.
        cmd = [
            self._executable,
            "-p",
            "--output-format",
            "json",
            "--model",
            str(self._model),
            "--max-turns",
            "1",
            "--strict-mcp-config",
            "--tools",
            "",
            "--system-prompt",
            prompt,
        ]
        mode = normalize_reasoning(reasoning)
        if mode in _SUPPORTED_REASONING_MODES[1:]:
            cmd.extend(["--effort", mode])
        elif mode != "auto":
            # ``off``, ``none`` and ``minimal`` have no Claude CLI equivalent.
            log.warning("claude_cli.reasoning.unsupported", requested=mode, using="auto")
        return cmd

    @contextlib.contextmanager
    def working_dir(self) -> Iterator[str]:
        # One directory per call avoids both CLAUDE.md discovery and state
        # leaking between concurrent pages; it is removed however the call ends.
        with tempfile.TemporaryDirectory(prefix="repowise-claude-cli-") as scratch_dir:
            yield str(Path(scratch_dir).resolve())

    def exit_error(self, returncode: int | None, stdout: str, stderr: str) -> ProviderError:
        # API failures arrive as JSON on stdout, often with stderr empty.
        payload: dict[str, Any] | None = None
        with contextlib.suppress(ProviderError):
            payload = _parse_result(stdout)
        if payload is not None and _is_failure(payload):
            message, status_code = _payload_failure(payload)
            return ProviderError("claude_cli", message, status_code=status_code)
        return ProviderError("claude_cli", self.error_message(stderr, stdout, returncode))

    def parse_output(self, stdout: str, stderr: str) -> GeneratedResponse:
        payload = _parse_result(stdout)
        if _is_failure(payload):
            message, status_code = _payload_failure(payload)
            raise ProviderError("claude_cli", message, status_code=status_code)

        content = payload.get("result")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("claude_cli", "claude -p succeeded but returned no result text.")

        raw_usage = payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        # Claude Code reports only the uncached remainder as ``input_tokens``;
        # a page's stable prefix arrives as a cache write or a cache read. The
        # three are disjoint, so their sum is the prompt total the rest of the
        # codebase means by ``input_tokens`` (#2267). ``cached_tokens`` stays
        # the read half.
        uncached_input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
        input_tokens = uncached_input_tokens + cached_tokens + cache_creation_tokens

        stop_reason, provider_stop_reason = normalize_stop_reason(payload.get("stop_reason"))

        usage_payload = {
            **usage,
            "source": "claude_cli",
            "model": self.model_name,
            "input_tokens": input_tokens,
            "uncached_input_tokens": uncached_input_tokens,
            "cache_creation_input_tokens": cache_creation_tokens,
            # Auditing only: the cost table prices subscription usage at zero.
            "reported_cost_usd": payload.get("total_cost_usd"),
            "num_turns": payload.get("num_turns"),
            "stderr": stderr_tail(stderr),
        }
        if not usage:
            usage_payload["estimated"] = True

        return GeneratedResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            usage=usage_payload,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
        )
