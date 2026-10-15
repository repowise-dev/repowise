"""Where a repo's LLM provider, model and endpoint come from, decided once.

Every entry point (CLI init/update, ``repowise serve`` chat and jobs, the MCP
server's ``get_answer``) asks :func:`resolve_llm`, so one repo resolves the
same way from all of them. Precedence, highest first:

1. an explicit choice: a CLI flag, or a per-request override from the web UI
2. the repo's ``.repowise/config.yaml`` (``provider``, ``model``, and
   ``<provider>: {base_url: ...}``)
3. the global default in ``provider_config.json`` (``active_provider``,
   ``active_model``, ``active_base_url``)
4. ``REPOWISE_PROVIDER`` / ``REPOWISE_MODEL`` and the provider's own
   ``*_BASE_URL`` variable, from the process env or the repo's ``.env``
5. the spec's default model, or auto-detection from the credentials present

API keys resolve on their own chain (:func:`resolve_api_key`): process env,
then the repo's ``.repowise/.env``, then keys stored in ``provider_config.json``.

Chat resolves on every request, so the three files are parsed once per
version: reads are cached by path and a digest of the bytes. Nothing in this
module touches the network.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from repowise.core.providers.llm.registry import (
    PROVIDER_API_KEY_ENVS,
    PROVIDER_AUTODETECT_ORDER,
    PROVIDER_BASE_URL_ENVS,
    EnvLookup,
    provider_credentials_present,
    provider_is_usable,
    provider_kwargs,
)
from repowise.core.providers.llm.specs import PROVIDER_SPECS

logger = logging.getLogger(__name__)

GLOBAL_CONFIG_FILENAME = "provider_config.json"

# ---------------------------------------------------------------------------
# Cached reads
# ---------------------------------------------------------------------------

_cache: dict[Path, tuple[bytes, Any]] = {}
_cache_lock = threading.Lock()


def cached_read(path: Path, parse: Callable[[Path], Any]) -> Any:
    """``parse(path)``, reused while the file's bytes are unchanged.

    Keyed on a digest of the content, not mtime: these files are a few hundred
    bytes, so reading them is cheap and parsing is the cost, and a same-size
    rewrite by another process inside one timestamp tick is still seen. The
    value is shared between callers, so it is read-only by contract. A missing
    file is ``{}``. ``parse`` handles its own errors, which lets a broken file
    warn once per version instead of on every request.
    """
    try:
        digest = hashlib.blake2b(path.read_bytes(), digest_size=16).digest()
    except OSError:
        return {}
    with _cache_lock:
        hit = _cache.get(path)
    if hit is not None and hit[0] == digest:
        return hit[1]
    value = parse(path)
    with _cache_lock:
        _cache[path] = (digest, value)
    return value


def forget_cached(path: Path | str) -> None:
    """Drop *path* from the read cache, for a writer that wants it gone now."""
    with _cache_lock:
        _cache.pop(Path(path), None)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def global_config_path() -> Path:
    """``$REPOWISE_CONFIG_DIR/provider_config.json``, else ``~/.repowise/``."""
    config_dir = os.environ.get("REPOWISE_CONFIG_DIR", "")
    base = Path(config_dir) if config_dir else Path.home() / ".repowise"
    return base / GLOBAL_CONFIG_FILENAME


def _parse_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read %s; using no stored provider settings", path)
        return {}
    return data if isinstance(data, dict) else {}


def _parse_repo_config(path: Path) -> dict[str, Any]:
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        return load_repo_config(path.parent.parent)
    except RepoConfigError as exc:
        # A broken config must surface, not resolve as defaults silently.
        logger.warning("%s; using no repo provider settings", exc)
    except Exception:
        logger.warning("Could not read %s; using no repo provider settings", path)
    return {}


def _parse_repo_env(path: Path) -> dict[str, str]:
    from repowise.core.repo_config import RepoConfigError, load_repo_env

    try:
        return load_repo_env(path.parent.parent)
    except RepoConfigError as exc:
        logger.warning("%s; using the process environment only", exc)
    except Exception:
        logger.warning("Could not read %s; using the process environment only", path)
    return {}


def read_global_config() -> Mapping[str, Any]:
    """The parsed ``provider_config.json``. Read-only: copy before editing."""
    return cached_read(global_config_path(), _parse_json)


_global_write_lock = threading.RLock()


def update_global_config(change: Callable[[dict[str, Any]], Any]) -> None:
    """Apply *change* to ``provider_config.json`` under one lock, atomically.

    Every writer (keys, the global default, migration, trusted URLs) goes
    through here, so two requests' read-modify-writes cannot interleave and
    lose one another's edit. The lock is per process; a second server process
    writing the same file at the same instant is the remaining ceiling.
    """
    with _global_write_lock:
        path = global_config_path()
        config = json.loads(json.dumps(dict(read_global_config())))
        change(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        # Keys are sensitive: owner read/write only where the OS honours it.
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        _replace(tmp, path)
        forget_cached(path)


def _replace(tmp: Path, path: Path) -> None:
    """``os.replace``, retried while a reader briefly holds *path* open.

    Windows refuses to replace a file another thread or process has open, and
    every resolution reads this one, so a write can land mid-read.
    """
    for _attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.01)
    os.replace(tmp, path)


def _repo_key(repo_path: Path | str) -> str:
    return str(Path(repo_path).resolve())


def base_url_trusted(repo_path: Path | str | None, url: str) -> bool:
    """Whether the user confirmed *url* for this repo (web UI or ``repowise init``)."""
    if repo_path is None:
        return False
    trusted = read_global_config().get("trusted_base_urls")
    if not isinstance(trusted, Mapping):
        return False
    urls = trusted.get(_repo_key(repo_path))
    return isinstance(urls, list) and url in urls


def trust_base_url(repo_path: Path | str, url: str) -> None:
    """Record that the user chose *url* for this repo, so stored keys may go there."""
    if base_url_trusted(repo_path, url):
        return

    def change(config: dict[str, Any]) -> None:
        trusted = config.get("trusted_base_urls")
        if not isinstance(trusted, dict):
            trusted = config["trusted_base_urls"] = {}
        urls = trusted.setdefault(_repo_key(repo_path), [])
        if url not in urls:
            urls.append(url)

    update_global_config(change)


def read_repo_config(repo_path: Path | str | None) -> Mapping[str, Any]:
    """The parsed ``.repowise/config.yaml``, ``{}`` when absent or broken."""
    if repo_path is None:
        return {}
    return cached_read(Path(repo_path) / ".repowise" / "config.yaml", _parse_repo_config)


def read_repo_env(repo_path: Path | str | None) -> Mapping[str, str]:
    """The parsed ``.repowise/.env``, never merged into ``os.environ``."""
    if repo_path is None:
        return {}
    return cached_read(Path(repo_path) / ".repowise" / ".env", _parse_repo_env)


def repo_getenv(repo_path: Path | str | None) -> EnvLookup:
    """Process env first, then *repo_path*'s ``.repowise/.env``; blanks are unset."""
    overlay = read_repo_env(repo_path)

    def getenv(name: str) -> str | None:
        return _clean(os.environ.get(name)) or _clean(overlay.get(name))

    return getenv


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMSelection:
    """The resolved provider, model and endpoint, and where each came from.

    Sources: the override label (``"flag"``, ``"request"``, or an env name for
    an override variable), ``"config.yaml"``, ``"global"``,
    ``"env:<VAR>"``, ``"default"``, ``"autodetect"``. ``None`` when unset.
    """

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    provider_source: str | None = None
    model_source: str | None = None
    base_url_source: str | None = None

    @property
    def provenance(self) -> dict[str, str | None]:
        return {
            "provider": self.provider_source,
            "model": self.model_source,
            "base_url": self.base_url_source,
        }

    @property
    def display_base_url(self) -> str | None:
        """``base_url`` safe to show: any user name or password removed."""
        return without_credentials(self.base_url) if self.base_url else None

    def describe(self) -> str:
        """One line for a CLI summary, e.g. ``openai (config.yaml), model x (default)``."""
        if not self.provider:
            return "no provider"
        text = f"{self.provider} ({self.provider_source})"
        if self.model:
            text += f", model {self.model} ({self.model_source})"
        if self.base_url:
            text += f", base URL {self.display_base_url} ({self.base_url_source})"
        return text


