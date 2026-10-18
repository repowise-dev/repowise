"""Provider configuration for the web UI: catalog, keys, active selection.

Resolution itself lives in :mod:`repowise.core.providers.llm.resolve`, shared
with the CLI and the MCP server, so a repo resolves the same way everywhere:
explicit request override > the repo's ``config.yaml`` > the global default
here > ``REPOWISE_PROVIDER`` / ``REPOWISE_MODEL`` > spec default or
auto-detection. API keys: process env > the repo's ``.repowise/.env`` > keys
stored here.

This module owns the server-side JSON file (``provider_config.json``): API
keys and the global default (``active_provider`` / ``active_model`` /
``active_base_url``). A repo's own choice is written to that repo's
``.repowise/config.yaml`` through the same core writer the CLI uses. Older
servers kept it here under ``repos[repo_id]``; :func:`_migrate_repo_selection`
moves such an entry into the repo's file the first time the repo is resolved.

Because the CLI resolves keys from ``.repowise/.env`` first, :func:`set_api_key`
mirrors a UI-added key into that file too.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.providers.embedding.registry import (
    DEFAULT_EMBEDDER,
    KEYLESS_EMBEDDERS,
    list_embedders,
)
from repowise.core.providers.llm.resolve import (
    UNSET,
    LLMSelection,
    global_config_path,
    read_global_config,
    read_repo_config,
    resolve_api_key,
    resolve_llm,
    save_repo_llm_selection,
    trust_base_url,
    update_global_config,
    validate_base_url,
)
from repowise.core.providers.llm.specs import PROVIDER_SPECS

logger = logging.getLogger(__name__)

# One migration at a time: two first requests for the same repo must not both
# write its config.yaml and state.json.
_migration_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Provider catalog, derived from the provider specs. Flag-only providers
# (mock) are left out.
# ---------------------------------------------------------------------------

PROVIDER_CATALOG: list[dict[str, Any]] = [
    {
        "id": spec.name,
        "name": spec.label,
        "default_model": spec.default_model,
        "models": list(spec.models),
        "env_keys": list(spec.api_key_envs),
        "requires_key": bool(spec.api_key_envs),
        # Takes an endpoint (``<provider>: {base_url}`` in config.yaml).
        "accepts_base_url": bool(spec.base_url_envs),
        # Can point at a local OpenAI-compatible server and run with no key.
        "local_server": spec.keyless_on_loopback,
        # What a user sets to make it usable, and how to install it.
        "env_vars": list(spec.required_envs),
        "setup_hint": spec.setup_hint,
    }
    for spec in PROVIDER_SPECS.values()
    if spec.picker_rank is not None
]

_CATALOG_BY_ID = {p["id"]: p for p in PROVIDER_CATALOG}

FLAG_ONLY_PROVIDERS = [spec.name for spec in PROVIDER_SPECS.values() if spec.picker_rank is None]


class UnknownProviderError(ValueError):
    """A provider id outside the catalog."""


class NoProviderConfiguredError(ValueError):
    """Nothing names a provider for this repo and no credentials were found."""


def _embedder_catalog() -> list[dict[str, Any]]:
    """Every registered embedder, read at call time so custom ones appear.

    A built-in embedder reads the same credentials as the LLM provider of the
    same name, so its env vars come from that spec; a custom one has none.
    The default comes first so the picker opens on it; the rest stay sorted.
    """
    return [
        {
            "id": name,
            "env_vars": list(spec.required_envs) if (spec := PROVIDER_SPECS.get(name)) else [],
            "semantic": name not in KEYLESS_EMBEDDERS,
        }
        for name in sorted(list_embedders(), key=lambda n: n != DEFAULT_EMBEDDER)
    ]


# ---------------------------------------------------------------------------
# Config file I/O
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    """The server's ``provider_config.json`` (see ``global_config_path``)."""
    return global_config_path()


