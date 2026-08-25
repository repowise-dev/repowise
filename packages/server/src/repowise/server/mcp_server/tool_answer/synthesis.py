"""Provider resolution, synthesis budget, and cache-key hashing for get_answer.

Recovers the LLM provider the user configured for ``repowise init`` so the
MCP server can synthesise without a separate config, decides how long one
synthesis call may take, and hashes the question for the answer cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import math
import os
import posixpath
from pathlib import Path

from repowise.core.reasoning import ReasoningMode, resolve_reasoning
from repowise.core.repo_config import load_repo_config
from repowise.server.mcp_server.tool_answer.config import (
    _SYNTHESIS_MAX_TOKENS,
    _SYNTHESIS_TEMPERATURE,
)

_log = logging.getLogger("repowise.mcp.answer")

# Escape hatch for the per-provider synthesis budget. Seconds, float.
_TIMEOUT_ENV = "REPOWISE_ANSWER_TIMEOUT_S"
# Used only if a provider reports no usable budget of its own. Every
# BaseProvider subclass inherits one, so this covers a runtime-registered
# provider that does not subclass it, or one that declares nonsense.
_FALLBACK_TIMEOUT_S = 30.0
# Ceiling on any budget, however configured. get_answer is a tool an agent
# blocks on, and every MCP client enforces its own tool timeout underneath
# this one, so a longer budget cannot produce an answer the client will still
# accept. It only converts our diagnosable degraded payload into the client's
# bare transport error. 600s also matches the agent-CLI providers' own
# subprocess ceiling, past which raising this is inert anyway.
_MAX_TIMEOUT_S = 600.0
# Operation label for the ledger rows written here, so answer spend is its own
# bucket on the costs report rather than folded into doc generation.
_COST_OPERATION = "answer_synthesis"


def _hash_question(question: str) -> str:
    """Stable SHA-256 of the normalized question. Lowercase + strip + collapse ws."""
    norm = " ".join(question.lower().strip().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _normalize_scope(scope: str | None) -> str | None:
    """Canonical repo-relative scope used by retrieval and cache identity."""
    if scope is None or not scope.strip():
        return None
    normalized = posixpath.normpath(scope.strip().replace("\\", "/"))
    normalized = normalized.removeprefix("./").strip("/")
    return normalized if normalized and normalized != "." else None


def _hash_answer_identity(question: str, normalized_scope: str | None) -> str:
    """Versioned answer-cache identity for one normalized question and scope."""
    normalized_question = " ".join(question.lower().strip().split())
    # JSON preserves the distinction between null and every possible scope
    # string (including a literal "<unscoped>") without a sentinel collision.
    identity = _json.dumps(
        ["v2", normalized_question, normalized_scope],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _resolve_reasoning_for_answer(repo_path: Path | None) -> ReasoningMode:
    """Resolve the synthesis reasoning mode from env and repo config."""
    config = load_repo_config(repo_path) if repo_path is not None else None
    return resolve_reasoning(config=config)


def _load_repo_provider_config(
    repo_path: Path | None,
) -> tuple[str | None, str | None, dict[str, str]]:
    """Read persisted provider config for a repo.

    `repowise init` writes the chosen provider + model into
    ``.repowise/state.json`` and the corresponding API key into
    ``.repowise/.env``. The MCP server doesn't load that .env at startup,
    so without this helper get_answer can't reach an LLM unless the user
    also exports REPOWISE_PROVIDER / OPENAI_API_KEY in the shell that
    launched Claude Code. This recovers the persisted values so the same
    provider used for init / update is reused for get_answer.

    Returns ``(provider_name, model, env_overlay)``. Any field may be
    None / empty — callers should fall back to process env when missing.
    """
    if repo_path is None:
        return None, None, {}

    config_path = repo_path / ".repowise" / "config.yaml"
    state_path = repo_path / ".repowise" / "state.json"
    env_path = repo_path / ".repowise" / ".env"

    name: str | None = None
    model: str | None = None
    overlay: dict[str, str] = {}

    # config.yaml first: it is the user-editable intent. state.json only
    # records what the LAST index run used, which goes stale the moment the
    # user switches providers (observed: a config saying openai while
    # state.json still said gemini from a months-old index build — resolution
    # followed state.json into a keyless provider and synthesis went dark).
    try:
        if config_path.is_file():
            import yaml

            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                name = data.get("provider") or None
                model = data.get("model") or None
    except Exception:
        _log.debug("Failed to read %s", config_path, exc_info=True)

    try:
        if state_path.is_file():
            data = _json.loads(state_path.read_text(encoding="utf-8"))
            name = name or data.get("provider") or None
            model = model or data.get("model") or None
    except Exception:
        _log.debug("Failed to read %s", state_path, exc_info=True)

    try:
        if env_path.is_file():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                if key:
                    overlay[key] = val
    except Exception:
        _log.debug("Failed to read %s", env_path, exc_info=True)

    return name, model, overlay


def _resolve_provider_for_answer(repo_path: Path | None = None):
    """Best-effort provider lookup mirroring cli/helpers.resolve_provider.

    Avoids the click dependency from the cli package, but shares that
    function's env-var mapping (``repowise.core.providers.llm.registry``) so
    the two cannot drift apart. Returns a BaseProvider or None if no API key /
    provider is configured.

    Resolution order: process env vars first, then ``.repowise/config.yaml``
    / ``state.json`` + ``.repowise/.env`` for the active repo. The persisted
    values are the same ones ``repowise init`` and ``repowise update`` use, so
    get_answer follows the user's existing provider choice without a separate
    config.
    """
    try:
        from repowise.core.providers.llm.registry import (
            PROVIDER_AUTODETECT_ORDER,
            get_provider,
            provider_credentials_present,
            provider_is_usable,
            provider_kwargs,
        )
    except Exception:
        _log.warning("Provider registry import failed", exc_info=True)
        return None

    persisted_name, persisted_model, env_overlay = _load_repo_provider_config(repo_path)

    def _env(key: str) -> str | None:
        # Prefer real process env so an explicit shell export still wins;
        # fall back to .repowise/.env only when the process env is empty.
        return os.environ.get(key) or env_overlay.get(key) or None

    name = os.environ.get("REPOWISE_PROVIDER") or persisted_name
    model = (
        os.environ.get("REPOWISE_DOC_MODEL") or os.environ.get("REPOWISE_MODEL") or persisted_model
    )

    def _try(provider_name: str, provider_model: str | None):
        kwargs = provider_kwargs(
            provider_name,
            model=provider_model,
            repo_path=repo_path,
            getenv=_env,
        )
        try:
            return get_provider(provider_name, **kwargs)
        except Exception:
            _log.warning("get_provider(%s) failed", provider_name, exc_info=True)
            return None

    # Explicit selection wins — when its key is actually available. A
    # configured/persisted provider whose key is absent must NOT end
    # resolution: returning None here made get_answer silently drop to
    # retrieval-only mode even though another provider's key sat in the env
    # (the auto-detect below was unreachable). Fall through instead, and
    # drop the persisted model on the way down — it belongs to the named
    # provider and would break a cross-provider fallback call.
    if name:
        if provider_is_usable(name, _env):
            provider = _try(name, model)
            if provider is not None:
                return provider
        _log.warning(
            "Configured provider %r has no usable key/setup; falling back to "
            "auto-detection from available API keys.",
            name,
        )
        if not (os.environ.get("REPOWISE_DOC_MODEL") or os.environ.get("REPOWISE_MODEL")):
            model = None  # persisted model was provider-specific

    # Auto-detect from whatever credentials the environment carries. Only the
    # keyed providers participate: a keyless one is "usable" everywhere, so
    # auto-detecting it would hijack every repo that never configured it.
    for candidate in PROVIDER_AUTODETECT_ORDER:
        if candidate == name:
            # Already attempted above with the same credentials. Retrying it
            # here only differs in dropping the model, which cannot fix the
            # reasons construction fails (a missing SDK, a rejected key), and
            # it costs the next candidate its turn.
            continue
        if not provider_credentials_present(candidate, _env):
            continue
        provider = _try(candidate, model)
        if provider is not None:
            return provider
    return None


def _usable_seconds(value: object) -> float | None:
    """``value`` as a usable budget in seconds, clamped, or None if unusable.

    Rejects bools (``float(True)`` would be a silent 1-second budget), NaN, and
    anything non-positive. Infinity is clamped rather than rejected: it means
    "no limit", and ``asyncio.wait_for`` honours that literally by never firing,
    which is the one outcome this function exists to prevent.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or number <= 0:
        return None
    return min(number, _MAX_TIMEOUT_S)


