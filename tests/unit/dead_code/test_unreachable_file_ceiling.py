"""Git age cannot lift an unreachable file past what the graph could have seen.

From the 25 Nov hosted audit: an Elixir controller the router names
(``get "/health", HealthController, :check``), a PHP file reached by a computed
``require_once``, a C++ header five files include through an unresolved
``<rw_mingw.hpp>``, and a Rails service class untouched for years all came out
at 0.9 to 1.0 on git age alone. Each fixture is parsed by the real pipeline and
every file is given the same stale history, so only the ceiling separates them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.passes.unreachable_files import (
    UNPROVEN_FILE_CONFIDENCE,
)
from repowise.core.pipeline.incremental import build_repo_graph
from tests.unit.dead_code._helpers import _old_date


def _confidences(root: Path, files: dict[str, str]) -> dict[str, float]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _parsed, source_map, builder, _s, _c = build_repo_graph(
        root, [], collect_sources=True, read_only=True
    )
    graph = builder.graph()
    stale = {"commit_count_90d": 0, "last_commit_at": _old_date(900), "age_days": 1200}
    analyzer = DeadCodeAnalyzer(
        graph,
        {node: dict(stale) for node, data in graph.nodes(data=True) if data.get("node_type") != "symbol"},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
    )
    report = analyzer.analyze({"min_confidence": 0.0, "detect_zombie_packages": False})
    return {
        f.file_path: f.confidence
        for f in report.findings
        if f.kind is DeadCodeKind.UNREACHABLE_FILE
    }


def test_elixir_files_never_pass_the_weak_resolver_ceiling(tmp_path):
    found = _confidences(
        tmp_path,
        {
            "mix.exs": "defmodule App.MixProject do\n  use Mix.Project\nend\n",
            "lib/app_web/router.ex": (
                "defmodule AppWeb.Router do\n  use Phoenix.Router\n"
                '  scope "/", AppWeb do\n    get "/health", HealthController, :check\n  end\nend\n'
            ),
            "lib/app_web/controllers/health_controller.ex": (
                "defmodule AppWeb.HealthController do\n"
                "  def check(conn, _params), do: put_view(conn, html: AppWeb.ErrorHTML)\nend\n"
            ),
            "lib/app_web/controllers/error_html.ex": (
                "defmodule AppWeb.ErrorHTML do\n  def render(t, _a), do: t\nend\n"
            ),
            "lib/app/orphan_report.ex": (
                "defmodule App.OrphanReport do\n  def build(rows), do: Enum.count(rows)\nend\n"
            ),
        },
    )
    for path in (
        "lib/app_web/controllers/health_controller.ex",
        "lib/app_web/controllers/error_html.ex",
        "lib/app/orphan_report.ex",
    ):
        assert found[path] == pytest.approx(UNPROVEN_FILE_CONFIDENCE), path


def test_php_computed_includes_never_pass_the_ceiling(tmp_path):
    files = {
        "main.php": (
            "<?php\n$srcdir = __DIR__ . '/lib';\n"
            "require_once(\"$srcdir/interp.php\");\n"
            "require_once($GLOBALS['srcdir'] . '/glob.php');\n"
            "interp_fn(); glob_fn();\n"
        ),
        "lib/interp.php": "<?php\nfunction interp_fn() { return 1; }\n",
        "lib/glob.php": "<?php\nfunction glob_fn() { return 1; }\n",
        "lib/orphan_helper.php": "<?php\nfunction never_called_fn() { return 2; }\n",
    }
    found = _confidences(tmp_path, files)
    for path in ("lib/interp.php", "lib/glob.php", "lib/orphan_helper.php"):
        assert found[path] <= UNPROVEN_FILE_CONFIDENCE, path


def test_cpp_header_production_code_names_is_capped_and_a_silent_one_is_not(tmp_path):
    found = _confidences(
        tmp_path,
        {
            "src/main.cpp": "#include <rw_mingw.hpp>\nint main() { return 0; }\n",
            "rwengine/compat/rw_mingw.hpp": (
                "#pragma once\n#ifdef __MINGW32__\n#define RW_MINGW_COMPAT 1\n#endif\n"
            ),
            "rwengine/legacy/orphan_math.hpp": "#pragma once\n#define ORPHAN_MATH_SCALE 3\n",
            # A test or a doc naming a header is not production code using it.
            "tests/orphan_math_test.cpp": "// orphan_math is checked by hand\nint t() { return 0; }\n",
            "docs/notes.md": "orphan_math is kept for the old build.\n",
            # Nor is another unreachable file naming it.
            "rwengine/stats/orphan_stats.cpp": "// scaled by orphan_math\nint s() { return 1; }\n",
        },
    )
    assert found["rwengine/compat/rw_mingw.hpp"] == pytest.approx(UNPROVEN_FILE_CONFIDENCE)
    assert found["rwengine/legacy/orphan_math.hpp"] == pytest.approx(1.0)


def test_rails_file_untouched_for_years_stays_below_high(tmp_path):
    found = _confidences(
        tmp_path,
        {
            "Gemfile": "source 'https://rubygems.org'\ngem 'rails'\n",
            "app/controllers/application_controller.rb": (
                "class ApplicationController < ActionController::Base\nend\n"
            ),
            "app/controllers/campaigns_controller.rb": (
                "class CampaignsController < ApplicationController\n"
                "  def create\n    CampaignConversationBuilder.new(params).perform\n  end\nend\n"
            ),
            "app/services/campaign_conversation_builder.rb": (
                "class CampaignConversationBuilder\n  def perform; end\nend\n"
            ),
            "app/models/legacy_ledger.rb": "class LegacyLedger < ApplicationRecord\nend\n",
        },
    )
    assert found, "the fixture produced no unreachable files"
    assert all(conf <= UNPROVEN_FILE_CONFIDENCE for conf in found.values()), found