def _load_config() -> dict[str, Any]:
    """A fresh copy of the stored config. Writes go through ``update_global_config``."""
    return json.loads(json.dumps(dict(read_global_config())))


def _redact_key(text: str) -> str:
    """Redact API key material from *text* for logging/error responses."""
    import re

    # Matches common key prefixes: sk-..., sk-ant-..., sk-proj-..., etc.
    # Keep first 3 chars to indicate presence, redact rest.
    return re.sub(r"(sk-[A-Za-z0-9\-_]{4,})", "sk-***", text)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _get_key_for_provider(provider_id: str, repo_path: str | Path | None = None) -> str | None:
    """The key chat would use: process env > repo ``.env`` > stored key."""
    if provider_id not in _CATALOG_BY_ID:
        return None
    return resolve_api_key(provider_id, repo_path)[0]


def _provider_configured(provider_id: str, repo_path: str | Path | None = None) -> bool:
    """Whether chat can build ``provider_id``: it needs no key or has one."""
    catalog = _CATALOG_BY_ID.get(provider_id)
    if catalog is None:
        return False
    if not catalog["requires_key"] or _get_key_for_provider(provider_id, repo_path):
        return True
    return PROVIDER_SPECS[provider_id].keyless_on_loopback and _on_local_server(
        provider_id, repo_path
    )


def _on_local_server(provider_id: str, repo_path: str | Path | None) -> bool:
    """Whether *provider_id* resolves to a local server, which needs no key."""
    from repowise.core.providers.llm.openai import needs_api_key

    base_url = resolve_llm(repo_path, provider=provider_id).base_url
    return bool(base_url) and not needs_api_key(base_url)


def _legacy_selection(repo_id: str | None) -> Mapping[str, Any] | None:
    """The old server's stored ``repos[repo_id]`` choice, if one is left."""
    if not repo_id:
        return None
    repos = read_global_config().get("repos")
    selection = repos.get(repo_id) if isinstance(repos, Mapping) else None
    return selection if isinstance(selection, Mapping) else None


def _as_repo_layer(selection: Mapping[str, Any]) -> dict[str, Any]:
    """A legacy entry in ``config.yaml``'s shape, for a repo with no checkout."""
    provider = selection.get("provider")
    layer: dict[str, Any] = {"provider": provider, "model": selection.get("model")}
    if selection.get("base_url") and isinstance(provider, str):
        layer[provider] = {"base_url": selection["base_url"]}
    return layer


def _migrate_repo_selection(repo_id: str | None, repo_path: str | Path | None) -> None:
    """Move a legacy ``repos[repo_id]`` entry into the repo's ``config.yaml``.

    Written there (base URL included) only when the yaml names no provider;
    otherwise the yaml wins and the entry is dropped. A repo with no checkout
    keeps its entry, which the resolver then reads in place of the yaml. Once
    the entry is gone this is a dict lookup on the cached file.

    The entry already chose the model for jobs started from the UI, so moving
    it is not a model change: state.json's fingerprints are carried over and
    the next ``repowise update`` does not regenerate because of it.
    """
    if repo_path is None or _legacy_selection(repo_id) is None:
        return
    with _migration_lock:
        selection = _legacy_selection(repo_id)
        if selection is None:
            return
        provider = selection.get("provider")
        if read_repo_config(repo_path).get("provider"):
            logger.info(
                "Dropped the stored chat selection for repo %s: its config.yaml already "
                "names a provider",
                repo_id,
            )
        elif provider not in _CATALOG_BY_ID:
            logger.warning(
                "Dropped the stored chat selection for repo %s: provider %r is not "
                "available on this server",
                repo_id,
                provider,
            )
        else:
            try:
                _write_migrated_selection(repo_path, selection)
            except (OSError, ValueError):
                logger.warning(
                    "Could not move the stored chat selection for repo %s into %s/.repowise/"
                    "config.yaml; keeping it for the next try",
                    repo_id,
                    repo_path,
                )
                return
            logger.info("Moved the stored chat selection for repo %s into config.yaml", repo_id)

        def drop(config: dict[str, Any]) -> None:
            stored = config.get("repos")
            if isinstance(stored, dict):
                stored.pop(repo_id, None)
                if not stored:
                    config.pop("repos", None)

        update_global_config(drop)