def _synthesis_timeout(provider) -> float:
    """Wall-clock budget for one synthesis call, in seconds.

    Defaults to the provider's own ``interactive_timeout_s``. A remote API
    answers in seconds, an agent-CLI subprocess or a local model needs minutes,
    and a single hardcoded number cancels the second group on every call
    (issue #1119). ``REPOWISE_ANSWER_TIMEOUT_S`` overrides it for the cases no
    default can predict: a slow proxy, an overloaded self-hosted box, an agent
    harness that would rather fail fast.

    Every input here is untrusted. The env var is user-typed, and the provider
    attribute belongs to a possibly runtime-registered class that need not
    subclass BaseProvider, so a bad value must degrade to a working default
    rather than raise out of a function whose caller is not expecting it to.
    """
    raw = (os.environ.get(_TIMEOUT_ENV) or "").strip()
    if raw:
        try:
            override = _usable_seconds(float(raw))
        except ValueError:
            override = None
        if override is not None:
            return override
        _log.warning("Ignoring unusable %s=%r", _TIMEOUT_ENV, raw)

    declared = _usable_seconds(getattr(provider, "interactive_timeout_s", None))
    return _FALLBACK_TIMEOUT_S if declared is None else declared


def _synthesis_failure_note(exc: BaseException, provider, timeout_s: float, timed_out: bool) -> str:
    """Human-readable ``note`` for a synthesis failure.

    Names the provider and model that failed and, when we cancelled the call,
    the budget it blew and the knob that raises it. The old note carried only
    the exception class, which collapsed "your agent CLI needs 90s" and "your
    key is rate limited" into an identical, unactionable ``TimeoutError``
    (#1119).

    ``timed_out`` is passed in rather than sniffed off ``exc``: builtin
    ``TimeoutError`` is an ``OSError`` subclass and is what a bare socket
    timeout raises, so type-testing would offer "raise your budget" for a
    connection that died in ten seconds.
    """
    who = (
        f"provider={getattr(provider, 'provider_name', None) or '?'}, "
        f"model={getattr(provider, 'model_name', None) or '?'}"
    )
    if timed_out:
        headroom = ""
        if timeout_s < _MAX_TIMEOUT_S:
            headroom = f" Raise it with {_TIMEOUT_ENV}=<seconds> (your MCP client's own tool timeout still applies), or"
        return (
            f"DEGRADED: LLM synthesis exceeded its {timeout_s:g}s budget ({who})."
            f"{headroom} switch to a faster provider. Read the listed files to "
            "answer meanwhile."
        )
    detail = " ".join(str(exc).split())[:200]
    suffix = f": {detail}" if detail else ""
    return (
        f"DEGRADED: LLM synthesis failed ({type(exc).__name__}{suffix}) "
        f"[{who}]. Read the listed files to answer."
    )


