"""MCP tool for live commit and range change-risk scoring.

``tool.py`` holds the registered tool; the sibling modules build its blocks.
Every name the single module had is re-exported here, and a block calls a
name tests patch through the package (``_pkg.<name>``), never by bare name,
or the patch stops reaching it.
"""

# ruff: noqa: F401

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from functools import partial
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from repowise.core.analysis.change_health.service import (
    ChangeHealthDeltaService,
    DeltaRequest,
)
from repowise.core.analysis.change_risk import (
    FixHistoryUnavailableError,
    change_risk_payload,
    fixes_reachable_from,
    normalize_extensions,
    score_live_change,
)
from repowise.core.analysis.change_risk.features import revspec_head
from repowise.core.analysis.change_risk.render import LIMITS, size_text
from repowise.core.analysis.git_cli import path_filter
from repowise.core.analysis.prior_fix_impact import (
    FixRecord,
    PriorFixFile,
    PriorFixImpact,
    dominant_file,
    parse_old_ranges,
    summarize_prior_fixes,
    unavailable_prior_fixes,
    unsupported_prior_fixes,
)
from repowise.core.fsutils import comparable_path
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._budget.contracts import response_budget_shed_order
from repowise.server.mcp_server._change_health import (
    directive as _directive,
)
from repowise.server.mcp_server._change_health import (
    finding_detail as _finding_detail,
)
from repowise.server.mcp_server._change_health import (
    health_delta_block as _health_delta_block,
)
from repowise.server.mcp_server._change_health import (
    patch_coverage_action as _patch_coverage_action,
)
from repowise.server.mcp_server._failure_shield import (
    is_stale_server_import,
    stale_server_notice,
)
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _is_workspace_mode,
    _resolve_repo_context,
    _unsupported_repo_all,
    attach_ignored_arguments,
    is_missing_table,
    resolve_enum_argument,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._test_impact import (
    cross_repo_tests,
    tests_block_for,
)
from repowise.server.mcp_server._test_selection import (
    UNAVAILABLE_REASON,
    SelectionUnavailableError,
    select_change_tests,
    selection_block,
)
from repowise.server.mcp_server.tool_change_risk.branches import (
    _BRANCH_OVERLAP_FILES_LIMIT,
    _BRANCH_OVERLAP_LIMIT,
    _BRANCH_OVERLAP_TIMEOUT_SECONDS,
    _UNGROUPED_FILES_LIMIT,
    _branch_overlap_block,
    _independent_changes_block,
    _scan_from_trunk,
    _scan_overlap,
)
from repowise.server.mcp_server.tool_change_risk.changed import (
    _change_base,
    _changed_in_scope,
    _changed_with_label,
    _filter_changed,
    _push_base,
    _push_change,
)
from repowise.server.mcp_server.tool_change_risk.cross_repo import (
    _CROSS_REPO_BREAKING_LIMIT,
    _CROSS_REPO_CONSUMER_LIMIT,
    _attach_consumer_tests,
    _breaking_row,
    _consumer_test_files,
    _cross_repo_block,
    _cross_repo_breaking,
    _cross_repo_consumers,
    _cross_repo_summary,
    _present,
    _workspace_enricher,
)
from repowise.server.mcp_server.tool_change_risk.health import (
    _DELTA_SERVICE_CAPACITY,
    _DELTA_SERVICES,
    _DELTA_SERVICES_LOCK,
    _attach_health_references,
    _compare_health,
    _delta_service,
    _evidence_block,
    _repository,
    _scored_repo,
    log,
)
from repowise.server.mcp_server.tool_change_risk.impacted import (
    _IMPACTED_TESTS_LIMIT,
    _empty_impacted,
    _impacted_tests_block,
    _serialize_missing,
)
from repowise.server.mcp_server.tool_change_risk.patch_coverage import (
    _attention_files,
    _enrich_patch,
    _patch_coverage_block,
)
from repowise.server.mcp_server.tool_change_risk.payload import (
    _DIAGNOSTIC_FIELDS,
    _DIFF_SIZE_LABELS,
    _HEALTH_DIAGNOSTIC_FIELDS,
    _INCLUDE_BLOCKS,
    _SHED_ORDER,
    _attach_health,
    _compact_patch_coverage,
    _diff_shape_sentence,
    _drill_down,
    _include_blocks,
    _path_patterns,
    _shape_payload,
)
from repowise.server.mcp_server.tool_change_risk.prior_fixes import (
    _PRIOR_FIXES_LIMIT,
    _events_before,
    _prior_fix_row,
    _prior_fixes_block,
    _read_failure,
    _read_prior_fixes,
)
from repowise.server.mcp_server.tool_change_risk.tool import (
    _attach_branch_blocks,
    _attach_change_blocks,
    _nothing_to_score,
    _score_change,
    get_change_risk,
)
