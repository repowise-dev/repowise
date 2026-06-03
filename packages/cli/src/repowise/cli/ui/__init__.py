"""Branding, theme constants, and interactive UI helpers for the repowise CLI.

This package was split out of the former single ``ui.py`` module. The submodules
group the helpers by concern; this façade re-exports every previously-public name
so existing ``from repowise.cli.ui import ...`` call sites are unchanged.
"""

from __future__ import annotations

from repowise.cli.ui.brand import (
    BRAND,
    BRAND_STYLE,
    DIM,
    ERR,
    OK,
    WARN,
    format_elapsed,
    print_banner,
    print_phase_header,
)
from repowise.cli.ui.env_persistence import load_dotenv
from repowise.cli.ui.mode_selection import (
    LARGE_REPO_FILE_THRESHOLD,
    interactive_advanced_config,
    interactive_fast_mode_offer,
    interactive_mode_select,
    print_index_only_intro,
    should_offer_fast_mode,
)
from repowise.cli.ui.progress import MaybeCountColumn, RichProgressCallback
from repowise.cli.ui.provider_selection import (
    ProviderSelection,
    interactive_provider_config_select,
    interactive_provider_select,
)
from repowise.cli.ui.repo_scanner import (
    RepoScanInfo,
    print_scan_summary,
    quick_repo_scan,
)
from repowise.cli.ui.result_panels import (
    build_analysis_summary_panel,
    build_completion_panel,
    build_contextual_next_steps,
)
from repowise.cli.ui.workspace_selection import (
    interactive_primary_select,
    interactive_repo_select,
)

__all__ = [
    "BRAND",
    "BRAND_STYLE",
    "DIM",
    "ERR",
    "LARGE_REPO_FILE_THRESHOLD",
    "OK",
    "WARN",
    "MaybeCountColumn",
    "ProviderSelection",
    "RepoScanInfo",
    "RichProgressCallback",
    "build_analysis_summary_panel",
    "build_completion_panel",
    "build_contextual_next_steps",
    "format_elapsed",
    "interactive_advanced_config",
    "interactive_fast_mode_offer",
    "interactive_mode_select",
    "interactive_primary_select",
    "interactive_provider_config_select",
    "interactive_provider_select",
    "interactive_repo_select",
    "load_dotenv",
    "print_banner",
    "print_index_only_intro",
    "print_phase_header",
    "print_scan_summary",
    "quick_repo_scan",
    "should_offer_fast_mode",
]
