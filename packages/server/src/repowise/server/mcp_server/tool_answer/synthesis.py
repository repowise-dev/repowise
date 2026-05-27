"""Provider resolution and cache-key hashing for get_answer.

Recovers the LLM provider the user configured for ``repowise init`` so the
MCP server can synthesise without a separate config, and hashes the question
for the answer cache.
"""

from __future__ import annotations

import hashlib
import json as _json
import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger("repowise.mcp.answer")


def _hash_question(question: str) -> str:
    """Stable SHA-256 of the normalized question. Lowercase + strip + collapse ws."""
    norm = " ".join(question.lower().strip().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


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

    state_path = repo_path / ".repowise" / "state.json"
    env_path = repo_path / ".repowise" / ".env"

    name: str | None = None
    model: str | None = None
    overlay: dict[str, str] = {}

    try:
        if state_path.is_file():
            data = _json.loads(state_path.read_text(encoding="utf-8"))
            name = data.get("provider") or None
            model = data.get("model") or None
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

    Avoids the click dependency from the cli package. Returns a BaseProvider
    or None if no API key / provider is configured.

    Resolution order: process env vars first, then ``.repowise/state.json``
    + ``.repowise/.env`` for the active repo. The persisted values are the
    same ones ``repowise init`` and ``repowise update`` use, so get_answer
    follows the user's existing provider choice without a separate config.
    """
    try:
        from repowise.core.providers.llm.registry import get_provider
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

    def _try(provider_name: str, **kwargs: Any):
        try:
            return get_provider(provider_name, **kwargs)
        except Exception:
            _log.warning("get_provider(%s) failed", provider_name, exc_info=True)
            return None

    def _resolve_base_url(provider_name: str) -> str | None:
        mapping = {
            "openai": ["OPENAI_BASE_URL"],
            "anthropic": ["ANTHROPIC_BASE_URL"],
            "gemini": ["GEMINI_BASE_URL"],
            "deepseek": ["DEEPSEEK_BASE_URL"],
            "ollama": ["OLLAMA_BASE_URL"],
            "litellm": ["LITELLM_BASE_URL", "LITELLM_API_BASE"],
        }
        for env_var in mapping.get(provider_name, []):
            val = _env(env_var)
            if val:
                return val
        return None

    # Explicit selection wins.
    if name:
        kw: dict[str, Any] = {}
        if model:
            kw["model"] = model
        if name == "anthropic" and _env("ANTHROPIC_API_KEY"):
            kw["api_key"] = _env("ANTHROPIC_API_KEY")
        elif name == "openai" and _env("OPENAI_API_KEY"):
            kw["api_key"] = _env("OPENAI_API_KEY")
        elif name == "deepseek" and _env("DEEPSEEK_API_KEY"):
            kw["api_key"] = _env("DEEPSEEK_API_KEY")
        elif name == "gemini" and (_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")):
            kw["api_key"] = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
        base_url = _resolve_base_url(name)
        if base_url:
            kw["base_url"] = base_url
        return _try(name, **kw)

    # Auto-detect from API keys.
    if _env("ANTHROPIC_API_KEY"):
        kw = {"api_key": _env("ANTHROPIC_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("anthropic")
        if base_url:
            kw["base_url"] = base_url
        return _try("anthropic", **kw)
    if _env("OPENAI_API_KEY"):
        kw = {"api_key": _env("OPENAI_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("openai")
        if base_url:
            kw["base_url"] = base_url
        return _try("openai", **kw)
    if _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
        kw = {"api_key": _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("gemini")
        if base_url:
            kw["base_url"] = base_url
        return _try("gemini", **kw)
    if _env("OLLAMA_BASE_URL"):
        kw = {"base_url": _env("OLLAMA_BASE_URL")}
        if model:
            kw["model"] = model
        return _try("ollama", **kw)
    if _env("DEEPSEEK_API_KEY"):
        kw = {"api_key": _env("DEEPSEEK_API_KEY")}
        if model:
            kw["model"] = model
        base_url = _resolve_base_url("deepseek")
        if base_url:
            kw["base_url"] = base_url
        return _try("deepseek", **kw)
    return None
