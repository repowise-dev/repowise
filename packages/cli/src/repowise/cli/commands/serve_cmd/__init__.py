"""``repowise serve`` command package.

* :mod:`.command`: the Click command
* :mod:`._advisory`: periodic update advisory
* :mod:`._lock`: discovery lockfile
* :mod:`._embedder`: embedder and provider env setup
* :mod:`._ports`: port probing
* :mod:`._web_assets`: node/npm detection and web bundle resolution
* :mod:`._frontend`: launching the web UI

Every name is re-exported so ``repowise.cli.commands.serve_cmd`` keeps its
import surface. Patch a name on the submodule that defines it: a patch here
does not reach lookups inside the submodules.
"""

from __future__ import annotations

from ._advisory import (
    _UPDATE_ADVISORY_INTERVAL_S,
    _start_update_advisory_loop,
    _update_advisory_tick,
)
from ._embedder import (
    _GLOBAL_CONFIG_DIR,
    _get_or_prompt_api_key,
    _load_local_provider_config,
    _save_global_embedder,
    _set_api_key_env,
    _setup_embedder,
)
from ._frontend import _start_frontend
from ._lock import _SERVE_LOCK_NAME, _remove_serve_lock, _serve_lock_path, _write_serve_lock
from ._ports import _find_free_port, _is_port_free
from ._web_assets import (
    _GITHUB_REPO,
    _MARKER_FILE,
    _NODE_MIN_MAJOR,
    _WEB_CACHE_DIR,
    _build_local_web,
    _download_web,
    _find_local_web,
    _local_build_is_stale,
    _node_available,
    _node_major_version,
    _npm_available,
    _safe_extract_tar,
    _web_is_cached,
)
from .command import serve_command

__all__ = [
    "_GITHUB_REPO",
    "_GLOBAL_CONFIG_DIR",
    "_MARKER_FILE",
    "_NODE_MIN_MAJOR",
    "_SERVE_LOCK_NAME",
    "_UPDATE_ADVISORY_INTERVAL_S",
    "_WEB_CACHE_DIR",
    "_build_local_web",
    "_download_web",
    "_find_free_port",
    "_find_local_web",
    "_get_or_prompt_api_key",
    "_is_port_free",
    "_load_local_provider_config",
    "_local_build_is_stale",
    "_node_available",
    "_node_major_version",
    "_npm_available",
    "_remove_serve_lock",
    "_safe_extract_tar",
    "_save_global_embedder",
    "_serve_lock_path",
    "_set_api_key_env",
    "_setup_embedder",
    "_start_frontend",
    "_start_update_advisory_loop",
    "_update_advisory_tick",
    "_web_is_cached",
    "_write_serve_lock",
    "serve_command",
]