def _write_migrated_selection(repo_path: str | Path, selection: Mapping[str, Any]) -> None:
    from repowise.core.repo_config import config_dependency_fingerprints, config_fingerprint

    before = (config_fingerprint(repo_path), config_dependency_fingerprints(repo_path))
    base_url = validate_base_url(selection.get("base_url"))
    save_repo_llm_selection(
        repo_path,
        selection["provider"],
        selection.get("model"),
        base_url if base_url else UNSET,
    )
    if base_url:
        trust_base_url(repo_path, base_url)
    _carry_fingerprints(repo_path, before)


def _carry_fingerprints(repo_path: str | Path, before: tuple[str, dict[str, str]]) -> None:
    """Move state.json's config fingerprints past the migration's own write.

    Only a fingerprint that still matched the pre-migration config moves, so a
    real config change waiting for the next update is still seen.
    """
    from repowise.core.fsutils import atomic_write_text
    from repowise.core.repo_config import config_dependency_fingerprints, config_fingerprint

    state_path = Path(repo_path) / ".repowise" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(state, dict):
        return
    old_fp, old_deps = before
    changed = False
    if state.get("config_fingerprint") == old_fp:
        state["config_fingerprint"] = config_fingerprint(repo_path)
        changed = True
    stored = state.get("config_dependency_fingerprints")
    if isinstance(stored, dict):
        for bucket, value in config_dependency_fingerprints(repo_path).items():
            if stored.get(bucket) == old_deps.get(bucket) and value != old_deps.get(bucket):
                stored[bucket] = value
                changed = True
    if changed:
        atomic_write_text(state_path, json.dumps(state, indent=2))


