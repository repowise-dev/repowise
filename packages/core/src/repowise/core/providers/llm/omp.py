"""Oh My Pi (``omp``) provider for repowise.

Delegates generation to a local Oh My Pi install via ``omp -p --mode json``
(headless / print mode). Oh My Pi already holds credentials for whatever it is
configured against -- an OAuth subscription, a provider account, or a plain API
key living in its own config -- so repowise needs no key of its own.

Oh My Pi also exposes a bidirectional JSON-RPC transport (``--mode rpc``), and
it is the richer surface: request/response correlation, mid-turn steering,
host-owned tools. None of that applies to a one-shot completion. Print mode
answers one prompt and exits, reports failure as a non-zero exit with the reason
on stderr, and emits the same ``message_end`` event carrying the answer, the
token usage, the stop reason and the model actually routed to. Using it keeps
this provider the same shape as ``claude_cli``, ``codex_cli`` and ``opencode``
instead of carrying a bespoke framing/correlation client that would have to
track the RPC protocol's own versioning.

Security: uses ``asyncio.create_subprocess_exec`` (no shell), validates
user-supplied model names against a safe character set, disables the agent's
built-in tool catalog, and runs the subprocess in a temporary scratch directory
resolved with ``Path.resolve()``.

Three deliberate choices worth knowing about:

- The subprocess does *not* run in the repo, so this provider is absent from
  ``REPO_PATH_PROVIDERS``. Oh My Pi discovers ``AGENTS.md``-style context files
  from its working directory, and on a large repo that injects the project's
  agent instructions into every page's prompt -- spending tokens and letting
  repo-specific rules bias documentation prose. Everything the generator wants
  is already in the prompt it passes.
- ``--no-session`` keeps wiki generation out of the user's session history. A
  68-page run would otherwise leave 68 resumable sessions behind.
- MCP servers configured in the user's own Oh My Pi config still load, and their
  tool schemas still ride along on each request. Oh My Pi has no switch to
  suppress them for a headless run, so this provider does not pretend to remove
  them; it disables everything it actually can (built-in tools, extensions,
  skills, rules, memory, auto-learn, advisor) and pins the approval mode to
  ``always-ask``, which headless has no UI to satisfy -- so a stray MCP call
  errors instead of running against the user's machine. The schemas are
  prompt-cached after the first call, so the cost is one cache write per run
  rather than per page.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
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
from repowise.core.reasoning import REASONING_MODES, ReasoningMode, normalize_reasoning

log = structlog.get_logger(__name__)

_LABEL_PREFIX = "omp/"
_DEFAULT_MODEL_LABEL = "omp/default"

# Model ids are Oh My Pi *selectors*: `anthropic/claude-sonnet-4-5`, a bare
# `gpt-5.2`, or a configured role (`slow`, `@slow`). The leading `@` is the only
# reason this pattern differs from the other CLI providers'.
_MODEL_NAME_RE = re.compile(r"^@?[a-zA-Z0-9][a-zA-Z0-9._/\-]*$")

# A process spawn plus a full agent turn on a prompt that can carry a lot of file
# context. Generous, because too low is not a slow page but no page.
_EXEC_TIMEOUT_SECONDS = 600

# `omp models --json` reads a local catalog database; it is fast, but it can
# refresh over the network on a cold cache.
_CATALOG_TIMEOUT_SECONDS = 15

_MAX_STDERR_CHARS = 1_000

# Subscription seats are rate limited per account and each call is a full agent
# process. Serializing turns a 68-page wiki into an hour; too much concurrency
# trips the account limit and fails the run. 4 matches the ceiling init applies
# to the other CLI-backed providers.
#
# The env override is a true override, not a clamp: it can raise the fan-out
# above 4 as well as lower it.
_DEFAULT_CONCURRENCY = 4
_CONCURRENCY_ENV = "REPOWISE_OMP_CONCURRENCY"

# Oh My Pi accepts every reasoning level repowise names. `none` has no separate
# meaning here -- it is spelled `off` -- and `auto` is expressed by omitting the
# flag so the user's configured default survives.
_SUPPORTED_REASONING_MODES: tuple[ReasoningMode, ...] = REASONING_MODES

_THINKING_BY_REASONING: dict[str, str] = {
    "off": "off",
    "none": "off",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}

# Oh My Pi still renders a coding-agent framing around a custom system prompt,
# and MCP tools stay callable however this provider is configured. A model that
# calls a tool instead of answering spends the turn and leaves no assistant
# text, so say plainly that one response is the whole job.
_ONE_SHOT_INSTRUCTION = (
    "Answer with the requested document in a single response. Do not call tools, "
    "do not ask follow-up questions, and do not describe what you are about to "
    "do -- write the content itself."
)

# Written into the scratch directory and passed with `--config`. Every key here
# overrides a *global* Oh My Pi setting that a documentation run has no business
# inheriting:
#
# - `memory` / `autolearn` add write-capable tools (`learn`, `manage_skill`) to
#   an otherwise read-only turn, and would persist state from a documentation
#   run into the user's own memory store.
# - `advisor` reviews each finished turn in a second model call. On a 68-page
#   wiki that silently doubles the model spend for notes nobody reads.
# - `approvalMode` is the one that matters for safety. MCP servers from the
#   user's own config still load (see the module docstring), and a global
#   `yolo` would let the model run a mutating MCP tool against their machine
#   mid-generation with no prompt. The enum is a permissiveness ladder, not a
#   gate selector: `write` *auto-approves* writes, so only `always-ask` gates
#   anything. Measured headless, `always-ask` fails the call closed rather than
#   blocking on a prompt nobody can answer -- Oh My Pi reports `Tool "x"
#   requires approval but no interactive UI available`, the model gets a tool
#   error, and the turn still finishes with prose.
_ISOLATION_CONFIG = (
    "autolearn:\n"
    "  enabled: false\n"
    "memory:\n"
    '  backend: "off"\n'
    "advisor:\n"
    "  enabled: false\n"
    "tools:\n"
    "  approvalMode: always-ask\n"
)

_NOT_FOUND_MESSAGE = (
    "Oh My Pi CLI not found. Install it from https://github.com/can1357/oh-my-pi "
    "and run 'omp' once to sign in."
)


async def _close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    """Close asyncio's subprocess transport before the event loop shuts down."""
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        with contextlib.suppress(Exception):
            transport.close()
    # Give the loop one tick to run the transport's close callbacks, so a
    # finished process does not surface as "Event loop is closed" at teardown.
    await asyncio.sleep(0)


