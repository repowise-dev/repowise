"""Claude Code CLI provider for repowise.

Delegates generation to the authenticated local Claude Code CLI via ``claude -p``
(headless / print mode). Intended for users whose Claude subscription (Pro, Max,
Team or Enterprise seat) is already configured by ``claude login``; it needs no
ANTHROPIC_API_KEY.

Security: uses ``asyncio.create_subprocess_exec`` (no shell), validates model
names against a safe character set, disables the CLI's tool catalog, and runs
the subprocess in a temporary scratch directory resolved with
``Path.resolve()``.

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

import asyncio
import contextlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog

from repowise.core.providers.llm.base import (
    BaseProvider,
    CacheHint,
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
_DEFAULT_MODEL = "claude-haiku-4-5"
_LABEL_PREFIX = "claude_cli/"

_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$")

# A process spawn plus a full CLI turn on a prompt that can carry a lot of file
# context. Generous, because too low is not a slow page but no page.
_EXEC_TIMEOUT_SECONDS = 600

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

# Subscription seats are rate limited per account and each call is a full CLI
# process. Serializing turns a 68-page wiki into an hour; too much concurrency
# trips the account limit and fails the run. 4 matches the ceiling init applies
# to the other CLI-backed providers.
#
# The env override is a true override, not a clamp: it can raise the fan-out
# above 4 as well as lower it. Deliberate -- a Max seat can take more than a Pro
# one, and only the operator knows which they have -- but it does mean 4 is a
# default rather than an enforced cap.
_DEFAULT_CONCURRENCY = 4
_CONCURRENCY_ENV = "REPOWISE_CLAUDE_CLI_CONCURRENCY"

_NOT_FOUND_MESSAGE = (
    "Claude Code CLI not found. Install it from https://claude.com/claude-code, "
    "then run 'claude login'."
)


async def _close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    """Close asyncio's subprocess transport before the event loop shuts down."""

    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(Exception):
        close()
    await asyncio.sleep(0)


def _resolve_claude_executable() -> str | None:
    return shutil.which("claude")


def _validate_model_name(model: str) -> None:
    if not _MODEL_NAME_RE.match(model):
        raise ProviderError(
            "claude_cli",
            f"Invalid model name {model!r}. Model names may only contain "
            "alphanumeric characters, dots, hyphens, underscores, and forward slashes.",
        )


def _normalize_model(model: str | None) -> str:
    """Return the native Claude model slug for *model*.

    Accepts the persisted ``claude_cli/<slug>`` label as well as a bare slug, so
    a value read back out of config.yaml round-trips.
    """
    if not model:
        return _DEFAULT_MODEL
    if model in (_LABEL_PREFIX.rstrip("/"), f"{_LABEL_PREFIX}default"):
        return _DEFAULT_MODEL
    if model.startswith(_LABEL_PREFIX):
        return model.removeprefix(_LABEL_PREFIX) or _DEFAULT_MODEL
    return model


def _model_label(native_model: str) -> str:
    """Return the persisted attribution label for a Claude CLI model.

    Prefixed so cost estimation prices it at zero (a subscription, not API
    spend) and so a page records which path produced it.
    """
    return f"{_LABEL_PREFIX}{native_model}"


def _resolve_concurrency() -> int:
    raw = os.environ.get(_CONCURRENCY_ENV, "").strip()
    if not raw:
        return _DEFAULT_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        log.warning("claude_cli.concurrency.invalid", value=raw, using=_DEFAULT_CONCURRENCY)
        return _DEFAULT_CONCURRENCY
    return max(1, value)


def _tail(text: str, max_chars: int = 2_000) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _error_message(stderr: str, stdout: str, returncode: int) -> str:
    for candidate in (_tail(stderr), _tail(stdout)):
        if not candidate:
            continue
        if candidate.lstrip().startswith(("{", "[")):
            continue
        return candidate
    return f"claude -p exited with {returncode}"


def _payload_failure(payload: dict[str, Any]) -> tuple[str, int | None]:
    """Return the useful Claude error text and its HTTP status, when present."""
    raw_status = payload.get("api_error_status")
    status_code = (
        raw_status if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
    )
    detail = raw_status or payload.get("subtype") or "unknown error"
    result_text = payload.get("result")
    if isinstance(result_text, str) and result_text.strip():
        detail = f"{detail}: {_tail(result_text, max_chars=500)}"
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
        f"could not parse claude -p JSON output: {_tail(text, max_chars=500)}",
    )