def resolve_for_repo(
    repo_id: str | None = None,
    repo_path: str | Path | None = None,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> LLMSelection:
    """The selection chat and jobs use for a repo, with provenance."""
    if provider_override and provider_override not in _CATALOG_BY_ID:
        raise UnknownProviderError(f"Unknown provider: {provider_override}")
    _migrate_repo_selection(repo_id, repo_path)
    legacy = _legacy_selection(repo_id) if repo_path is None else None
    return resolve_llm(
        repo_path,
        provider=provider_override,
        model=model_override,
        override_source="request",
        known=_CATALOG_BY_ID,
        repo_layer=_as_repo_layer(legacy) if legacy else None,
    )


def list_provider_status(
    repo_id: str | None = None,
    repo_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return provider catalog + the active selection for the repo.

    With ``repo_path`` the active provider/model/base URL are what that repo's
    chat will use, each with the layer it came from (``provenance``). A
    configured model missing from the static catalog is added to that
    provider's ``models`` so the picker can show it.
    """
    selection = resolve_for_repo(repo_id, repo_path)
    active_id, model = selection.provider, selection.model

    providers = []
    for p in PROVIDER_CATALOG:
        models = list(p["models"])
        # Surface a configured-but-not-cataloged model (e.g. a local LiteLLM
        # alias like ``gemma4``) so the picker can display the active choice.
        if p["id"] == active_id and model and model not in models:
            models.append(model)
        providers.append(
            {
                "id": p["id"],
                "name": p["name"],
                "models": models,
                "default_model": p["default_model"],
                "configured": _provider_configured(p["id"], repo_path),
                "requires_key": p["requires_key"],
                "accepts_base_url": p["accepts_base_url"],
                "local_server": p["local_server"],
                "env_vars": p["env_vars"],
                "setup_hint": p["setup_hint"],
            }
        )

    # Deferred: the estimator package pulls in the generation pipeline.
    from repowise.core.cost_estimator import lookup_cost

    rates = lookup_cost(model) if model else None
    # An unpriced model on a local provider costs nothing; anywhere else it
    # is unknown, and null rates make the dashboard show no estimate.
    spec = PROVIDER_SPECS.get(active_id or "")
    if rates is None and model and spec is not None and spec.local:
        rates = (0.0, 0.0)
    input_rate, output_rate = rates or (None, None)
    return {
        "active": {
            "provider": active_id,
            "model": model,
            "base_url": selection.display_base_url,
            "provenance": selection.provenance,
            "base_url_unconfirmed": _base_url_unconfirmed(selection, repo_path),
            "input_cost_per_1k": input_rate,
            "output_cost_per_1k": output_rate,
            "embedder": os.environ.get("REPOWISE_EMBEDDER", DEFAULT_EMBEDDER).lower(),
        },
        "providers": providers,
        "flag_only_providers": FLAG_ONLY_PROVIDERS,
        "embedders": _embedder_catalog(),
    }


def _base_url_unconfirmed(selection: LLMSelection, repo_path: str | Path | None) -> bool:
    """Whether chat refuses the repo's config.yaml base URL until the user
    confirms it (re-saving it from Settings does). No network."""
    if not selection.base_url or selection.base_url_source != "config.yaml":
        return False
    from repowise.core.providers.llm.resolve import UntrustedBaseUrlError, llm_provider_kwargs

    try:
        llm_provider_kwargs(selection, repo_path)
    except UntrustedBaseUrlError:
        return True
    except ValueError:
        return False
    return False


def get_active_provider(
    repo_id: str | None = None,
    repo_path: str | Path | None = None,
) -> tuple[str | None, str | None]:
    """Return (provider_id, model) for the active provider, optionally per-repo."""
    selection = resolve_for_repo(repo_id, repo_path)
    return selection.provider, selection.model


def get_configured_active_provider(
    repo_id: str | None = None,
    repo_path: str | Path | None = None,
) -> tuple[str | None, str | None]:
    """Like :func:`get_active_provider`, but ``(None, None)`` when the resolved
    provider still needs a key, so a caller can show setup instead of a 400."""
    provider_id, model = get_active_provider(repo_id, repo_path)
    if provider_id is None or not _provider_configured(provider_id, repo_path):
        return None, None
    return provider_id, model


def set_active_provider(
    provider_id: str,
    model: str | None = None,
    repo_id: str | None = None,
    *,
    repo_path: str | Path | None = None,
    base_url: Any = UNSET,
) -> None:
    """Persist the active provider/model (and optionally base URL).

    With ``repo_id`` the choice goes to that repo's ``.repowise/config.yaml``
    (``repo_path`` is required), the file the CLI reads too. Without it the
    global default in ``provider_config.json`` is set. ``model`` is any
    string, not only a catalog model. ``base_url`` left out keeps the current
    one; ``None`` or ``""`` clears it.
    """
    if provider_id not in _CATALOG_BY_ID:
        raise UnknownProviderError(f"Unknown provider: {provider_id}")
    choice = _Choice(
        provider_id,
        (model or "").strip() or None,
        base_url if base_url is UNSET else validate_base_url(base_url),
    )
    if repo_id:
        _save_repo_choice(choice, repo_id, repo_path)
    else:
        update_global_config(choice.apply_global)


@dataclass(frozen=True, slots=True)
class _Choice:
    """A validated provider/model/base URL; ``base_url`` may be ``UNSET`` (keep)."""

    provider: str
    model: str | None
    base_url: Any

    def apply_global(self, config: dict[str, Any]) -> None:
        config["active_provider"] = self.provider
        _set_or_pop(config, "active_model", self.model)
        if self.base_url is not UNSET:
            _set_or_pop(config, "active_base_url", self.base_url)


def _set_or_pop(config: dict[str, Any], key: str, value: Any) -> None:
    if value:
        config[key] = value
    else:
        config.pop(key, None)


def _save_repo_choice(choice: _Choice, repo_id: str, repo_path: str | Path | None) -> None:
    if repo_path is None:
        raise ValueError(f"Repository {repo_id} has no local checkout to save the provider in.")
    _migrate_repo_selection(repo_id, repo_path)
    save_repo_llm_selection(repo_path, choice.provider, choice.model, choice.base_url)
    if choice.base_url not in (UNSET, None):
        # Chosen here by the user, so stored keys may be sent to it.
        trust_base_url(repo_path, choice.base_url)


def set_api_key(
    provider_id: str,
    key: str | None,
    repo_path: str | Path | None = None,
) -> None:
    """Store or remove an API key for a provider.

    Always updates the server-global store. When ``repo_path`` is given, the
    key is *also* mirrored into that repo's ``.repowise/.env`` under the
    provider's canonical env var, so a later ``repowise`` CLI run in that repo
    (which reads ``.env``, not the server store) picks up a key added from the
    web UI. A ``None`` key removes it from both places.

    Writing ``.env`` is a deliberately OSS-server-only step: a hosted
    deployment resolves keys from its own vault, so it never passes
    ``repo_path`` here. The low-level file write is the shared
    ``core.repo_config.save_repo_env_key`` primitive (reused by the CLI), but
    the decision to mirror a key to disk lives here in the server layer.
    """
    if provider_id not in _CATALOG_BY_ID:
        raise UnknownProviderError(f"Unknown provider: {provider_id}")
    if key is not None and ("\n" in key or "\r" in key):
        # Reject before writing anything so a bad value can't inject extra env
        # lines nor leave a partial store update. A real key has no newline.
        raise ValueError("API key must not contain a newline")
    def change(config: dict[str, Any]) -> None:
        keys = config.setdefault("keys", {})
        if key:
            keys[provider_id] = key
        else:
            keys.pop(provider_id, None)

    update_global_config(change)

    if repo_path is not None:
        _mirror_key_to_repo_env(provider_id, key, repo_path)


def _mirror_key_to_repo_env(
    provider_id: str,
    key: str | None,
    repo_path: str | Path,
) -> None:
    """Write (or clear) a provider key in a repo's ``.repowise/.env``.

    Uses the provider's first catalog env var as the canonical name (e.g.
    ``ANTHROPIC_API_KEY``). Providers that take no key (empty ``env_keys``,
    e.g. Ollama) have nothing to mirror. Best-effort: a filesystem error here
    must not fail the API call, since the server store was already updated.
    """
    env_keys = _CATALOG_BY_ID[provider_id].get("env_keys", [])
    if not env_keys:
        return
    from repowise.core.repo_config import save_repo_env_key

    try:
        save_repo_env_key(repo_path, env_keys[0], key)
    except OSError:
        logger.warning("Failed to mirror %s key into %s/.repowise/.env", provider_id, repo_path)


def get_chat_provider_instance(
    repo_path: str | Path | None = None,
    repo_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
):
    """Create a chat provider instance, resolved for a specific repo.

    Uses the shared resolver, so chat builds what ``repowise update`` would:
    the repo's ``config.yaml`` and ``.env`` without the server loading any
    repo's ``.env`` into its own environment. An explicit
    ``provider_override``/``model_override`` (a per-request UI choice) wins.

    Returns a provider that implements both BaseProvider and ChatProvider.
    """
    from repowise.core.providers.llm.registry import get_provider
    from repowise.core.providers.llm.resolve import llm_provider_kwargs

    selection = resolve_for_repo(
        repo_id,
        repo_path,
        provider_override=provider_override,
        model_override=model_override,
    )
    if not selection.provider:
        raise NoProviderConfiguredError("No active provider configured. Set an API key first.")
    kwargs = llm_provider_kwargs(selection, repo_path)
    return get_provider(selection.provider, with_rate_limiter=False, **kwargs)