@dataclass(frozen=True, slots=True)
class _Layer:
    source: str
    provider: str | None
    model: str | None
    base_url: str | None = None
    provider_source: str | None = None
    model_source: str | None = None


def resolve_llm(
    repo_path: Path | str | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    override_source: str = "flag",
    known: Collection[str] | None = None,
    autodetect: bool = True,
    repo_layer: Mapping[str, Any] | None = None,
) -> LLMSelection:
    """Resolve the provider, model and base URL for *repo_path*.

    ``provider`` / ``model`` / ``base_url`` are the explicit choice, labelled
    ``override_source``. A model only applies to the provider it was chosen
    with: a layer naming a different provider contributes nothing, so a flag
    switching provider never inherits the repo's model for another provider.
    ``known`` limits which provider names count (the server's catalog), and
    ``autodetect=False`` stops at the configured layers. ``repo_layer`` stands
    in for ``config.yaml`` for a repo with no checkout to hold one (the
    server's stored choice for it), labelled ``provider_config.json``.
    """
    getenv = repo_getenv(repo_path)
    if repo_layer is not None and repo_path is None:
        cfg, repo_source = repo_layer, GLOBAL_CONFIG_FILENAME
    else:
        cfg, repo_source = read_repo_config(repo_path), "config.yaml"
    glob = read_global_config()

    layers = (
        _Layer(override_source, _clean(provider), _clean(model), _clean(base_url)),
        _Layer(repo_source, _clean(cfg.get("provider")), _clean(cfg.get("model"))),
        _Layer(
            "global",
            _clean(glob.get("active_provider")),
            _clean(glob.get("active_model")),
            _clean(glob.get("active_base_url")),
        ),
        _Layer(
            "env",
            getenv("REPOWISE_PROVIDER"),
            getenv("REPOWISE_MODEL"),
            provider_source="env:REPOWISE_PROVIDER",
            model_source="env:REPOWISE_MODEL",
        ),
    )

    def counts(name: str | None) -> bool:
        return bool(name) and (known is None or name in known)

    chosen: str | None = None
    chosen_source: str | None = None
    for layer in layers:
        if counts(layer.provider):
            chosen = layer.provider
            chosen_source = layer.provider_source or layer.source
            break
    if chosen is None and autodetect:
        chosen = _autodetect(getenv, glob, known)
        chosen_source = "autodetect" if chosen else None
    if chosen is None:
        return LLMSelection()

    def applies(layer: _Layer) -> bool:
        return layer.provider is None or layer.provider == chosen

    resolved_model, model_source = None, None
    for layer in layers:
        if layer.model and applies(layer):
            resolved_model, model_source = layer.model, layer.model_source or layer.source
            break
    if resolved_model is None:
        spec = PROVIDER_SPECS.get(chosen)
        if spec is not None:
            resolved_model, model_source = spec.default_model, "default"

    resolved_url, url_source = _resolve_base_url(chosen, layers, cfg, getenv)

    return LLMSelection(
        provider=chosen,
        model=resolved_model,
        base_url=resolved_url,
        provider_source=chosen_source,
        model_source=model_source,
        base_url_source=url_source,
    )


