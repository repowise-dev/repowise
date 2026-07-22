"""Provider configuration management — API keys, active provider, model selection.

Stores configuration in a server-side JSON file (``_config_path``). Two
resolution chains run here, both most-specific-first:

**API key** (:func:`_get_key_for_provider`):
  1. process environment (``ANTHROPIC_API_KEY`` etc.)
  2. the target repo's ``.repowise/.env`` (read into a dict, never applied to
     ``os.environ``, so one repo's key can't leak into another in workspace
     mode)
  3. the server-global stored key set through :func:`set_api_key`

**Active provider/model** (:func:`_resolve_active_for_repo`): per-repo UI
selection > the repo's ``config.yaml`` > server-global active selection >
``REPOWISE_PROVIDER`` / ``REPOWISE_MODEL`` env > auto-detect the first provider
with a usable key. See that function's docstring for the full rationale.

Because the CLI resolves keys from ``.repowise/.env`` (step 2) and never sees
the server store, :func:`set_api_key` mirrors a UI-added key into that file so a
later CLI run in the repo picks it up.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider catalog (hardcoded — add new providers here)
# ---------------------------------------------------------------------------

PROVIDER_CATALOG: list[dict[str, Any]] = [
    {
        "id": "gemini",
        "name": "Google Gemini",
        "default_model": "gemini-3.1-flash-lite-preview",
        "models": [
            "gemini-3.1-flash-lite-preview",
            "gemini-3-flash-preview",
            "gemini-3.1-pro-preview",
        ],
        "env_keys": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "requires_key": True,
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "default_model": "claude-sonnet-4-6",
        "models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "env_keys": ["ANTHROPIC_API_KEY"],
        "requires_key": True,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "default_model": "gpt-5.4-nano",
        "models": ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4"],
        "env_keys": ["OPENAI_API_KEY"],
        "requires_key": True,
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "default_model": "anthropic/claude-sonnet-4.6",
        "models": [
            "anthropic/claude-sonnet-4.6",
            "google/gemini-3.1-flash-lite-preview",
            "meta-llama/llama-4-maverick",
            "openai/gpt-4o",
        ],
        "env_keys": ["OPENROUTER_API_KEY"],
        "requires_key": True,
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "default_model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "env_keys": ["DEEPSEEK_API_KEY"],
        "requires_key": True,
    },
    {
        "id": "kimi",
        "name": "Kimi",
        "default_model": "kimi-for-coding",
        "models": [
            "kimi-for-coding",
            "kimi-for-coding-highspeed",
            "kimi-k2.5",
            "kimi-k2.6",
        ],
        "env_keys": ["KIMI_API_KEY"],
        "requires_key": True,
    },
    {
        "id": "ollama",
        "name": "Ollama (Local)",
        "default_model": "llama3.2",
        "models": ["llama3.2", "codellama", "deepseek-coder-v2", "qwen2.5-coder"],
        "env_keys": [],
        "requires_key": False,
    },
    {
        "id": "litellm",
        "name": "LiteLLM",
        "default_model": "groq/llama-3.1-70b-versatile",
        "models": ["groq/llama-3.1-70b-versatile"],
        "env_keys": [],
        "requires_key": True,
    },
    {
        "id": "opencode",
        "name": "OpenCode (Local CLI)",
        "default_model": "opencode/default",
        "models": [
            "opencode/default",
            "opencode/openai/gpt-5",
            "opencode/deepseek/deepseek-v4-pro",
        ],
        "env_keys": [],
        "requires_key": False,
    },
]

_CATALOG_BY_ID = {p["id"]: p for p in PROVIDER_CATALOG}


# ---------------------------------------------------------------------------
# Config file I/O
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    """Return the server's provider-config JSON path.

    ``REPOWISE_CONFIG_DIR`` wins when set; otherwise the file lives in the
    per-user ``~/.repowise`` config directory (the same home the CLI's global
    store and the default database use). The previous CWD-relative fallback
    meant a key was saved next to wherever the server happened to be launched
    and then invisible from any other working directory.
    """
    config_dir = os.environ.get("REPOWISE_CONFIG_DIR", "")
    if config_dir:
        return Path(config_dir) / "provider_config.json"
    return Path.home() / ".repowise" / "provider_config.json"


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read provider config, using defaults")
    return {}


def _save_config(config: dict[str, Any]) -> None:
    # Write-then-rename so a concurrent reader (or a second writer racing on
    # the read-modify-write) never sees a half-written file or a truncated one.
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_BASE_URL_ENV_VARS = {
    "anthropic": ["ANTHROPIC_BASE_URL"],
    "openai": ["OPENAI_BASE_URL"],
    "gemini": ["GEMINI_BASE_URL"],
    "ollama": ["OLLAMA_BASE_URL"],
    "deepseek": ["DEEPSEEK_BASE_URL"],
    "kimi": ["KIMI_BASE_URL"],
    "litellm": ["LITELLM_BASE_URL", "LITELLM_API_BASE"],
}


def _load_repo_context(repo_path: str | Path | None) -> tuple[dict[str, Any], dict[str, str]]:
    """Load a repo's ``config.yaml`` dict and ``.env`` dict for resolution.

    Returns ``({}, {})`` when no path is given so callers can resolve the
    server-global configuration unchanged. The ``.env`` is read into a plain
    dict (never ``os.environ``) so per-request, per-repo resolution can't leak
    one repo's keys into another's in a long-lived workspace server.
    """
    if repo_path is None:
        return {}, {}
    from repowise.core.repo_config import load_repo_config, load_repo_env

    try:
        cfg = load_repo_config(repo_path)
    except Exception:
        cfg = {}
    try:
        env = load_repo_env(repo_path)
    except Exception:
        env = {}
    return cfg, env


def _get_key_for_provider(provider_id: str, repo_env: dict[str, str] | None = None) -> str | None:
    """Resolve an API key: process env > repo ``.env`` > stored server config.

    ``repo_env`` is the target repo's ``.repowise/.env`` (parsed, not applied
    to ``os.environ``). Honouring it is what makes chat use the same key the
    user supplied at ``repowise init`` time, even in workspace mode where the
    server never loads any single repo's ``.env`` into its own environment.
    """
    catalog = _CATALOG_BY_ID.get(provider_id)
    if not catalog:
        return None

    for env_key in catalog.get("env_keys", []):
        val = os.environ.get(env_key)
        if val:
            return val
    if repo_env:
        for env_key in catalog.get("env_keys", []):
            val = repo_env.get(env_key)
            if val:
                return val

    config = _load_config()
    keys = config.get("keys", {})
    return keys.get(provider_id)


def _get_base_url_for_provider(
    provider_id: str,
    repo_env: dict[str, str] | None = None,
    repo_cfg: dict[str, Any] | None = None,
) -> str | None:
    """Resolve a provider base_url: process env > repo ``.env`` > repo config.

    Mirrors the CLI's resolution (``cli/helpers.resolve_provider``) so a
    local LiteLLM/OpenAI-compatible endpoint configured at init is reused by
    chat. The repo ``config.yaml`` may carry it under a per-provider section,
    e.g. ``openai: {base_url: http://localhost:4000/v1}``.
    """
    env_vars = _BASE_URL_ENV_VARS.get(provider_id, [])
    for env_var in env_vars:
        val = os.environ.get(env_var)
        if val:
            return val
    if repo_env:
        for env_var in env_vars:
            val = repo_env.get(env_var)
            if val:
                return val
    if repo_cfg:
        section = repo_cfg.get(provider_id)
        if isinstance(section, dict) and section.get("base_url"):
            return str(section["base_url"])
    return None


def _resolve_active_for_repo(
    repo_id: str | None,
    repo_cfg: dict[str, Any],
    repo_env: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the (provider, model) for a repo's chat, most specific first.

    Precedence:
      1. Per-repo UI selection persisted under ``repos[repo_id]`` — an
         explicit override the user made for *this* repo. Stored per-repo so
         selecting a model for one repo never shadows another's config (the
         original bug behind issue #426).
      2. The repo's own ``config.yaml`` (``provider`` + ``model``) written by
         ``repowise init`` — the seamless default.
      3. The server-global ``active_provider`` (legacy single-repo UI state).
      4. ``REPOWISE_PROVIDER`` / ``REPOWISE_MODEL`` env vars.
      5. Auto-detect the first provider with a usable key.
    """
    config = _load_config()

    def _with_default(pid: str | None, model: str | None) -> tuple[str | None, str | None]:
        if pid and not model:
            model = _CATALOG_BY_ID.get(pid, {}).get("default_model")
        return pid, model

    # 1. Per-repo persisted selection
    if repo_id:
        repos = config.get("repos") or {}
        sel = repos.get(repo_id)
        if isinstance(sel, dict) and sel.get("provider") in _CATALOG_BY_ID:
            return _with_default(sel["provider"], sel.get("model"))

    # 2. Repo config.yaml
    cfg_provider = repo_cfg.get("provider")
    if cfg_provider in _CATALOG_BY_ID:
        return _with_default(cfg_provider, repo_cfg.get("model"))

    # 3. Server-global active selection
    if config.get("active_provider") in _CATALOG_BY_ID:
        return _with_default(config["active_provider"], config.get("active_model"))

    # 4. Environment
    env_provider = os.environ.get("REPOWISE_PROVIDER")
    if env_provider in _CATALOG_BY_ID:
        return _with_default(env_provider, os.environ.get("REPOWISE_MODEL"))

    # 5. Auto-detect
    for p in PROVIDER_CATALOG:
        if _get_key_for_provider(p["id"], repo_env) or not p["requires_key"]:
            return _with_default(p["id"], None)

    return None, None


def list_provider_status(
    repo_id: str | None = None,
    repo_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return provider catalog + the active selection for ``repo_id``.

    When ``repo_path`` is given the active provider/model reflect that repo's
    own configuration (so the UI's model picker shows what chat will actually
    use), and a custom configured model not present in the static catalog is
    surfaced in that provider's ``models`` list so it's selectable.
    """
    repo_cfg, repo_env = _load_repo_context(repo_path)
    active_id, active_model = _resolve_active_for_repo(repo_id, repo_cfg, repo_env)

    providers = []
    for p in PROVIDER_CATALOG:
        has_key = bool(_get_key_for_provider(p["id"], repo_env))
        configured = has_key or not p["requires_key"]
        models = list(p["models"])
        # Surface a configured-but-not-cataloged model (e.g. a local LiteLLM
        # alias like ``gemma4``) so the picker can display the active choice.
        if p["id"] == active_id and active_model and active_model not in models:
            models.append(active_model)
        providers.append(
            {
                "id": p["id"],
                "name": p["name"],
                "models": models,
                "default_model": p["default_model"],
                "configured": configured,
            }
        )

    return {
        "active": {
            "provider": active_id,
            "model": active_model
            or (_CATALOG_BY_ID.get(active_id, {}).get("default_model") if active_id else None),
        },
        "providers": providers,
    }


def get_active_provider(
    repo_id: str | None = None,
    repo_path: str | Path | None = None,
) -> tuple[str | None, str | None]:
    """Return (provider_id, model) for the active provider, optionally per-repo."""
    repo_cfg, repo_env = _load_repo_context(repo_path)
    return _resolve_active_for_repo(repo_id, repo_cfg, repo_env)


def set_active_provider(
    provider_id: str,
    model: str | None = None,
    repo_id: str | None = None,
) -> None:
    """Persist the active provider/model selection.

    With ``repo_id`` the selection is scoped to that repo (under ``repos``),
    so it overrides only that repo's chat and never shadows other repos in a
    workspace. Without it, the legacy server-global selection is set.
    """
    if provider_id not in _CATALOG_BY_ID:
        raise ValueError(f"Unknown provider: {provider_id}")
    resolved_model = model or _CATALOG_BY_ID[provider_id]["default_model"]
    config = _load_config()
    if repo_id:
        repos = config.setdefault("repos", {})
        repos[repo_id] = {"provider": provider_id, "model": resolved_model}
    else:
        config["active_provider"] = provider_id
        config["active_model"] = resolved_model
    _save_config(config)


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
        raise ValueError(f"Unknown provider: {provider_id}")
    if key is not None and ("\n" in key or "\r" in key):
        # Reject before writing anything so a bad value can't inject extra env
        # lines nor leave a partial store update. A real key has no newline.
        raise ValueError("API key must not contain a newline")
    config = _load_config()
    keys = config.setdefault("keys", {})
    if key:
        keys[provider_id] = key
    else:
        keys.pop(provider_id, None)
    _save_config(config)

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

    Resolution draws on the target repo's ``.repowise/config.yaml`` and
    ``.repowise/.env`` (provider, model, API key, base_url) so chat uses the
    exact configuration set at ``repowise init`` — without the server having
    to load any repo's ``.env`` into its own process environment. An explicit
    ``provider_override``/``model_override`` (a per-request UI choice) wins.

    Returns a provider that implements both BaseProvider and ChatProvider.
    """
    from repowise.core.providers.llm.registry import get_provider

    repo_cfg, repo_env = _load_repo_context(repo_path)

    if provider_override:
        if provider_override not in _CATALOG_BY_ID:
            raise ValueError(f"Unknown provider: {provider_override}")
        provider_id = provider_override
        model = model_override
        # Inherit the repo's configured model when overriding only the
        # provider and that provider matches what init configured.
        if not model and repo_cfg.get("provider") == provider_id:
            model = repo_cfg.get("model")
        if not model:
            model = _CATALOG_BY_ID[provider_id]["default_model"]
    else:
        provider_id, model = _resolve_active_for_repo(repo_id, repo_cfg, repo_env)

    if not provider_id:
        raise ValueError("No active provider configured. Set an API key first.")

    api_key = _get_key_for_provider(provider_id, repo_env)
    base_url = _get_base_url_for_provider(provider_id, repo_env, repo_cfg)
    catalog = _CATALOG_BY_ID[provider_id]

    kwargs: dict[str, Any] = {"model": model or catalog["default_model"]}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    return get_provider(provider_id, with_rate_limiter=False, **kwargs)
