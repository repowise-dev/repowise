"""npm/yarn/pnpm workspace package resolution for TypeScript imports.

Maps each workspace package name to its directory so ``import x from
"@myorg/foo"`` binds the intra-repo file instead of an ``external:`` node.
Subpath imports honour the package's Node ``exports`` map, falling back to a
``<pkg>/<subpath>`` probe for plain monorepo layouts.

Every submodule name is re-exported here. Patch a name where it is looked up:
a patch on this package does not reach lookups inside the submodules.
"""

# ruff: noqa: F401

from __future__ import annotations

import contextlib
import json
import posixpath
import re
import subprocess
from collections.abc import Container
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..pnpm_workspace import read_pnpm_workspace_patterns
from .builds import (
    _BUILD_OUTPUT_JS,
    _BUILD_SOURCE_EXTENSIONS,
    _built_output,
    _built_source,
    _CheckedInBuilds,
    _git_checked_in,
    _manifest_targets,
    _named_build_outputs,
    checked_in_builds,
    get_checked_in_builds,
    manifest_entry_paths,
    probe_manifest_target,
)
from .exports import (
    _CONDITION_PRIORITY,
    _DECLARATION_SUFFIXES,
    _build_exports_map,
    _flatten_export_value,
    _match_export_key,
    _ordered_export_targets,
)
from .index import (
    TsWorkspaceIndex,
    _expand_exports_wildcard,
    build_ts_workspace_index,
    get_or_build_ts_index,
)
from .members import (
    _expand_member_dirs,
    _read_workspace_declaration,
    _WorkspaceDeclaration,
    build_workspace_info,
    build_workspace_map,
    get_or_build_workspace_info,
    read_workspaces_field,
)
from .paths import (
    _PROBE_EXTENSIONS,
    _normalize_repo_rel,
    _probe_path,
)
from .resolve import (
    _read_sub_package_manifest,
    _resolve_nested_subpackage,
    _WorkspaceProbe,
    resolve_via_workspaces,
)
from .scan import (
    _SCAN_PRUNE_DIRS,
    _VITEST_CONFIG_NAMES,
    _get_repo_scan,
    _parsed_package_jsons,
    _RepoFileScan,
    _scan_repo_files,
)
from .tool_configs import (
    _EXPERIMENT_DIR_NAMES,
    _GRAFANA_ENTRY_EXTS,
    _MDX_IMPORT_RE,
    _NPM_SCRIPT_RUNNERS,
    _NPM_SCRIPT_SOURCE_EXTS,
    _VITEST_INCLUDE_RE,
    _VITEST_STRING_RE,
    _iter_script_tokens,
    _vitest_glob_to_regex,
    find_grafana_plugin_targets,
    find_mdx_import_targets,
    find_npm_script_entry_targets,
    find_vitest_include_targets,
)
