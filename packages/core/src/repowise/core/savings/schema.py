"""Transactional schema upgrade for the omissions/savings sidecar."""

from __future__ import annotations

import sqlite3

#: Version of the *store*, recorded in ``PRAGMA user_version``. Distinct from
#: the per-event ``schema_version`` column, which versions an event payload and
#: is still 1: v2 changed only how the store validates an agent id.
SAVINGS_SCHEMA_VERSION = 2

_PRESERVED_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS omissions (
        ref TEXT PRIMARY KEY,
        content BLOB NOT NULL,
        source TEXT NOT NULL,
        created_at REAL NOT NULL,
        original_tokens INTEGER NOT NULL,
        kept_tokens INTEGER NOT NULL,
        access_count INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_references (
        ref TEXT PRIMARY KEY,
        content BLOB NOT NULL,
        repository TEXT NOT NULL,
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_omissions_created ON omissions(created_at)",
)

_LEGACY_SAVINGS_TABLE = """
CREATE TABLE savings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    filter TEXT NOT NULL,
    source TEXT NOT NULL,
    command TEXT,
    raw_tokens INTEGER NOT NULL,
    distilled_tokens INTEGER NOT NULL
)
"""

_SCHEMA_TABLES = (
    """
    CREATE TABLE savings_events (
        event_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        idempotency_key TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        repository_id TEXT NOT NULL,
        surface TEXT NOT NULL,
        integration TEXT NOT NULL,
        agent TEXT NOT NULL,
        subagent_id TEXT,
        session_id TEXT,
        request_id TEXT,
        tool_call_id TEXT,
        operation TEXT NOT NULL,
        evidence_kind TEXT NOT NULL,
        confidence REAL,
        estimator TEXT NOT NULL,
        token_unit TEXT NOT NULL,
        result_state TEXT NOT NULL,
        is_usable INTEGER NOT NULL,
        baseline_input_tokens INTEGER,
        pre_budget_input_tokens INTEGER,
        delivered_input_tokens INTEGER,
        dropped_input_tokens INTEGER,
        saved_input_tokens INTEGER NOT NULL,
        baseline_output_tokens INTEGER,
        delivered_output_tokens INTEGER,
        saved_output_tokens INTEGER,
        model TEXT,
        currency TEXT,
        pricing_source TEXT,
        pricing_version TEXT,
        input_rate_usd_per_million REAL,
        output_rate_usd_per_million REAL,
        metadata_json TEXT NOT NULL,
        UNIQUE(repository_id, idempotency_key),
        CHECK(schema_version = 1),
        CHECK(surface IN ('distill','hook','mcp','vscode_lm')),
        -- Agent ids are checked syntactically, never against a list. A list
        -- here would be a copy of the agent registry that only the database
        -- can see, and it would reject a newly added agent's events outright.
        CHECK(length(integration) BETWEEN 1 AND 32
            AND integration NOT GLOB '*[^a-z0-9_]*'),
        CHECK(length(agent) BETWEEN 1 AND 32 AND agent NOT GLOB '*[^a-z0-9_]*'),
        CHECK(evidence_kind IN ('measured','inferred')),
        CHECK(result_state IN ('success','dead_end','error','partial','unknown')),
        CHECK(is_usable IN (0,1)),
        CHECK(result_state != 'success' OR is_usable = 1),
        CHECK(result_state NOT IN ('dead_end','error','unknown') OR is_usable = 0),
        CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
        CHECK(baseline_input_tokens IS NULL OR baseline_input_tokens >= 0),
        CHECK(pre_budget_input_tokens IS NULL OR pre_budget_input_tokens >= 0),
        CHECK(delivered_input_tokens IS NULL OR delivered_input_tokens >= 0),
        CHECK(dropped_input_tokens IS NULL OR dropped_input_tokens >= 0),
        CHECK(saved_input_tokens >= 0),
        CHECK(baseline_output_tokens IS NULL OR baseline_output_tokens >= 0),
        CHECK(delivered_output_tokens IS NULL OR delivered_output_tokens >= 0),
        CHECK(saved_output_tokens IS NULL OR saved_output_tokens >= 0),
        CHECK(input_rate_usd_per_million IS NULL OR input_rate_usd_per_million >= 0),
        CHECK(output_rate_usd_per_million IS NULL OR output_rate_usd_per_million >= 0)
    )
    """,
    """
    CREATE TABLE savings_event_omissions (
        event_id TEXT NOT NULL REFERENCES savings_events(event_id) ON DELETE CASCADE,
        omission_ref TEXT NOT NULL,
        PRIMARY KEY(event_id, omission_ref)
    )
    """,
    """
    CREATE TABLE savings_event_correlations (
        event_id TEXT NOT NULL REFERENCES savings_events(event_id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        value_hash TEXT NOT NULL,
        PRIMARY KEY(event_id, kind, value_hash),
        CHECK(length(value_hash) = 71)
    )
    """,
    """
    CREATE TABLE savings_opportunities (
        observation_id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        repository_id TEXT NOT NULL,
        integration TEXT NOT NULL,
        kind TEXT NOT NULL,
        estimated_potential_input_tokens INTEGER NOT NULL,
        CHECK(length(integration) BETWEEN 1 AND 32
            AND integration NOT GLOB '*[^a-z0-9_]*'),
        CHECK(estimated_potential_input_tokens >= 0)
    )
    """,
)

_SCHEMA_INDEXES = (
    "CREATE INDEX idx_savings_events_repo_time ON savings_events(repository_id, occurred_at)",
    "CREATE INDEX idx_savings_events_surface_time ON savings_events(surface, occurred_at)",
    "CREATE INDEX idx_savings_events_operation_time ON savings_events(operation, occurred_at)",
    "CREATE INDEX idx_savings_events_agent_time ON savings_events(agent, occurred_at)",
    "CREATE INDEX idx_savings_event_omissions_ref ON savings_event_omissions(omission_ref)",
    "CREATE INDEX idx_savings_correlations_hash ON savings_event_correlations(kind, value_hash)",
    "CREATE INDEX idx_savings_opportunities_repo_time ON savings_opportunities(repository_id, occurred_at)",
    "CREATE INDEX idx_savings_created ON savings(created_at)",
)

_REQUIRED_TABLES = frozenset(
    {
        "omissions",
        "evidence_references",
        "savings",
        "savings_events",
        "savings_event_omissions",
        "savings_event_correlations",
        "savings_opportunities",
    }
)


def _has_current_schema(conn: sqlite3.Connection) -> bool:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return tables >= _REQUIRED_TABLES


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Reset obsolete telemetry and install v1; caller owns the transaction."""
    for statement in _PRESERVED_TABLES:
        conn.execute(statement)
    for table in (
        "savings_event_omissions",
        "savings_event_correlations",
        "savings_opportunities",
        "savings_events",
        "savings",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(_LEGACY_SAVINGS_TABLE)
    for statement in _SCHEMA_TABLES:
        conn.execute(statement)
    for statement in _SCHEMA_INDEXES:
        conn.execute(statement)


def initialize_savings_schema(conn: sqlite3.Connection) -> None:
    """Upgrade once under a write lock, recording version only after success."""
    conn.execute("PRAGMA foreign_keys=ON")
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > SAVINGS_SCHEMA_VERSION:
        raise RuntimeError(
            f"savings sidecar schema {version} is newer than supported "
            f"version {SAVINGS_SCHEMA_VERSION}"
        )
    if version == SAVINGS_SCHEMA_VERSION and _has_current_schema(conn):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SAVINGS_SCHEMA_VERSION:
            raise RuntimeError(
                f"savings sidecar schema {version} is newer than supported "
                f"version {SAVINGS_SCHEMA_VERSION}"
            )
        if version < SAVINGS_SCHEMA_VERSION or not _has_current_schema(conn):
            _apply_schema(conn)
            conn.execute(f"PRAGMA user_version={SAVINGS_SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