class ClaudeCliProvider(BaseProvider):
    """LLM provider backed by ``claude -p`` (Claude Code headless mode).

    Args:
        model: Claude model slug (e.g. ``claude-haiku-4-5``,
            ``claude-sonnet-4-6``). Persisted labels like
            ``claude_cli/claude-haiku-4-5`` are accepted and normalized.
        rate_limiter: Accepted for interface consistency; the provider also
            bounds its own subprocess fan-out.
    """

    # A process spawn plus a full CLI turn: the floor is tens of seconds even for
    # a short prompt, so an interactive caller must budget in minutes or it
    # cancels every call it makes (#1119). Stays under _EXEC_TIMEOUT_SECONDS so
    # the caller gives up before the subprocess does and the error names the real
    # cause.
    interactive_timeout_s: float = 180.0

    def __init__(
        self,
        model: str | None = None,
        rate_limiter: RateLimiter | None = None,
        **_ignored: Any,
    ) -> None:
        claude_cmd = _resolve_claude_executable()
        if not claude_cmd:
            raise ProviderError("claude_cli", _NOT_FOUND_MESSAGE)
        self._claude_cmd = claude_cmd
        self._model = _normalize_model(model)
        _validate_model_name(self._model)
        self._rate_limiter = rate_limiter
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    @property
    def provider_name(self) -> str:
        return "claude_cli"

    @property
    def model_name(self) -> str:
        return _model_label(self._model)

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return _SUPPORTED_REASONING_MODES

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        # The CLI has no machine-readable model catalog to query, so this is a
        # curated list rather than discovery.
        return (
            ProviderModelOption(
                model=_model_label("claude-haiku-4-5"),
                label="claude-haiku-4-5",
                reasoning_modes=_SUPPORTED_REASONING_MODES,
                recommended=True,
                source="fallback",
                notes="fastest; ample for doc pages",
            ),
            ProviderModelOption(
                model=_model_label("claude-sonnet-4-6"),
                label="claude-sonnet-4-6",
                reasoning_modes=_SUPPORTED_REASONING_MODES,
                source="fallback",
                notes="better prose, slower",
            ),
            ProviderModelOption(
                model=_model_label("claude-opus-4-6"),
                label="claude-opus-4-6",
                reasoning_modes=_SUPPORTED_REASONING_MODES,
                source="fallback",
                notes="highest quality; heaviest on subscription limits",
            ),
        )

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(_resolve_concurrency())
            self._semaphore_loop = loop
        return self._semaphore  # type: ignore[return-value]

    def _build_command(
        self,
        system_prompt: str,
        *,
        reasoning: ReasoningMode = "auto",
    ) -> list[str]:
        cmd = [
            self._claude_cmd,
            "-p",
            "--output-format",
            "json",
            "--model",
            self._model,
            "--max-turns",
            "1",
            "--strict-mcp-config",
            "--tools",
            "",
        ]
        prompt = system_prompt.strip()
        prompt = (
            f"{prompt}\n\n{_TOOLLESS_SYSTEM_INSTRUCTION}"
            if prompt
            else _TOOLLESS_SYSTEM_INSTRUCTION
        )
        # --system-prompt replaces Claude Code's agent preamble rather than
        # appending to it: repowise's prompt is the whole instruction set, and
        # the coding-agent framing only competes with it.
        cmd.extend(["--system-prompt", prompt])

        mode = normalize_reasoning(reasoning)
        if mode in _SUPPORTED_REASONING_MODES[1:]:
            cmd.extend(["--effort", mode])
        elif mode != "auto":
            # ``off``, ``none`` and ``minimal`` have no Claude CLI equivalent.
            log.warning("claude_cli.reasoning.unsupported", requested=mode, using="auto")
        return cmd

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
        reasoning: ReasoningMode = "auto",
        cache_hints: tuple[CacheHint, ...] = (),
    ) -> GeneratedResponse:
        # temperature and max_tokens have no CLI equivalent. The base-class
        # contract says to clip rather than raise, so they are accepted and
        # dropped.
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        cmd = self._build_command(system_prompt, reasoning=reasoning)

        log.debug("claude_cli.generate.start", model=self.model_name, request_id=request_id)

        async with self._get_semaphore():
            # One directory per call avoids both CLAUDE.md discovery and state
            # leaking between concurrent pages. TemporaryDirectory removes it
            # after success, failure, timeout, or cancellation.
            with tempfile.TemporaryDirectory(prefix="repowise-claude-cli-") as scratch_dir:
                cwd = str(Path(scratch_dir).resolve())
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                    )
                except FileNotFoundError as exc:
                    raise ProviderError("claude_cli", _NOT_FOUND_MESSAGE) from exc

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(user_prompt.encode("utf-8")),
                        timeout=_EXEC_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(ProcessLookupError):
                        await proc.wait()
                    raise
                except TimeoutError as exc:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(ProcessLookupError):
                        await proc.wait()
                    raise ProviderError(
                        "claude_cli",
                        f"claude -p timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                    ) from exc
                finally:
                    await _close_subprocess_transport(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        payload: dict[str, Any] | None = None
        with contextlib.suppress(ProviderError):
            payload = _parse_result(stdout)

        if proc.returncode != 0:
            if payload is not None and (
                payload.get("is_error") or payload.get("subtype") != "success"
            ):
                message, status_code = _payload_failure(payload)
                raise ProviderError("claude_cli", message, status_code=status_code)
            raise ProviderError(
                "claude_cli",
                _error_message(stderr, stdout, proc.returncode),
            )

        if payload is None:
            payload = _parse_result(stdout)

        if payload.get("is_error") or payload.get("subtype") != "success":
            message, status_code = _payload_failure(payload)
            raise ProviderError("claude_cli", message, status_code=status_code)

        content = payload.get("result")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("claude_cli", "claude -p succeeded but returned no result text.")

        raw_usage = payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        # The CLI reports cache reads and creations separately; repowise wants a
        # single "served from cache" number, which is the read half.
        cached_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)

        stop_reason, provider_stop_reason = normalize_stop_reason(payload.get("stop_reason"))

        log.debug(
            "claude_cli.generate.done",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            request_id=request_id,
        )

        usage_payload = {
            **usage,
            "source": "claude_cli",
            "model": self.model_name,
            "cache_creation_input_tokens": cache_creation_tokens,
            # Recorded for auditing only. Cost is priced at zero in the cost
            # table: this is subscription usage, not API spend.
            "reported_cost_usd": payload.get("total_cost_usd"),
            "num_turns": payload.get("num_turns"),
            "stderr": _tail(stderr, max_chars=1_000) if stderr.strip() else "",
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
