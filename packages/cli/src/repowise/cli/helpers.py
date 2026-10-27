"""Shared CLI utilities: a facade over ``repowise.cli._helpers``.

Every name lives in a focused module under ``_helpers/`` and is re-exported
here. Moved code looks names that tests patch on this module up through it at
call time, so ``monkeypatch.setattr(helpers, ...)`` keeps reaching them.
"""

from __future__ import annotations

from typing import TypeVar

from repowise.cli._helpers.config import (
    HOOK_REPLACEMENT_SURFACES as HOOK_REPLACEMENT_SURFACES,
)
from repowise.cli._helpers.config import (
    NO_SAVE_KEY_ENV as NO_SAVE_KEY_ENV,
)
from repowise.cli._helpers.config import (
    _clean_flag as _clean_flag,
)
from repowise.cli._helpers.config import (
    _persist_provider_key as _persist_provider_key,
)
from repowise.cli._helpers.config import (
    _trust_configured_base_url as _trust_configured_base_url,
)
from repowise.cli._helpers.config import (
    config_fingerprint as config_fingerprint,
)
from repowise.cli._helpers.config import (
    load_config as load_config,
)
from repowise.cli._helpers.config import (
    resolve_max_file_pages as resolve_max_file_pages,
)
from repowise.cli._helpers.config import (
    resolve_reasoning as resolve_reasoning,
)
from repowise.cli._helpers.config import (
    save_config as save_config,
)
from repowise.cli._helpers.config import (
    save_config_partial as save_config_partial,
)
from repowise.cli._helpers.config import (
    save_distill_commands_enabled as save_distill_commands_enabled,
)
from repowise.cli._helpers.config import (
    save_hook_surface_enabled as save_hook_surface_enabled,
)
from repowise.cli._helpers.console import (
    MACHINE_OUTPUT_LOGGER_NAMES as MACHINE_OUTPUT_LOGGER_NAMES,
)
from repowise.cli._helpers.console import (
    console as console,
)
from repowise.cli._helpers.console import (
    err_console as err_console,
)
from repowise.cli._helpers.console import (
    run_async as run_async,
)
from repowise.cli._helpers.console import (
    silence_logs_for_machine_output as silence_logs_for_machine_output,
)
from repowise.cli._helpers.console import (
    silence_logs_for_machine_output_until_close as silence_logs_for_machine_output_until_close,
)
from repowise.cli._helpers.console import (
    warn as warn,
)
from repowise.cli._helpers.db import (
    _RECONCILE_BUSY_TIMEOUT_MS as _RECONCILE_BUSY_TIMEOUT_MS,
)
from repowise.cli._helpers.db import (
    STALE_INDEX_MESSAGE as STALE_INDEX_MESSAGE,
)
from repowise.cli._helpers.db import (
    StaleIndexError as StaleIndexError,
)
from repowise.cli._helpers.db import (
    _missing_sqlite_file as _missing_sqlite_file,
)
from repowise.cli._helpers.db import (
    db_configured as db_configured,
)
from repowise.cli._helpers.db import (
    get_db_url_for_repo as get_db_url_for_repo,
)
from repowise.cli._helpers.db import (
    reconcile_schema_best_effort as reconcile_schema_best_effort,
)
from repowise.cli._helpers.db import (
    repo_index_session as repo_index_session,
)
from repowise.cli._helpers.git import (
    _COMMIT_ID_RE as _COMMIT_ID_RE,
)
from repowise.cli._helpers.git import (
    as_commit_id as as_commit_id,
)
from repowise.cli._helpers.git import (
    get_head_commit as get_head_commit,
)
from repowise.cli._helpers.git import (
    head_commit_ts as head_commit_ts,
)
from repowise.cli._helpers.markers import (
    UPDATE_LOG_FILENAME as UPDATE_LOG_FILENAME,
)
from repowise.cli._helpers.markers import (
    UPDATE_LOG_KEEP_TAIL_BYTES as UPDATE_LOG_KEEP_TAIL_BYTES,
)
from repowise.cli._helpers.markers import (
    UPDATE_LOG_MAX_BYTES as UPDATE_LOG_MAX_BYTES,
)
from repowise.cli._helpers.markers import (
    UPDATE_PENDING_FILENAME as UPDATE_PENDING_FILENAME,
)
from repowise.cli._helpers.markers import (
    UPDATE_QUEUED_FILENAME as UPDATE_QUEUED_FILENAME,
)
from repowise.cli._helpers.markers import (
    UPDATE_QUEUED_STALE_AFTER_SECONDS as UPDATE_QUEUED_STALE_AFTER_SECONDS,
)
from repowise.cli._helpers.markers import (
    _pending_commit_still_ahead as _pending_commit_still_ahead,
)
from repowise.cli._helpers.markers import (
    _update_pending_path as _update_pending_path,
)
from repowise.cli._helpers.markers import (
    _update_queued_path as _update_queued_path,
)
from repowise.cli._helpers.markers import (
    clear_update_pending as clear_update_pending,
)
from repowise.cli._helpers.markers import (
    clear_update_queued as clear_update_queued,
)
from repowise.cli._helpers.markers import (
    consume_update_pending as consume_update_pending,
)
from repowise.cli._helpers.markers import (
    read_update_pending as read_update_pending,
)
from repowise.cli._helpers.markers import (
    read_update_queued as read_update_queued,
)
from repowise.cli._helpers.markers import (
    rotate_update_log_if_needed as rotate_update_log_if_needed,
)
from repowise.cli._helpers.markers import (
    update_log_path as update_log_path,
)
from repowise.cli._helpers.markers import (
    write_update_pending as write_update_pending,
)
from repowise.cli._helpers.markers import (
    write_update_queued as write_update_queued,
)
from repowise.cli._helpers.paths import (
    REPOWISE_DIR as REPOWISE_DIR,
)
from repowise.cli._helpers.paths import (
    STATE_FILENAME as STATE_FILENAME,
)
from repowise.cli._helpers.paths import (
    _same_path as _same_path,
)
from repowise.cli._helpers.paths import (
    ensure_repowise_dir as ensure_repowise_dir,
)
from repowise.cli._helpers.paths import (
    find_repowise_repo_root as find_repowise_repo_root,
)
from repowise.cli._helpers.paths import (
    find_workspace_root as find_workspace_root,
)
from repowise.cli._helpers.paths import (
    get_repowise_dir as get_repowise_dir,
)
from repowise.cli._helpers.paths import (
    resolve_repo_path as resolve_repo_path,
)
from repowise.cli._helpers.paths import (
    user_global_dir as user_global_dir,
)
from repowise.cli._helpers.providers import (
    _build_provider as _build_provider,
)
from repowise.cli._helpers.providers import (
    _no_provider_message as _no_provider_message,
)
from repowise.cli._helpers.providers import (
    _provider_kwargs_or_error as _provider_kwargs_or_error,
)
from repowise.cli._helpers.providers import (
    resolve_explicit_provider_or_prompt as resolve_explicit_provider_or_prompt,
)
from repowise.cli._helpers.providers import (
    resolve_provider as resolve_provider,
)
from repowise.cli._helpers.providers import (
    resolve_provider_or_prompt as resolve_provider_or_prompt,
)
from repowise.cli._helpers.providers import (
    validate_provider_config as validate_provider_config,
)
from repowise.cli._helpers.state import (
    ONBOARDING_SLOTS_OFFERED_KEY as ONBOARDING_SLOTS_OFFERED_KEY,
)
from repowise.cli._helpers.state import (
    load_state as load_state,
)
from repowise.cli._helpers.state import (
    save_state as save_state,
)
from repowise.cli._helpers.state import (
    stamp_offered_slots as stamp_offered_slots,
)
from repowise.cli._helpers.target import (
    CommandTarget as CommandTarget,
)
from repowise.cli._helpers.target import (
    WorkspaceNotFound as WorkspaceNotFound,
)
from repowise.cli._helpers.target import (
    cross_repo_cut_inputs as cross_repo_cut_inputs,
)
from repowise.cli._helpers.target import (
    resolve_command_target as resolve_command_target,
)
from repowise.core.repo_config import CONFIG_FILENAME as CONFIG_FILENAME

# Update lock — coordinates concurrent `repowise update` invocations and lets
# the augment hook suppress stale-wiki warnings while a refresh is in flight.
# One shared implementation in core (the workspace updater used to carry a
# hand-synced copy); re-exported here for the existing CLI/hook imports.
from repowise.core.update_lock import (
    UPDATE_LOCK_FILENAME as UPDATE_LOCK_FILENAME,
)
from repowise.core.update_lock import (
    UPDATE_LOCK_STALE_AFTER_SECONDS as UPDATE_LOCK_STALE_AFTER_SECONDS,
)
from repowise.core.update_lock import (
    read_update_lock as read_update_lock,
)
from repowise.core.update_lock import (
    release_update_lock as release_update_lock,
)
from repowise.core.update_lock import (
    try_acquire_update_lock as try_acquire_update_lock,
)

T = TypeVar("T")