def _resolve_omp_executable() -> str | None:
    return shutil.which("omp")


def _validate_model_name(model: str) -> None:
    if not _MODEL_NAME_RE.match(model):
        raise ProviderError(
            "omp",
            f"Invalid model name {model!r}. Expected an Oh My Pi selector such as "
            "'anthropic/claude-sonnet-4-5', 'gpt-5.2', or a role like '@slow'.",
        )


def _normalize_model(model: str | None) -> str | None:
    """Return the native Oh My Pi selector for *model*, or None for the default.

    Accepts the persisted label form (``omp/anthropic/claude-sonnet-4-5``) as
    well as a bare selector, and treats ``omp/default`` as "whatever Oh My Pi is
    configured to use", which is the point of this provider.
    """
    if not model:
        return None
    candidate = model.strip()
    if candidate.startswith(_LABEL_PREFIX):
        candidate = candidate[len(_LABEL_PREFIX) :]
    if not candidate or candidate.lower() == "default":
        return None
    return candidate


def _model_label(model: str | None) -> str:
    """Return the persisted attribution label for an Oh My Pi model."""
    native = _normalize_model(model)
    return f"{_LABEL_PREFIX}{native}" if native else _DEFAULT_MODEL_LABEL


def _resolve_concurrency() -> int:
    raw = os.environ.get(_CONCURRENCY_ENV, "").strip()
    if not raw:
        return _DEFAULT_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        log.warning("omp.concurrency.invalid", value=raw, using=_DEFAULT_CONCURRENCY)
        return _DEFAULT_CONCURRENCY
    return max(1, value)


def _tail(text: str, max_chars: int = _MAX_STDERR_CHARS) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _error_message(stderr: str, stdout: str, returncode: int | None) -> str:
    for candidate in (_tail(stderr), _tail(stdout)):
        if candidate:
            return candidate
    return f"omp -p exited with {returncode}"


def _parse_events(stdout: str) -> tuple[str, dict[str, Any]]:
    """Return ``(content, usage)`` from ``--mode json`` output.

    The stream is one JSON event per line. Only assistant ``message_end`` events
    matter: each carries the finished text blocks plus that message's usage,
    stop reason and routed model. Text is accumulated rather than overwritten so
    an answer split across messages (a tool call followed by prose, which MCP
    tools make possible however this provider is configured) survives intact,
    and usage is summed for the same reason.
    """
    text_parts: list[str] = []
    totals = {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0}
    cost = 0.0
    observed = False
    stop_reason: Any = None
    resolved_model = ""

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        # Cheap guard before the parse: print mode writes JSONL, but a runtime
        # warning from the JS host on stdout should not abort a whole page.
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue

        for block in message.get("content") or ():
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            chunk = block.get("text")
            if isinstance(chunk, str) and chunk.strip():
                text_parts.append(chunk)

        usage = message.get("usage")
        if isinstance(usage, dict):
            observed = True
            for key in totals:
                totals[key] += int(usage.get(key, 0) or 0)
            usage_cost = usage.get("cost")
            if isinstance(usage_cost, dict):
                cost += float(usage_cost.get("total", 0.0) or 0.0)

        if message.get("stopReason"):
            stop_reason = message.get("stopReason")
        provider, model = message.get("provider"), message.get("model")
        if provider and model:
            resolved_model = f"{provider}/{model}"
        elif model:
            resolved_model = str(model)

    return "\n".join(text_parts), {
        **totals,
        "cost": cost,
        "observed": observed,
        "stop_reason": stop_reason,
        "resolved_model": resolved_model,
    }


