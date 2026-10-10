"""Migrations, startup, shutdown and crash recovery run once, so a cause there
is ``expected`` with reason ``cold_path``, not queued work.

The positives are the dogfood rows that led real performance queues; the
negatives are request-path names that share a word with them.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.causal import execution_context
from repowise.core.analysis.health.perf.cold_paths import is_cold_path
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.perf.opportunity_rank import default_queue_exclusion


def _finding(path: str, function: str) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name=function,
        line_start=10,
        line_end=10,
        details={
            "boundary_kind": "db",
            "cross_function": False,
            "path": [],
            "loop_magnitude": "grows_with_data",
        },
        health_impact=0.0,
        dimension="performance",
    )


@pytest.mark.parametrize(
    ("path", "function"),
    [
        ("hermes_cli/kanban_db.py", "_migrate_add_optional_columns"),
        ("plugins/memory/honcho/cli.py", "cmd_migrate"),
        ("src/plugins/services.ts", "startPluginServices"),
        ("src/gateway/mcp-http.ts", "startMcpLoopbackServer"),
        ("packages/server/src/repowise/server/workspace_startup.py", "_open_member_dbs"),
        ("hermes_cli/session_recovery.py", "_reconstruct_missing_sessions"),
        ("cron/executions.py", "recover_interrupted_executions"),
        ("src/agents/subagent-orphan-recovery.ts", "recoverOrphanedSubagentSessions"),
        ("gateway/run.py", "GatewayRunner::_on_shutdown"),
    ],
)
def test_cold_names(path: str, function: str) -> None:
    assert is_cold_path(path, function)


@pytest.mark.parametrize(
    ("path", "function"),
    [
        ("app/views/auth.py", "recover_password"),
        ("app/account_recovery.py", "send_reset_link"),
        ("gateway/session.py", "SessionStore::_recover_session_from_db"),
        ("extensions/telegram/src/network-errors.ts", "isRecoverableTelegramNetworkError"),
        ("src/agents/chat.ts", "startConversation"),
        ("src/gateway/server/plugins-http.ts", "createGatewayPluginRequestHandler"),
        ("app/api/orders.py", "list_orders"),
        ("app/settings.py", "migrate_user_settings"),
        ("app/db/schema.py", "apply_migration_step"),
        ("app/db/schema.py", "_load_migration_state"),
        ("app/testing.py", "start_server"),
        ("app/workers.py", "stop_service"),
        ("app/db.py", "teardown"),
        ("app/startup.py", "auth_middleware"),
        ("app/shutdown.py", "handle_request"),
        ("app/uploads.py", "recover_aborted_upload"),
        ("src/plugins/startup-trace-segment.ts", "encodeStartupTraceSegment"),
        ("app/api/orders.py", None),
    ],
)
def test_request_path_names_stay_warm(path: str, function: str | None) -> None:
    assert not is_cold_path(path, function)


def test_a_migration_loop_leaves_the_default_queue_as_expected() -> None:
    opportunity = build_performance_opportunities(
        [_finding("hermes_cli/kanban_db.py", "_migrate_add_optional_columns")]
    )[0]

    assert opportunity.execution_context == "production"
    assert opportunity.actionability_state == "expected"
    assert opportunity.actionability_reason == "cold_path"
    assert opportunity.fix is None
    assert default_queue_exclusion(opportunity) == "cold_path"


def test_a_request_handler_named_recover_password_stays_queued() -> None:
    opportunity = build_performance_opportunities(
        [_finding("app/views/auth.py", "recover_password")]
    )[0]

    assert opportunity.actionability_state == "advisory"
    assert default_queue_exclusion(opportunity) is None


def test_one_warm_member_keeps_a_shared_group_queued() -> None:
    sink = ("store.py::save", "db.py::execute")
    rows = [
        _finding("app/startup.py", "warm_cache"),
        _finding("app/api/orders.py", "create_order"),
    ]
    owners = ("app/startup.py::warm_cache", "app/api/orders.py::create_order")
    for row, owner in zip(rows, owners, strict=True):
        row.details.update(cross_function=True, path=[owner, *sink])

    opportunity = build_performance_opportunities(rows)[0]

    assert opportunity.intervention_symbol == "store.py::save"
    assert opportunity.actionability_state == "advisory"


def test_a_cold_name_does_not_move_the_identity_kernel() -> None:
    assert execution_context("hermes_cli/session_recovery.py") == "production"


def test_vitest_suite_modules_are_test_code() -> None:
    assert execution_context("src/gateway/server.auth.default-token.suite.ts") == "test"


def test_a_cold_group_with_no_strategy_is_expected_too() -> None:
    row = _finding("hermes_cli/kanban_db.py", "_migrate_add_optional_columns")
    row.details["loop_key_unused"] = True

    opportunity = build_performance_opportunities([row])[0]

    assert opportunity.actionability_reason == "cold_path"


def test_gated_off_takes_precedence_over_cold_path() -> None:
    row = _finding("hermes_cli/kanban_db.py", "_migrate_add_optional_columns")
    row.details["gated_off"] = True

    opportunity = build_performance_opportunities([row])[0]

    assert opportunity.actionability_reason == "gated_off"
    assert default_queue_exclusion(opportunity) == "gated_off"