def _empty_completion_note(provider, response) -> str:
    """Note for a call that succeeded and returned no text.

    Measured against a local reasoning model on ollama: it spent all 1024
    tokens thinking and emitted an empty content block. The call did not fail,
    so nothing marked it degraded, and get_answer shipped a blank answer as a
    normal result. That is the same silent empty answer #1119 was reported as,
    reached from the other side.
    """
    who = (
        f"provider={getattr(provider, 'provider_name', None) or '?'}, "
        f"model={getattr(provider, 'model_name', None) or '?'}"
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        return (
            f"DEGRADED: the model used its entire {_SYNTHESIS_MAX_TOKENS}-token "
            f"budget without emitting an answer ({who}). Reasoning models spend "
            "that budget on hidden thinking; try a non-reasoning model for "
            "synthesis. Read the listed files to answer meanwhile."
        )
    return (
        f"DEGRADED: the model returned an empty completion ({who}). "
        "Read the listed files to answer."
    )


async def _record_synthesis_cost(
    provider,
    response,
    session_factory,
    repo_id: str | None,
) -> None:
    """Meter one completed synthesis call: a ledger row plus a telemetry line.

    Every published figure for what a question costs to answer has come from
    metering the provider *outside* the product, because the token counts the
    provider returns were read for nothing but the empty-completion check. That
    makes the cost of a change to this path unverifiable anywhere it actually
    runs.

    Two states are reported separately on purpose. A call with counts is priced
    with the same table the generation ledger uses and persisted under its own
    operation label. A call whose counts are both zero is a provider adapter
    that did not normalise ``usage`` — it is *not* a free call, so it warns and
    writes nothing; a zero row would total up as free forever.

    Never raises: metering a call must not be able to fail an answer that
    already succeeded.
    """
    input_tokens = int(getattr(response, "input_tokens", 0) or 0)
    output_tokens = int(getattr(response, "output_tokens", 0) or 0)
    cached_tokens = int(getattr(response, "cached_tokens", 0) or 0)
    model = getattr(provider, "model_name", None) or "?"
    name = getattr(provider, "provider_name", None) or "?"

    if not input_tokens and not output_tokens:
        _log.warning(
            "get_answer synthesis reported no usage (provider=%s, model=%s) — this call is "
            "unpriced, not free. The provider adapter is not normalising input/output tokens.",
            name,
            model,
        )
        return

    try:
        from repowise.core.generation.cost_tracker import CostTracker

        tracker = CostTracker(session_factory, repo_id)
        cost_usd = await tracker.record(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            operation=_COST_OPERATION,
        )
    except Exception:
        _log.warning(
            "get_answer synthesis cost not recorded (provider=%s, model=%s, "
            "input_tokens=%d, output_tokens=%d, cached_tokens=%d)",
            name,
            model,
            input_tokens,
            output_tokens,
            cached_tokens,
            exc_info=True,
        )
        return

    # One line per synthesis, logged whether or not there was a ledger to write
    # to — an MCP server pointed at a read-only or repo-less index still has to
    # be able to report what it spent. ``cached_tokens`` is on the line because a
    # cost comparison across two runs is meaningless without it.
    _log.info(
        "get_answer synthesis cost: provider=%s model=%s operation=%s "
        "input_tokens=%d output_tokens=%d cached_tokens=%d cost_usd=%.6f",
        name,
        model,
        _COST_OPERATION,
        input_tokens,
        output_tokens,
        cached_tokens,
        cost_usd,
    )


async def synthesize(
    provider,
    system_prompt: str,
    user_prompt: str,
    *,
    reasoning: ReasoningMode = "auto",
    session_factory=None,
    repo_id: str | None = None,
) -> tuple[str, str | None]:
    """Run one synthesis call. Returns ``(answer_text, failure_note)``.

    ``failure_note`` is None on success. Owning the budget, the call and the
    error message together is what keeps them consistent: the budget that
    cancelled the call is the number the note quotes, and a caller cannot wire
    up the call while forgetting the budget (which is how #1119 survived a
    hardcoded 30s for as long as it did).

    ``session_factory`` / ``repo_id`` are where the cost row lands. Both
    optional: with neither, the call is still priced and logged, just not
    persisted.
    """
    timeout_s = _synthesis_timeout(provider)

    async def _generate() -> tuple[object | None, BaseException | None]:
        """Swallow the provider's own errors so only our deadline escapes.

        Without this, a provider raising builtin ``TimeoutError`` (which is
        what a bare socket timeout raises, since it subclasses ``OSError``)
        would be indistinguishable from the deadline we set, and the note would
        tell the user to raise a budget that had nothing to do with it.
        """
        try:
            return (
                await provider.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=_SYNTHESIS_MAX_TOKENS,
                    temperature=_SYNTHESIS_TEMPERATURE,
                    reasoning=reasoning,
                ),
                None,
            )
        except Exception as exc:
            return None, exc

    timed_out = False
    try:
        response, failure = await asyncio.wait_for(_generate(), timeout=timeout_s)
    except TimeoutError as exc:
        # Only wait_for can reach here now, so the label is earned.
        timed_out = True
        response, failure = None, exc
    if failure is None:
        # Metered before the empty-completion branch: a reasoning model that
        # spends its whole budget on hidden thinking returns no text and is the
        # most expensive call this path makes, so it is the last one that should
        # go unpriced.
        await _record_synthesis_cost(provider, response, session_factory, repo_id)
        text = (getattr(response, "content", None) or "").strip()
        return (text, None) if text else ("", _empty_completion_note(provider, response))

    _log.warning(
        "get_answer LLM call failed (provider=%s, model=%s, budget=%.1fs, timed_out=%s): %s",
        getattr(provider, "provider_name", "?"),
        getattr(provider, "model_name", "?"),
        timeout_s,
        timed_out,
        failure,
    )
    return "", _synthesis_failure_note(failure, provider, timeout_s, timed_out)