@lru_cache(maxsize=4)
def _load_omp_model_catalog(omp_cmd: str) -> tuple[dict[str, Any], ...] | None:
    """Return Oh My Pi's model catalog, or None when it cannot be read.

    ``omp models --json`` lists the models the local install can actually reach,
    which is narrower than every model Oh My Pi knows about: it reflects the
    providers the user is authenticated for.

    Decoding is pinned to UTF-8 rather than left to ``text=True``, which uses
    the process locale: on a default Windows console any non-ASCII byte in that
    JSON (a model's display name is enough) raises ``UnicodeDecodeError``. That
    is a ``ValueError``, so neither handler below would catch it and it would
    escape the degradation this function exists to provide -- the picker would
    traceback instead of falling back to the default option. Same defect as
    #2186 against ``codex_cli``'s catalog loader.

    ``ValueError`` is deliberately *not* caught. With the encoding pinned the
    decode can no longer raise, so catching it would only be able to swallow a
    future regression that removed the encoding again -- turning the loud
    failure this fix exists to prevent back into a silent empty catalog.
    """
    try:
        completed = subprocess.run(
            [omp_cmd, "models", "--json", "--no-extensions"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CATALOG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None

    entries = [entry for entry in models if isinstance(entry, dict) and entry.get("selector")]
    return tuple(entries) or None


def _omp_model_options(omp_cmd: str) -> tuple[ProviderModelOption, ...]:
    catalog = _load_omp_model_catalog(omp_cmd)
    if catalog is None:
        return (
            ProviderModelOption(
                model=_DEFAULT_MODEL_LABEL,
                label="Oh My Pi default",
                reasoning_modes=_SUPPORTED_REASONING_MODES,
                recommended=True,
                source="fallback",
                notes="uses your omp model config",
            ),
        )

    options = [
        ProviderModelOption(
            model=_DEFAULT_MODEL_LABEL,
            label="Oh My Pi default",
            reasoning_modes=_SUPPORTED_REASONING_MODES,
            recommended=True,
            source="local",
            notes="uses your omp model config",
        )
    ]
    for entry in sorted(catalog, key=lambda item: str(item.get("selector", ""))):
        selector = str(entry["selector"])
        # A model that does not reason cannot honour a thinking level, so the
        # picker should not offer one for it.
        modes = _SUPPORTED_REASONING_MODES if entry.get("reasoning") else ("auto",)
        options.append(
            ProviderModelOption(
                model=_model_label(selector),
                label=selector,
                reasoning_modes=modes,
                recommended=False,
                source="local",
                notes=str(entry.get("name") or ""),
            )
        )
    return tuple(options)


class OmpProvider(BaseProvider):
    """LLM provider backed by ``omp -p --mode json`` (Oh My Pi print mode).

    Uses the local Oh My Pi CLI for generation. Does not require an API key --
    Oh My Pi manages its own authentication, so a subscription or an account
    configured with ``omp`` works here.

    Args:
        model: Optional Oh My Pi selector (``anthropic/claude-sonnet-4-5``,
            ``gpt-5.2``, or a role such as ``@slow``). Persisted labels like
            ``omp/anthropic/claude-sonnet-4-5`` are accepted and normalized.
            Omitted or ``omp/default`` leaves model choice to Oh My Pi.
        rate_limiter: Accepted for interface consistency; the provider also
            bounds its own subprocess fan-out.
    """

    # A process spawn plus a full agent turn: the floor is tens of seconds even
    # for a short prompt, so an interactive caller must budget in minutes or it
    # cancels every call it makes. Stays under _EXEC_TIMEOUT_SECONDS so the
    # caller gives up before the subprocess does and the error names the real
    # cause.
    interactive_timeout_s: float = 180.0

    def __init__(
        self,
        model: str | None = None,
        rate_limiter: RateLimiter | None = None,
        **_ignored: Any,
    ) -> None:
        omp_cmd = _resolve_omp_executable()
        if not omp_cmd:
            raise ProviderError("omp", _NOT_FOUND_MESSAGE)
        self._omp_cmd = omp_cmd
        self._model = _normalize_model(model)
        if self._model is not None:
            _validate_model_name(self._model)
        self._rate_limiter = rate_limiter
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    @property
    def provider_name(self) -> str:
        return "omp"

    @property
    def model_name(self) -> str:
        return _model_label(self._model)

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return _SUPPORTED_REASONING_MODES

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _omp_model_options(self._omp_cmd)

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(_resolve_concurrency())
            self._semaphore_loop = loop
        return self._semaphore  # type: ignore[return-value]

    def _build_command(
        self,
        *,
        system_prompt_file: Path | None,
        config_file: Path,
        reasoning: ReasoningMode = "auto",
    ) -> list[str]:
        cmd = [
            self._omp_cmd,
            # Non-interactive: answer the prompt on stdin and exit.
            "-p",
            "--mode",
            "json",
            # Ephemeral: a documentation run should not leave a resumable
            # session behind for every page it writes.
            "--no-session",
            # The prompt is the whole instruction set. Everything the CLI would
            # otherwise discover competes with it and costs tokens.
            "--no-extensions",
            "--no-skills",
            "--no-rules",
            "--no-tools",
            "--config",
            str(config_file),
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if system_prompt_file is not None:
            # Passed as a path rather than inline text: a system prompt carrying
            # file context can exceed the per-argument length limit, and Oh My
            # Pi reads a single-line value as a file when one exists there.
            cmd.extend(["--system-prompt", str(system_prompt_file)])

        mode = normalize_reasoning(reasoning)
        thinking = _THINKING_BY_REASONING.get(mode)
        if thinking is not None:
            cmd.extend(["--thinking", thinking])
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

        log.debug("omp.generate.start", model=self.model_name, request_id=request_id)

        async with self._get_semaphore():
            # One directory per call keeps context-file discovery and any
            # per-session state from leaking between concurrent pages.
            # TemporaryDirectory removes it after success, failure, timeout, or
            # cancellation.
            with tempfile.TemporaryDirectory(prefix="repowise-omp-") as scratch_dir:
                scratch = Path(scratch_dir).resolve()
                # The agent's cwd is a subdirectory, so the files this provider
                # writes for the CLI are never visible as project content.
                work_dir = scratch / "work"
                work_dir.mkdir()

                config_file = scratch / "omp-config.yml"
                config_file.write_text(_ISOLATION_CONFIG, encoding="utf-8")

                instructions = system_prompt.strip()
                instructions = (
                    f"{instructions}\n\n{_ONE_SHOT_INSTRUCTION}"
                    if instructions
                    else _ONE_SHOT_INSTRUCTION
                )
                system_prompt_file = scratch / "system-prompt.md"
                system_prompt_file.write_text(instructions, encoding="utf-8")

                cmd = self._build_command(
                    system_prompt_file=system_prompt_file,
                    config_file=config_file,
                    reasoning=reasoning,
                )

                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=str(work_dir),
                    )
                except FileNotFoundError as exc:
                    raise ProviderError("omp", _NOT_FOUND_MESSAGE) from exc

                try:
                    # The prompt goes on stdin rather than argv: a rendered page
                    # prompt can exceed the per-argument length limit, and print
                    # mode reads non-TTY stdin as the initial message.
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
                        "omp",
                        f"omp -p timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                    ) from exc
                finally:
                    await _close_subprocess_transport(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if proc.returncode != 0:
            raise ProviderError("omp", _error_message(stderr, stdout, proc.returncode))

        content, usage = _parse_events(stdout)
        if not content.strip():
            # A clean exit that produced no prose and said nothing on stderr is
            # what an install that was never signed into looks like, and that is
            # the likeliest first failure for a provider whose whole premise is
            # "no API key". Say so rather than reporting an empty stream.
            detail = _tail(stderr) or (
                "no assistant text and nothing on stderr -- if you have not "
                "signed in yet, run 'omp' once"
            )
            raise ProviderError("omp", f"omp -p succeeded but returned no result text ({detail}).")

        input_tokens = int(usage.get("input", 0) or 0)
        output_tokens = int(usage.get("output", 0) or 0)
        cached_tokens = int(usage.get("cacheRead", 0) or 0)
        cache_creation_tokens = int(usage.get("cacheWrite", 0) or 0)

        stop_reason, provider_stop_reason = normalize_stop_reason(usage.get("stop_reason"))

        log.debug(
            "omp.generate.done",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            request_id=request_id,
        )

        usage_payload = {
            "source": "omp",
            "model": self.model_name,
            # Which model Oh My Pi actually routed to. With `omp/default` that
            # is the only record of what wrote the page.
            "resolved_model": usage.get("resolved_model", ""),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": int(usage.get("reasoning", 0) or 0),
            "cache_read_input_tokens": cached_tokens,
            "cache_creation_input_tokens": cache_creation_tokens,
            # Recorded for auditing only. Cost is priced at zero in the cost
            # table: Oh My Pi bills against its own account, not a repowise key.
            "reported_cost_usd": usage.get("cost"),
            "stderr": _tail(stderr) if stderr.strip() else "",
        }
        if not usage.get("observed"):
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