def _resolve_base_url(
    chosen: str,
    layers: tuple[_Layer, ...],
    cfg: Mapping[str, Any],
    getenv: EnvLookup,
) -> tuple[str | None, str | None]:
    override, repo, glob = layers[0], layers[1], layers[2]
    if override.base_url and override.provider in (None, chosen):
        return override.base_url, override.source
    section = cfg.get(chosen)
    if isinstance(section, Mapping) and _clean(section.get("base_url")):
        return _clean(section.get("base_url")), repo.source
    if glob.base_url and glob.provider == chosen:
        return glob.base_url, "global"
    for var in PROVIDER_BASE_URL_ENVS.get(chosen, ()):
        value = getenv(var)
        if value:
            return value, f"env:{var}"
    return None, None


def _autodetect(
    getenv: EnvLookup, glob: Mapping[str, Any], known: Collection[str] | None
) -> str | None:
    stored = glob.get("keys") if isinstance(glob.get("keys"), Mapping) else {}
    for name in PROVIDER_AUTODETECT_ORDER:
        if known is not None and name not in known:
            continue
        if provider_credentials_present(name, getenv) or _clean(stored.get(name)):
            return name
    return None


def resolve_api_key(
    provider: str, repo_path: Path | str | None = None
) -> tuple[str | None, str | None]:
    """``(key, source)``: process env > repo ``.repowise/.env`` > stored key."""
    env_vars = PROVIDER_API_KEY_ENVS.get(provider, ())
    for var in env_vars:
        value = _clean(os.environ.get(var))
        if value:
            return value, f"env:{var}"
    overlay = read_repo_env(repo_path)
    for var in env_vars:
        value = _clean(overlay.get(var))
        if value:
            return value, ".repowise/.env"
    keys = read_global_config().get("keys")
    value = _clean(keys.get(provider)) if isinstance(keys, Mapping) else None
    if value:
        return value, GLOBAL_CONFIG_FILENAME
    return None, None


def provider_ready(provider: str, repo_path: Path | str | None = None) -> bool:
    """Whether *provider* has what it needs, counting stored keys too."""
    return provider_is_usable(provider, repo_getenv(repo_path)) or bool(
        resolve_api_key(provider, repo_path)[0]
    )


