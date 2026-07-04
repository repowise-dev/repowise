"""Unit tests for dbt SQL import extraction + resolution (lightweight tier).

Covers every ref()/source() form the extractor claims, the per-project
model-name index (dbt_project.yml path config, seeds, snapshots), package
qualification, multi-project preference, and the false-positive guards
that keep plain SQL files import-free.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.lightweight_imports import extract_lightweight_imports
from repowise.core.ingestion.lightweight_imports.sql import extract_dbt_imports
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.sql import resolve_dbt_import


def _ctx(repo: Path | None, files: dict[str, str]) -> ResolverContext:
    """ResolverContext over *files* ({path: content}); writes them under *repo*."""
    if repo is not None:
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    stem_map: dict[str, list[str]] = {}
    for p in files:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=set(files),
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo,
    )


def _modules(imports) -> list[str]:
    return [imp.module_path for imp in imports]


def _file_info(rel: str, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=f"/tmp/{rel}",
        language=lang,  # type: ignore[arg-type]
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestDbtExtraction:
    def test_single_arg_ref(self) -> None:
        src = "select * from {{ ref('stg_orders') }}\n"
        assert _modules(extract_dbt_imports(src)) == ["stg_orders"]

    def test_double_quotes_and_no_spaces(self) -> None:
        src = 'select * from {{ref("stg_orders")}}'
        assert _modules(extract_dbt_imports(src)) == ["stg_orders"]

    def test_two_arg_ref_is_package_qualified(self) -> None:
        src = "select * from {{ ref('elementary', 'alerts') }}"
        assert _modules(extract_dbt_imports(src)) == ["elementary.alerts"]

    def test_ref_with_version_kwarg(self) -> None:
        src = "select * from {{ ref('dim_customers', version=2) }}"
        assert _modules(extract_dbt_imports(src)) == ["dim_customers"]

    def test_ref_piped_through_a_filter(self) -> None:
        src = "{{ ref('stg_payments') | string }}"
        assert _modules(extract_dbt_imports(src)) == ["stg_payments"]

    def test_ref_inside_jinja_statement_block(self) -> None:
        src = "{% set payments = ref('stg_payments') %}\nselect * from {{ payments }}"
        assert _modules(extract_dbt_imports(src)) == ["stg_payments"]

    def test_source(self) -> None:
        src = "select * from {{ source('jaffle_shop', 'raw_orders') }}"
        assert _modules(extract_dbt_imports(src)) == ["source:jaffle_shop.raw_orders"]

    def test_whitespace_and_trim_markers(self) -> None:
        src = "select * from {{- ref( 'stg_orders' ) -}}"
        assert _modules(extract_dbt_imports(src)) == ["stg_orders"]

    def test_duplicates_collapse(self) -> None:
        src = "select * from {{ ref('a') }} join {{ ref('a') }} using (id)"
        assert _modules(extract_dbt_imports(src)) == ["a"]

    def test_plain_sql_has_no_imports(self) -> None:
        src = "CREATE TABLE users (id INT);\nSELECT * FROM orders;\n"
        assert extract_dbt_imports(src) == []

    def test_bare_ref_call_outside_jinja_is_not_a_dependency(self) -> None:
        # ref() as a plain SQL function (a UDF) must not create edges.
        src = "SELECT ref('foo') FROM t;"
        assert extract_dbt_imports(src) == []

    def test_dispatch_via_language_tag(self) -> None:
        info = _file_info("models/orders.sql", "sql")
        source = b"select * from {{ ref('stg_orders') }}"
        assert _modules(extract_lightweight_imports(info, source)) == ["stg_orders"]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

_PROJECT_YML = "name: jaffle_shop\nprofile: default\n"


class TestDbtResolution:
    def test_ref_resolves_to_model_file(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "dbt_project.yml": _PROJECT_YML,
                "models/stg_orders.sql": "select 1",
                "models/orders.sql": "select * from {{ ref('stg_orders') }}",
            },
        )
        assert resolve_dbt_import("stg_orders", "models/orders.sql", ctx) == "models/stg_orders.sql"

    def test_nested_model_dirs(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "dbt_project.yml": _PROJECT_YML,
                "models/staging/stg_orders.sql": "select 1",
                "models/marts/orders.sql": "",
            },
        )
        assert (
            resolve_dbt_import("stg_orders", "models/marts/orders.sql", ctx)
            == "models/staging/stg_orders.sql"
        )

    def test_custom_model_paths_from_project_yml(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "dbt_project.yml": "name: p\nmodel-paths: ['transformations']\n",
                "transformations/stg_orders.sql": "select 1",
                "models/ignored.sql": "select 1",
            },
        )
        assert (
            resolve_dbt_import("stg_orders", "transformations/orders.sql", ctx)
            == "transformations/stg_orders.sql"
        )
        # models/ is not a configured model path for this project
        assert resolve_dbt_import("ignored", "transformations/orders.sql", ctx) == (
            "external:dbt:ignored"
        )

    def test_seed_csv_is_ref_able(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "dbt_project.yml": _PROJECT_YML,
                "seeds/raw_customers.csv": "id,name\n",
                "models/customers.sql": "",
            },
        )
        assert (
            resolve_dbt_import("raw_customers", "models/customers.sql", ctx)
            == "seeds/raw_customers.csv"
        )

    def test_snapshots_are_ref_able(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "dbt_project.yml": _PROJECT_YML,
                "snapshots/orders_snapshot.sql": "select 1",
                "models/orders.sql": "",
            },
        )
        assert (
            resolve_dbt_import("orders_snapshot", "models/orders.sql", ctx)
            == "snapshots/orders_snapshot.sql"
        )

    def test_source_is_a_typed_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"dbt_project.yml": _PROJECT_YML})
        assert (
            resolve_dbt_import("source:jaffle_shop.raw_orders", "models/x.sql", ctx)
            == "external:source:jaffle_shop.raw_orders"
        )

    def test_unknown_ref_is_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"dbt_project.yml": _PROJECT_YML, "models/a.sql": ""})
        assert resolve_dbt_import("nope", "models/a.sql", ctx) == "external:dbt:nope"

    def test_two_arg_ref_prefers_named_project(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "core/dbt_project.yml": "name: core_pkg\n",
                "core/models/shared.sql": "select 1",
                "analytics/dbt_project.yml": "name: analytics\n",
                "analytics/models/shared.sql": "select 2",
                "analytics/models/report.sql": "",
            },
        )
        assert (
            resolve_dbt_import("core_pkg.shared", "analytics/models/report.sql", ctx)
            == "core/models/shared.sql"
        )

    def test_two_arg_ref_to_uninstalled_package_is_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"dbt_project.yml": _PROJECT_YML, "models/a.sql": ""})
        assert (
            resolve_dbt_import("dbt_utils.surrogate_key", "models/a.sql", ctx)
            == "external:dbt:dbt_utils.surrogate_key"
        )

    def test_same_name_prefers_importer_project(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "core/dbt_project.yml": "name: core_pkg\n",
                "core/models/shared.sql": "select 1",
                "analytics/dbt_project.yml": "name: analytics\n",
                "analytics/models/shared.sql": "select 2",
                "analytics/models/report.sql": "",
            },
        )
        assert (
            resolve_dbt_import("shared", "analytics/models/report.sql", ctx)
            == "analytics/models/shared.sql"
        )

    def test_dbt_packages_are_never_indexed(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "dbt_project.yml": _PROJECT_YML,
                "dbt_packages/elementary/dbt_project.yml": "name: elementary\n",
                "dbt_packages/elementary/models/alerts.sql": "select 1",
                "models/orders.sql": "",
            },
        )
        assert resolve_dbt_import("alerts", "models/orders.sql", ctx) == "external:dbt:alerts"

    def test_self_reference_returns_none(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {"dbt_project.yml": _PROJECT_YML, "models/orders.sql": "select 1"},
        )
        assert resolve_dbt_import("orders", "models/orders.sql", ctx) is None

    def test_no_dbt_project_means_external(self, tmp_path: Path) -> None:
        # Gate: without dbt_project.yml no model index exists at all.
        ctx = _ctx(tmp_path, {"models/stg_orders.sql": "select 1"})
        assert (
            resolve_dbt_import("stg_orders", "models/orders.sql", ctx) == "external:dbt:stg_orders"
        )

    def test_dispatch_through_resolve_import(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "dbt_project.yml": _PROJECT_YML,
                "models/stg_orders.sql": "select 1",
            },
        )
        assert (
            resolve_import("stg_orders", "models/orders.sql", "sql", ctx) == "models/stg_orders.sql"
        )
