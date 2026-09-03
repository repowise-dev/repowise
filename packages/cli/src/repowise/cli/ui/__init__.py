"""Branding, theme constants, and interactive UI helpers for the repowise CLI.

This package was split out of the former single ``ui.py`` module. The submodules
group the helpers by concern; this façade re-exports every previously-public name
so existing ``from repowise.cli.ui import ...`` call sites are unchanged.

The façade is lazy: each public name is resolved on first access via
:func:`__getattr__` rather than imported eagerly at package import time. That
matters for CLI startup — importing *any* submodule (e.g. ``ui.brand``, the one
cheap ``status``/``coverage`` commands need) used to run this package's
``__init__``, which eagerly pulled in the heavy interactive modules
(``mode_selection`` -> ``repo_scanner`` -> ``core.ingestion`` -> networkx +
tree-sitter, ~1.1s on a cold process). Issue #1712. ``import repowise.cli.ui``
still resolves ``__all__`` cheaply because the name -> submodule mapping lives in
this module; only the bodies are imported when actually accessed.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

#: Public name -> submodule that owns it, mirroring the eager re-exports below.
#: ``import *`` and ``hasattr`` stay cheap because only this dict is built.
_LAZY: dict[str, str] = {
    "BRAND": "brand",
    "BRAND_STYLE": "brand",
    "DIM": "brand",
    "ERR": "brand",
    "LARGE_REPO_FILE_THRESHOLD": "mode_selection",
    "OK": "brand",
    "OWL_SPINNER": "mascot",
    "THINKING_FRAMES": "mascot",
    "VALUE": "brand",
    "WARN": "brand",
    "MaybeCountColumn": "progress",
    "ProviderSelection": "provider_selection",
    "RepoScanInfo": "repo_scanner",
    "RichProgressCallback": "progress",
    "banner_text": "mascot",
    "build_completion_panel": "result_panels",
    "build_contextual_next_steps": "result_panels",
    "build_status_notes": "result_panels",
    "format_bytes": "brand",
    "format_elapsed": "brand",
    "interactive_advanced_config": "mode_selection",
    "interactive_customize_offer": "mode_selection",
    "interactive_fast_mode_offer": "mode_selection",
    "interactive_generate_docs_toggle": "mode_selection",
    "interactive_mode_select": "mode_selection",
    "interactive_primary_select": "workspace_selection",
    "interactive_provider_config_select": "provider_selection",
    "interactive_provider_select": "provider_selection",
    "interactive_repo_select": "workspace_selection",
    "key_value_table": "brand",
    "load_dotenv": "env_persistence",
    "mini": "mascot",
    "print_analysis_summary": "result_panels",
    "print_banner": "brand",
    "print_files_written": "result_panels",
    "print_index_only_intro": "mode_selection",
    "print_phase_header": "brand",
    "print_scan_summary": "repo_scanner",
    "print_section": "brand",
    "prompt_file_page_volume": "mode_selection",
    "prompt_wiki_style": "mode_selection",
    "quick_repo_scan": "repo_scanner",
    "should_offer_fast_mode": "mode_selection",
}

__all__ = sorted(_LAZY)

#: Cached fully-imported submodules, so repeated attribute access in a long-lived
#: process (the MCP/server path) does not re-import each time.
_loaded: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    submodule = _LAZY.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = _loaded.get(submodule)
    if module is None:
        module = import_module(f"{__name__}.{submodule}")
        _loaded[submodule] = module
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