def llm_provider_kwargs(
    selection: LLMSelection, repo_path: Path | str | None = None
) -> dict[str, Any]:
    """Constructor kwargs for *selection*: model, key, base URL, repo path."""
    if not selection.provider:
        raise ValueError("No provider to build")
    # The registry still owns the name -> repo_path mapping; key and URL come
    # from the chains above, so its env reads are switched off.
    kwargs = provider_kwargs(
        selection.provider,
        model=selection.model,
        repo_path=repo_path,
        getenv=lambda _name: None,
    )
    api_key, key_source = resolve_api_key(selection.provider, repo_path)
    if api_key:
        kwargs["api_key"] = api_key
    if selection.base_url:
        if selection.base_url_source == "config.yaml":
            _check_repo_base_url(selection, repo_path, key_source)
        kwargs["base_url"] = selection.base_url
    return kwargs


class UntrustedBaseUrlError(ValueError):
    """A committed ``config.yaml`` points a key from elsewhere at an unconfirmed host."""


def _check_repo_base_url(
    selection: LLMSelection, repo_path: Path | str | None, key_source: str | None
) -> None:
    """Refuse to send a key to a ``config.yaml`` base URL nobody here confirmed.

    ``config.yaml`` can be committed, so a cloned repo could otherwise redirect
    the user's exported or stored key to any host. Allowed: no key at all, a
    key from this repo's own ``.env``, a loopback host, or a URL the user set
    through the web UI or ``repowise init`` on this machine.
    """
    url = selection.base_url or ""
    try:
        validate_base_url(url)
    except ValueError as exc:
        raise ValueError(
            f"{selection.provider} base_url in .repowise/config.yaml: {exc}"
        ) from None
    if key_source is None or key_source == ".repowise/.env":
        return
    if _is_loopback(url) or base_url_trusted(repo_path, url):
        return
    raise UntrustedBaseUrlError(
        f"This repo's .repowise/config.yaml sends {selection.provider} requests to "
        f"{selection.display_base_url}, which has not been confirmed on this machine, so "
        "your API key was not sent there. Confirm the URL in Settings or with "
        "`repowise init`, or put the key in the repo's .repowise/.env."
    )


def _is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def without_credentials(url: str) -> str:
    """*url* with any ``user:password@`` removed, for display and errors."""
    parsed = urlparse(url)
    if not (parsed.username or parsed.password):
        return url
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return parsed._replace(netloc=netloc).geturl()


# ---------------------------------------------------------------------------
# Writing a repo's selection
# ---------------------------------------------------------------------------

#: "Leave this field as it is", distinct from ``None`` (clear it).
UNSET: Any = object()


def validate_base_url(value: str | None) -> str | None:
    """A trimmed ``http(s)://host...`` URL without credentials, or ``None`` for empty."""
    text = _clean(value)
    if text is None:
        return None
    parsed = urlparse(text)
    if parsed.username or parsed.password:
        raise ValueError(
            "Base URL must not contain a user name or password. "
            "Save the API key in Settings or .repowise/.env instead."
        )
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(
            "Base URL must start with http:// or https:// and name a host, "
            f"got {without_credentials(text)!r}"
        )
    return text


def apply_llm_selection(
    config: dict[str, Any],
    provider: str,
    model: str | None,
    base_url: Any = UNSET,
) -> dict[str, Any]:
    """Set provider/model (and optionally ``<provider>.base_url``) in *config*.

    ``model=None`` removes the key so the provider's default applies. Leaving
    ``base_url`` out keeps the section as it is; ``None`` or ``""`` clears it.
    """
    config["provider"] = provider
    if model:
        config["model"] = model
    else:
        config.pop("model", None)
    if base_url is not UNSET:
        current = config.get(provider)
        section = dict(current) if isinstance(current, Mapping) else {}
        if base_url:
            section["base_url"] = base_url
        else:
            section.pop("base_url", None)
        if section:
            config[provider] = section
        else:
            config.pop(provider, None)
    return config


def save_repo_llm_selection(
    repo_path: Path | str,
    provider: str,
    model: str | None,
    base_url: Any = UNSET,
) -> None:
    """Persist a selection into ``.repowise/config.yaml``, keeping other keys.

    A broken file raises :class:`~repowise.core.repo_config.RepoConfigError`
    and is left untouched, never overwritten with only these keys.
    """
    from repowise.core.repo_config import load_repo_config, save_repo_config

    config = load_repo_config(repo_path)
    save_repo_config(repo_path, apply_llm_selection(config, provider, model, base_url))


__all__ = [
    "UNSET",
    "LLMSelection",
    "UntrustedBaseUrlError",
    "apply_llm_selection",
    "base_url_trusted",
    "cached_read",
    "clear_cache",
    "forget_cached",
    "global_config_path",
    "llm_provider_kwargs",
    "provider_ready",
    "read_global_config",
    "read_repo_config",
    "read_repo_env",
    "repo_getenv",
    "resolve_api_key",
    "resolve_llm",
    "save_repo_llm_selection",
    "trust_base_url",
    "update_global_config",
    "validate_base_url",
    "without_credentials",
]
