"""``.repowise/config.yaml`` reads and writes, and provider key persistence."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from repowise.cli._helpers.paths import ensure_repowise_dir, get_repowise_dir
from repowise.cli.errors import reasoned_error
from repowise.core.reasoning import (
    ReasoningMode,
)
from repowise.core.reasoning import (
    resolve_reasoning as resolve_core_reasoning,
)
from repowise.core.repo_config import CONFIG_FILENAME, RepoConfigError, load_repo_config

# Suppresses mirroring the provider key into `.repowise/.env`. The env
# spelling of `init --no-save-key`, mirroring REPOWISE_SKIP_EDITOR_SETUP: CI
# and shared machines configure the process, not the command line. It is also
# how the interactive key prompt records a "no": that answer is taken at the
# start of the run and has to survive until config is written at the end.
NO_SAVE_KEY_ENV = "REPOWISE_NO_SAVE_KEY"


def _clean_flag(value: str | None) -> bool:
    """Whether an env var is set to something meaning "on"."""
    return (value or "").strip().lower() not in ("", "0", "false", "no")

# ---------------------------------------------------------------------------
# Config (provider / model / embedder persisted after init)
# ---------------------------------------------------------------------------


def load_config(repo_path: Path) -> dict[str, Any]:
    """Load ``.repowise/config.yaml`` or return an empty dict if absent.

    A broken config is surfaced as a warning (to stderr, so ``--format json``
    stays parseable) and degrades to an empty dict rather than crashing the
    command or silently using defaults — issue #852: configuration errors must
    be visible, not swallowed. Callers keep their current behaviour either
    way; the warning is the fix.
    """
    from repowise.cli import helpers as _facade  # patch point for tests

    try:
        return load_repo_config(repo_path)
    except RepoConfigError as exc:
        _facade.err_console.print(f"[yellow]Warning:[/yellow] {exc}")
        return {}


def resolve_reasoning(
    reasoning: str | None = None,
    config: dict[str, Any] | None = None,
) -> ReasoningMode:
    """Resolve generation reasoning from CLI flag, env, config, then default."""
    try:
        return resolve_core_reasoning(reasoning, config)
    except ValueError as exc:
        raise reasoned_error(str(exc), reason="invalid_reasoning") from exc


def resolve_max_file_pages(
    chosen: int | None = None,
    config: dict[str, Any] | None = None,
) -> int | None:
    """Resolve the file-page cap: explicit choice, then config.yaml, then unset.

    Three states reach ``GenerationConfig.max_file_pages`` (see its comment and
    ``selection/selector.py``): ``None`` leaves the volume policy in charge, ``0``
    is an explicit refusal to cap, and a positive value is a cap.

    A negative or unparseable ``max_file_pages`` in config.yaml resolves to
    ``None`` rather than to zero pages, so a typo hands the decision back to the
    policy instead of silently deleting the file layer.
    """
    raw = chosen if chosen is not None else (config or {}).get("max_file_pages")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return 0  # "all of them", and it survives the policy
    return value if value > 0 else None


def _persist_provider_key(repo_path: Path, provider: str) -> None:
    """Mirror ``provider``'s credential from the environment into ``.repowise/.env``.

    Key persistence used to hang off the interactive key *prompt*, so it only
    ran when the user typed a key. Supplying the key through the environment is
    precisely the no-prompt case, which meant a scripted
    ``init --provider openai --yes`` indexed fine and wrote ``provider:`` to
    config.yaml but left no credential behind: ``repowise mcp`` against that
    repo then answered ``degraded: "no-llm-provider"``. Scripted init is the
    primary path for agents and CI, so a run that succeeds has to leave a repo
    whose MCP server can actually answer.

    The rule, stated out loud: a key that successfully indexed this repo is the
    key this repo needs, so it is saved, with a notice naming the file and
    ``--no-save-key`` to opt out. Nothing is written for a provider that needs
    no credential.

    Routed through :func:`save_repo_env_key` rather than writing the file here:
    the ``.gitignore`` entry and the owner-only mode come with it, and a
    hand-rolled write would leave a committable secret.

    Best-effort by design. It runs at the tail of a completed init, after the
    index has been paid for, so a read-only checkout or an unwritable
    ``.repowise/.gitignore`` must cost the user a warning, not the run.
    """
    from repowise.cli import helpers as _facade  # patch point for tests
    from repowise.core.providers.llm.registry import PROVIDER_API_KEY_ENVS
    from repowise.core.repo_config import save_repo_env_key

    if _clean_flag(os.environ.get(NO_SAVE_KEY_ENV)):
        return

    # Keys only. ``provider_required_envs`` would also hand back ollama's
    # OLLAMA_BASE_URL, and pinning an endpoint into the repo is a different
    # decision from saving a credential: a later run on another network
    # would silently reuse the stale URL.
    #
    # Some providers accept either of two vars (gemini: GEMINI_API_KEY /
    # GOOGLE_API_KEY). Persist the one actually carrying the value, not both,
    # so the var written is the var `provider_kwargs` will read back.
    for env_var in PROVIDER_API_KEY_ENVS.get(provider, ()):
        value = (os.environ.get(env_var) or "").strip()
        if not value:
            continue
        try:
            save_repo_env_key(repo_path, env_var, value)
        except (OSError, ValueError) as exc:
            _facade.warn(
                f"could not save {env_var} to "
                f".repowise/.env ({exc}). The index is complete, but "
                f"`repowise mcp` will need {env_var} in its environment."
            )
            return
        _facade.console.print(
            f"[dim]Saved {env_var} to .repowise/.env (gitignored). "
            f"--no-save-key or {NO_SAVE_KEY_ENV}=1 to skip.[/dim]"
        )
        return


def save_config(
    repo_path: Path,
    provider: str,
    model: str,
    embedder: str,
    *,
    embedding_model: str | None = None,
    exclude_patterns: list[str] | None = None,
    commit_limit: int | None = None,
    reasoning: str | None = None,
    save_key: bool = True,
) -> None:
    """Write provider/model/embedder (and optionally exclude_patterns) to ``.repowise/config.yaml``.

    Performs a round-trip load so existing keys are preserved.

    ``embedding_model`` is persisted so ``repowise serve`` can rebuild the same
    embedder used at init time — without it the server silently falls back to a
    provider default (e.g. ``text-embedding-3-small``), which mismatches the
    indexed vectors and breaks chat/search retrieval (issue #426).

    ``save_key`` also mirrors the provider's credential into ``.repowise/.env``.
    It belongs here because this function *is* the "this run committed to this
    provider for this repo" moment, shared by all three flows that reach it
    (single-repo init, workspace init, ``workspace add``). All three otherwise
    leave an indexed repo their MCP server cannot authenticate against.
    """
    from repowise.core.providers.llm.resolve import apply_llm_selection
    from repowise.core.repo_config import save_repo_config

    ensure_repowise_dir(repo_path)

    # Round-trip: preserve any existing keys (e.g. exclude_patterns set via CLI).
    # provider/model go through the writer the web UI uses too.
    existing = apply_llm_selection(load_config(repo_path), provider, model)
    existing["embedder"] = embedder
    if embedding_model:
        existing["embedding_model"] = embedding_model
    else:
        # No model was resolved this run: dropping the key beats leaving a
        # stale one that names a different provider's model, or one this
        # embedder was not actually built with (#2627).
        existing.pop("embedding_model", None)
    if exclude_patterns is not None:
        existing["exclude_patterns"] = exclude_patterns
    if commit_limit is not None:
        existing["commit_limit"] = commit_limit
    if reasoning is not None:
        existing["reasoning"] = resolve_reasoning(reasoning)

    save_repo_config(repo_path, existing)
    _trust_configured_base_url(repo_path, existing, provider)

    if save_key:
        _persist_provider_key(repo_path, provider)


def _trust_configured_base_url(repo_path: Path, config: dict[str, Any], provider: str) -> None:
    """Record ``<provider>.base_url`` as confirmed: this init run chose to use it.

    A config.yaml base URL only receives a key from outside the repo's own
    ``.env`` once the user confirmed it on this machine; running ``init``
    with it in place is that confirmation. An invalid URL is left unconfirmed,
    and resolution reports it.
    """
    from repowise.core.providers.llm.resolve import trust_base_url, validate_base_url

    section = config.get(provider)
    if not isinstance(section, dict):
        return
    try:
        url = validate_base_url(section.get("base_url"))
    except ValueError:
        return
    if url:
        with contextlib.suppress(OSError):
            trust_base_url(repo_path, url)


def save_config_partial(
    repo_path: Path,
    *,
    exclude_patterns: list[str] | None = None,
    commit_limit: int | None = None,
    **extra: Any,
) -> None:
    """Merge optional keys into ``.repowise/config.yaml``, preserving existing keys.

    ``exclude_patterns`` / ``commit_limit`` are explicit for the common case;
    any other config keys (e.g. ``enable_onboarding=False``) can be passed as
    keyword arguments. ``None`` values are skipped so callers can forward
    optional flags without clobbering existing keys.

    ``embedding_model`` is the one exception to "None is skipped": passed
    explicitly as ``None``, it clears any pinned model instead of leaving it
    alone, because that is the caller saying the model changed (or is no
    longer known) for whatever embedder this call names. Merely *omitting*
    ``embedding_model`` is not the same claim, so it does not clear anything
    on its own -- ``reindex_cmd`` calls this after every reindex with only
    ``embedder=``, having never had a model to pass, and a bare ``in extra``
    check on ``embedder`` used to read that silence as "no model" and wipe a
    real pin on every routine reindex (#2627, caught in review on the fix
    itself). Distinguishing "not passed" from "passed as ``None``" needs the
    raw ``extra`` dict, since a keyword default cannot do it: ``in extra``
    only reports that once, but ``get`` cannot tell the two shapes apart
    afterwards.
    """
    import yaml  # type: ignore[import-untyped]

    updates: dict[str, Any] = {}
    if exclude_patterns is not None:
        updates["exclude_patterns"] = exclude_patterns
    if commit_limit is not None:
        updates["commit_limit"] = commit_limit
    updates.update({k: v for k, v in extra.items() if v is not None})
    clear_embedding_model = "embedding_model" in extra and extra["embedding_model"] is None
    if not updates and not clear_embedding_model:
        return

    ensure_repowise_dir(repo_path)
    config_path = get_repowise_dir(repo_path) / CONFIG_FILENAME
    existing = load_config(repo_path)
    existing.update(updates)
    if clear_embedding_model:
        existing.pop("embedding_model", None)

    config_path.write_text(
        yaml.dump(existing, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def save_distill_commands_enabled(repo_path: Path, *, enabled: bool) -> None:
    """Deep-merge ``distill.commands.enabled`` into ``.repowise/config.yaml``.

    :func:`save_config_partial` merges shallowly at the top level, so the
    ``distill`` block is merged here first to avoid clobbering sibling keys
    like ``disabled_filters``.
    """
    cfg = load_config(repo_path)
    distill = dict(cfg.get("distill") or {})
    commands = dict(distill.get("commands") or {})
    commands["enabled"] = enabled
    distill["commands"] = commands
    save_config_partial(repo_path, distill=distill)


#: The hook surfaces that *replace* a tool result rather than adding to it.
#: One consent turns them all on; each has its own ``repowise hook <name>``
#: toggle afterwards, so a surface that turns out to be wrong can be dropped
#: without taking the others with it.
HOOK_REPLACEMENT_SURFACES = ("read_skeleton", "search_digest")


def save_hook_surface_enabled(repo_path: Path, surface: str, *, enabled: bool) -> None:
    """Deep-merge ``hooks.<surface>`` into ``.repowise/config.yaml``.

    Same shape as :func:`save_distill_commands_enabled`, and written by the
    same consent: the rewrite-hook prompt means "let repowise's hooks
    intervene in your agent's tool calls", and rewriting a Bash command is a
    larger intervention than serving a Read as its skeleton or a search as its
    digest, not a smaller one. There is deliberately no second question.
    """
    cfg = load_config(repo_path)
    hooks = dict(cfg.get("hooks") or {})
    hooks[surface] = enabled
    save_config_partial(repo_path, hooks=hooks)


def config_fingerprint(repo_path: Path) -> str:
    """SHA-256 hex of ``.repowise/config.yaml`` + ``health-rules.json`` content.

    Delegates to the shared core implementation so CLI runs and server jobs
    compute identical fingerprints for the same on-disk config.
    """
    from repowise.core.repo_config import config_fingerprint as _core_fingerprint

    return _core_fingerprint(repo_path)
