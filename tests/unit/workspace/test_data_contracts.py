"""Data (app-to-database) contract extraction tests.

Table-name normalization is tested first and hardest: it is where the false
links would come from (quoting, schema prefixes, case). Then per-dialect
provider/consumer extraction, then the cross-repo end-to-end match.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import normalize_contract_id
from repowise.core.workspace.extractors.data import DataExtractor
from repowise.core.workspace.extractors.data.names import (
    is_system_table,
    normalize_table_name,
    split_qualified,
)
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.system_graph import edge_kind_for_contract_type


class TestNormalizeTableName:
    def test_bare_lowercase_passthrough(self) -> None:
        assert normalize_table_name("users") == "users"

    def test_case_folds(self) -> None:
        assert normalize_table_name("Users") == "users"

    def test_double_quotes_stripped(self) -> None:
        assert normalize_table_name('"Users"') == "users"

    def test_backticks_stripped(self) -> None:
        assert normalize_table_name("`users`") == "users"

    def test_brackets_stripped(self) -> None:
        assert normalize_table_name("[Users]") == "users"

    def test_default_schema_dropped(self) -> None:
        assert normalize_table_name("public.users") == "users"
        assert normalize_table_name("dbo.Users") == "users"

    def test_named_schema_kept(self) -> None:
        assert normalize_table_name("analytics.events") == "analytics.events"

    def test_quoted_qualified(self) -> None:
        assert normalize_table_name('"public"."Users"') == "users"
        assert normalize_table_name("[dbo].[Orders]") == "orders"

    def test_three_part_name_keeps_last_two(self) -> None:
        assert normalize_table_name("mydb.analytics.events") == "analytics.events"

    def test_temp_table_rejected(self) -> None:
        assert normalize_table_name("#tmp_orders") is None

    def test_interpolation_rejected(self) -> None:
        assert normalize_table_name("{table}") is None
        assert normalize_table_name("${TABLE}") is None
        assert normalize_table_name("%s") is None

    def test_empty_rejected(self) -> None:
        assert normalize_table_name("") is None

    def test_split_qualified(self) -> None:
        assert split_qualified("a.b") == ("a", "b")
        assert split_qualified("b") == (None, "b")

    def test_is_system_table(self) -> None:
        for sys_tbl in (
            "pg_database",
            "pg_roles",
            "pg_class",
            "pg_catalog.pg_tables",
            "pg_catalog.custom_view",
            "information_schema.tables",
            "information_schema.columns",
            "alembic_version",
            "core.alembic_version",
        ):
            assert is_system_table(sys_tbl), sys_tbl
        for user_tbl in ("users", "page_views", "pgadmin", "analytics.events"):
            assert not is_system_table(user_tbl), user_tbl


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestDdlProviders:
    def test_create_table_and_view(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "schema.sql",
            """
            CREATE TABLE users (id INT PRIMARY KEY, email TEXT);
            CREATE VIEW active_users AS SELECT * FROM users WHERE active;
            CREATE INDEX idx_users_email ON users(email);
            """,
        )
        contracts = DataExtractor().extract(tmp_path, "db")
        providers = {c.contract_id for c in contracts if c.role == "provider"}
        assert "data::users" in providers
        assert "data::active_users" in providers
        # INDEX attaches to a table; it declares none.
        assert not any("idx_users_email" in c for c in providers)

    def test_schema_qualified_and_quoted(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "migrations/001_init.sql",
            'CREATE TABLE "public"."Orders" (id INT);\nCREATE TABLE analytics.events (id INT);\n',
        )
        contracts = DataExtractor().extract(tmp_path, "db")
        ids = {c.contract_id for c in contracts}
        assert "data::orders" in ids
        assert "data::analytics.events" in ids

    def test_alter_table_is_provider(self, tmp_path: Path) -> None:
        _write(tmp_path, "migrations/002_add.sql", "ALTER TABLE users ADD COLUMN age INT;")
        contracts = DataExtractor().extract(tmp_path, "db")
        assert any(c.contract_id == "data::users" and c.role == "provider" for c in contracts)

    def test_malformed_sql_yields_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, "broken.sql", "CREATE TABLE ((((( ;;; garbage !!")
        contracts = DataExtractor().extract(tmp_path, "db")
        assert contracts == []


class TestOrmProviders:
    def test_sqlalchemy_tablename(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/models.py",
            'class User(Base):\n    __tablename__ = "users"\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        assert any(c.contract_id == "data::users" and c.role == "provider" for c in contracts)

    def test_alembic_create_table(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "alembic/versions/abc_init.py",
            'def upgrade():\n    op.create_table("orders", sa.Column("id", sa.Integer))\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        (orders,) = [c for c in contracts if c.contract_id == "data::orders"]
        # A migration defines the schema, which marks the owning repo.
        assert orders.meta["schema"] == "create"

    def test_alembic_create_table_records_the_schema_keyword(self, tmp_path: Path) -> None:
        # `schema=` trails the column list, so the name has to come off the
        # call's argument list rather than the string right after the paren.
        _write(
            tmp_path,
            "alembic/versions/abc_init.py",
            "def upgrade():\n"
            "    op.create_table(\n"
            '        "owners",\n'
            '        sa.Column("id", sa.Integer, primary_key=True),\n'
            '        schema="core",\n'
            "    )\n",
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        ids = {c.contract_id for c in contracts}
        assert "data::core.owners" in ids
        # The bare key must go: it is what let the two sides drift apart.
        assert "data::owners" not in ids

    def test_alembic_schema_keyword_matches_the_qualified_sql_consumer(
        self, tmp_path: Path
    ) -> None:
        # The end-to-end shape from the issue: a schema-qualified migration and
        # a schema-qualified consumer previously never met (2 of 48 consumers).
        backend = tmp_path / "backend"
        worker = tmp_path / "worker"
        _write(
            backend,
            "alembic/versions/abc_init.py",
            'def upgrade():\n    op.create_table("owners", sa.Column("id"), schema="core")\n',
        )
        _write(worker, "jobs/process.py", 'q = "SELECT id FROM core.owners WHERE done = 0"\n')

        contracts = DataExtractor().extract(backend, "backend")
        contracts += DataExtractor().extract(worker, "worker")
        links = match_contracts(contracts)

        assert len(links) == 1
        assert links[0].provider_repo == "backend"
        assert links[0].consumer_repo == "worker"

    def test_alembic_default_schema_keyword_still_normalizes_away(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "alembic/versions/abc_init.py",
            'op.create_table("owners", sa.Column("id"), schema="public")\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        assert "data::owners" in {c.contract_id for c in contracts}

    def test_alembic_schema_keyword_does_not_leak_to_the_next_call(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "alembic/versions/abc_init.py",
            "def upgrade():\n"
            '    op.create_table("owners", sa.Column("id"), schema="core")\n'
            '    op.create_table("visits", sa.Column("id"))\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        ids = {c.contract_id for c in contracts}
        assert {"data::core.owners", "data::visits"} <= ids

    def test_alembic_schema_in_another_statement_does_not_leak(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "alembic/versions/abc_init.py",
            'op.create_table("owners", sa.Column("id"))\n'
            'op.execute(sa.text(\'ALTER TABLE x SET SCHEMA "core"\'))\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        assert "data::owners" in {c.contract_id for c in contracts}

    def test_sqlmodel_table_true(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/models.py",
            "class HeroTeam(SQLModel, table=True):\n    id: int\n",
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        # SQLModel default table name is the class name lower-cased.
        assert any(c.contract_id == "data::heroteam" for c in contracts)

    def test_django_db_table(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/models.py",
            "class Order(models.Model):\n    class Meta:\n        db_table = 'shop_orders'\n",
        )
        contracts = DataExtractor().extract(tmp_path, "shop")
        assert any(c.contract_id == "data::shop_orders" for c in contracts)

    def test_jpa_table_annotation(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/main/java/User.java",
            '@Entity\n@Table(name = "app_users")\npublic class User {}\n',
        )
        contracts = DataExtractor().extract(tmp_path, "svc")
        assert any(c.contract_id == "data::app_users" for c in contracts)

    def test_jpa_entity_class_convention(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/main/java/OrderItem.java",
            "@Entity\npublic class OrderItem {}\n",
        )
        contracts = DataExtractor().extract(tmp_path, "svc")
        match = [c for c in contracts if c.contract_id == "data::order_item"]
        assert match and match[0].confidence < 0.85

    def test_efcore_dbset_and_table_attr(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "Data/AppDbContext.cs",
            "public class AppDbContext : DbContext {\n"
            "    public DbSet<User> Users { get; set; }\n"
            "}\n",
        )
        _write(tmp_path, "Models/Legacy.cs", '[Table("legacy_orders")]\npublic class Legacy {}\n')
        contracts = DataExtractor().extract(tmp_path, "svc")
        ids = {c.contract_id for c in contracts}
        assert "data::users" in ids
        assert "data::legacy_orders" in ids

    def test_activerecord_convention_and_override(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/models/blog_post.rb", "class BlogPost < ApplicationRecord\nend\n")
        _write(
            tmp_path,
            "app/models/legacy.rb",
            "class Legacy < ApplicationRecord\n  self.table_name = 'old_stuff'\nend\n",
        )
        contracts = DataExtractor().extract(tmp_path, "rails")
        ids = {c.contract_id for c in contracts}
        assert "data::blog_posts" in ids
        assert "data::old_stuff" in ids

    def test_eloquent_table_property(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/Models/Order.php",
            "<?php\nclass Order extends Model {\n    protected $table = 'orders';\n}\n",
        )
        contracts = DataExtractor().extract(tmp_path, "shop")
        assert any(c.contract_id == "data::orders" for c in contracts)


class TestSqlStringConsumers:
    def test_parseable_select(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/queries.py",
            'q = "SELECT id, email FROM users WHERE active = 1"\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert [c.contract_id for c in consumers] == ["data::users"]
        assert consumers[0].meta["verb"] == "select"

    def test_quoted_table_inside_single_quoted_literal(self, tmp_path: Path) -> None:
        # The inner double quotes must stay part of the single-quoted literal;
        # lexing them as their own string turns the alias into a phantom
        # table (found live: data::u from JOIN "user" u).
        _write(
            tmp_path,
            "app/report.py",
            "q = 'SELECT i.id FROM item i JOIN \"user\" u ON i.owner_id = u.id'\n",
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert ids == {"data::item", "data::user"}

    def test_join_tables_all_captured(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/queries.go",
            'var q = "SELECT o.id FROM orders o JOIN customers c ON o.cid = c.id"\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert ids == {"data::orders", "data::customers"}

    def test_interpolated_query_falls_back_to_regex(self, tmp_path: Path) -> None:
        # %s placeholder breaks the sqlglot parse; the verb-anchored regex
        # still recovers the literal table token.
        _write(
            tmp_path,
            "app/db.py",
            'q = "UPDATE orders SET state = %s WHERE id = %s"\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert [c.contract_id for c in consumers] == ["data::orders"]

    def test_interpolated_table_name_skipped(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/db.py",
            'q = f"SELECT * FROM {table} WHERE id = 1"\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        assert [c for c in contracts if c.role == "consumer"] == []

    def test_prose_comment_not_matched(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/notes.py",
            '"""Select the widest rows from users and update the cache."""\n'
            "# fetch data from the users service\n",
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        assert contracts == []

    def test_insert_and_delete_verbs(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/db.ts",
            "const a = `INSERT INTO audit_log (id) VALUES (1)`;\n"
            "const b = `DELETE FROM sessions WHERE expired = true`;\n",
        )
        contracts = DataExtractor().extract(tmp_path, "web")
        by_id = {c.contract_id: c for c in contracts if c.role == "consumer"}
        assert set(by_id) == {"data::audit_log", "data::sessions"}
        assert by_id["data::audit_log"].meta["verb"] == "insert"
        assert by_id["data::sessions"].meta["verb"] == "delete"

    def test_test_files_excluded_by_default(self, tmp_path: Path) -> None:
        from repowise.core.workspace.extractors.base import make_exclude_predicate

        _write(tmp_path, "tests/test_db.py", 'q = "SELECT id FROM users WHERE x = 1"\n')
        contracts = DataExtractor().extract(tmp_path, "api", make_exclude_predicate())
        assert contracts == []

    def test_system_catalogs_and_alembic_version_skipped(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "scripts/promote.py",
            'q1 = "SELECT datname FROM pg_database WHERE datistemplate = false"\n'
            'q2 = "SELECT rolname FROM pg_roles WHERE rolcanlogin = true"\n'
            'q3 = "SELECT relname FROM pg_class WHERE relkind = \'r\'"\n'
            'q4 = "SELECT table_name FROM information_schema.tables WHERE table_schema = \'public\'"\n'
            'q5 = "SELECT version_num FROM alembic_version"\n'
            'q6 = "SELECT nspname FROM pg_catalog.pg_namespace"\n'
            'q7 = "SELECT id FROM orders WHERE promoted = true"\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert [c.contract_id for c in consumers] == ["data::orders"]

    def test_csharp_verbatim_sql(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/Verbatim.cs",
            'const string sql = @"\n'
            "    SELECT *\n"
            "    FROM Orders o\n"
            "    JOIN Customers c ON o.CustomerId = c.Id\n"
            '";\n',
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert ids == {"data::orders", "data::customers"}


class TestMyBatisConsumers:
    def test_mybatis_mapper_xml(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/main/resources/OrderMapper.xml",
            """<?xml version="1.0" encoding="UTF-8" ?>
            <!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
            <mapper namespace="com.shop.mapper.OrderMapper">
                <select id="findOrders">
                    SELECT * FROM orders WHERE id = #{id}
                </select>
                <select id="findLines">
                    SELECT * FROM order_lines WHERE order_id = #{orderId}
                </select>
                <!--
                <select id="deletedOrders">
                    SELECT * FROM archived_orders
                </select>
                -->
            </mapper>
            """,
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert ids == {"data::orders", "data::order_lines"}

    def test_mybatis_array_annotation(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/main/java/com/shop/mapper/CustomerMapper.java",
            """package com.shop.mapper;
            public interface CustomerMapper {
                @Update({
                    "UPDATE customers",
                    "SET name = #{name}",
                    "WHERE id = #{id}"
                })
                int update(Customer c);
            }
            """,
        )
        contracts = DataExtractor().extract(tmp_path, "api")
        ids = {c.contract_id for c in contracts if c.role == "consumer"}
        assert ids == {"data::customers"}


class TestDataMatching:
    def test_normalize_contract_id_lowercases(self) -> None:
        assert normalize_contract_id("data::Users") == "data::users"

    def test_edge_kind_is_db(self) -> None:
        assert edge_kind_for_contract_type("data") == "db"

    def test_cross_repo_link(self, tmp_path: Path) -> None:
        backend = tmp_path / "backend"
        worker = tmp_path / "worker"
        _write(backend, "migrations/001.sql", "CREATE TABLE orders (id INT);")
        _write(worker, "jobs/process.py", 'q = "SELECT id FROM orders WHERE done = 0"\n')

        contracts = DataExtractor().extract(backend, "backend")
        contracts += DataExtractor().extract(worker, "worker")
        links = match_contracts(contracts)

        assert len(links) == 1
        link = links[0]
        assert link.contract_type == "data"
        assert link.provider_repo == "backend"
        assert link.consumer_repo == "worker"
        assert link.match_type == "exact"

    def test_same_repo_not_linked(self, tmp_path: Path) -> None:
        repo = tmp_path / "app"
        _write(repo, "schema.sql", "CREATE TABLE orders (id INT);")
        _write(repo, "db.py", 'q = "SELECT id FROM orders WHERE done = 0"\n')
        contracts = DataExtractor().extract(repo, "app")
        assert match_contracts(contracts) == []

    def test_quoting_and_schema_survive_matching(self, tmp_path: Path) -> None:
        backend = tmp_path / "backend"
        worker = tmp_path / "worker"
        _write(backend, "schema.sql", 'CREATE TABLE "public"."Orders" (id INT);')
        _write(worker, "job.py", 'q = "SELECT id FROM orders WHERE x = 1"\n')
        contracts = DataExtractor().extract(backend, "backend")
        contracts += DataExtractor().extract(worker, "worker")
        assert len(match_contracts(contracts)) == 1

    def test_embedded_database_reader_not_linked_across_repos(self, tmp_path: Path) -> None:
        # A table name shared with another repo's server schema is a second
        # database when the reader opens a SQLite file of its own.
        backend = tmp_path / "backend"
        tool = tmp_path / "tool"
        _write(backend, "migrations/001.sql", "CREATE TABLE repos (id INT);")
        _write(
            tool,
            "store.py",
            'import sqlite3\nq = "SELECT id FROM repos WHERE x = 1"\n',
        )
        _write(
            tool,
            "web/db.ts",
            "import Database from 'better-sqlite3';\n"
            "const q = 'SELECT id FROM repos WHERE x = 1';\n",
        )
        consumers = DataExtractor().extract(tool, "tool")
        assert [c.meta.get("embedded") for c in consumers] == ["sqlite", "sqlite"]
        contracts = DataExtractor().extract(backend, "backend") + consumers
        assert match_contracts(contracts) == []

    def test_embedded_database_reader_linked_within_its_repo(self, tmp_path: Path) -> None:
        from repowise.core.workspace.matching.data import accepts

        repo = tmp_path / "app"
        _write(repo, "schema.sql", "CREATE TABLE repos (id INT);")
        _write(repo, "store.py", 'import sqlite3\nq = "SELECT id FROM repos WHERE x = 1"\n')
        contracts = DataExtractor().extract(repo, "app")
        (consumer,) = [c for c in contracts if c.role == "consumer"]
        (provider,) = [c for c in contracts if c.role == "provider"]
        assert consumer.meta.get("embedded") == "sqlite"
        assert accepts(provider, consumer)

    def test_server_database_reader_still_linked_across_repos(self, tmp_path: Path) -> None:
        backend = tmp_path / "backend"
        worker = tmp_path / "worker"
        _write(backend, "migrations/001.sql", "CREATE TABLE repos (id INT);")
        _write(
            worker,
            "job.py",
            "import psycopg\n"
            'URL = "postgresql://db/app"\n'
            'q = "SELECT id FROM repos WHERE x = 1"\n',
        )
        contracts = DataExtractor().extract(backend, "backend")
        contracts += DataExtractor().extract(worker, "worker")
        assert len(match_contracts(contracts)) == 1

    def test_repo_that_opens_sqlite_in_one_module_keeps_its_tables(self, tmp_path: Path) -> None:
        # The Flask tutorial shape: db.py opens SQLite, auth.py queries it,
        # schema.sql declares it. No other repo reads or owns those tables.
        app = tmp_path / "app"
        server = tmp_path / "server"
        _write(app, "app/db.py", "import sqlite3\n")
        _write(app, "app/auth.py", 'q = "SELECT id FROM user WHERE x = 1"\n')
        _write(app, "app/schema.sql", "CREATE TABLE post (id INT);")
        _write(server, "db/database.sql", "CREATE TABLE user (id INT);")
        _write(server, "db/db.go", 'import _ "github.com/lib/pq"\n')
        _write(server, "models/post.go", 'const q = "SELECT id FROM post WHERE x = 1"\n')
        app_contracts = DataExtractor().extract(app, "app")
        assert {c.meta.get("embedded") for c in app_contracts} == {"sqlite"}
        server_contracts = DataExtractor().extract(server, "server")
        assert len(server_contracts) == 2
        assert all("embedded" not in c.meta for c in server_contracts)
        assert match_contracts(app_contracts + server_contracts) == []

    def test_repo_naming_a_server_database_keeps_the_file_tag(self, tmp_path: Path) -> None:
        # SQLite only as a fallback to DATABASE_URL: the app may run on a shared server.
        app = tmp_path / "app"
        backend = tmp_path / "backend"
        _write(
            app,
            "config.py",
            "URI = os.environ.get('DATABASE_URL', '').replace('postgres://', 'postgresql://')"
            " or 'sqlite:///app.db'\n",
        )
        _write(app, "routes.py", 'q = "SELECT id FROM repos WHERE x = 1"\n')
        _write(backend, "migrations/001.sql", "CREATE TABLE repos (id INT);")
        contracts = DataExtractor().extract(app, "app") + DataExtractor().extract(backend, "backend")
        assert len(match_contracts(contracts)) == 1

    def test_repo_engine_reads_server_tokens_in_any_case(self) -> None:
        from repowise.core.workspace.extractors.data.dialect import repo_embedded_engine

        db = "import sqlite3\n"
        assert repo_embedded_engine([db, "x = 1\n"]) == "sqlite"
        assert repo_embedded_engine([db, "'driver' => 'pgsql',\n"]) is None
        assert repo_embedded_engine([db, "const { Pool } = require('pg');\n"]) is None
        assert repo_embedded_engine([db, "# Works with MariaDB too\n"]) is None
        assert repo_embedded_engine(["x = 1\n"]) is None

    def test_embedded_model_not_shared_with_another_repos_schema(self, tmp_path: Path) -> None:
        _write(tmp_path / "api", "db/schema.sql", "CREATE TABLE refunds (id INT);")
        _write(tmp_path / "admin", "config.php", "<?php\n$db = new PDO('sqlite:app.db');\n")
        _write(tmp_path / "admin", "app/Models/Refund.php", _ELOQUENT_HEAD + "class Refund extends Model {}\n")
        contracts = DataExtractor().extract(tmp_path / "api", "api")
        contracts += DataExtractor().extract(tmp_path / "admin", "admin")
        assert match_contracts(contracts) == []


_ELOQUENT_HEAD = "<?php\nnamespace App\\Models;\nuse Illuminate\\Database\\Eloquent\\Model;\n"


def _data(repo: Path, alias: str = "app") -> dict[tuple[str, str], dict]:
    """``(contract_id, framework or client) -> meta`` for *repo*'s data contracts."""
    return {
        (c.contract_id, c.meta.get("framework") or c.meta["client"]): c.meta
        for c in DataExtractor().extract(repo, alias)
    }


class TestLaravelData:
    def test_model_table_follows_the_naming_convention(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/Models/GuestlistEntry.php", _ELOQUENT_HEAD + "class GuestlistEntry extends Model {}\n")
        _write(
            tmp_path,
            "app/Models/User.php",
            "<?php\nuse Illuminate\\Foundation\\Auth\\User as Authenticatable;\n"
            "class User extends Authenticatable {}\n",
        )
        assert set(_data(tmp_path)) == {
            ("data::guestlist_entries", "eloquent"),
            ("data::users", "eloquent"),
        }

    def test_an_explicit_table_wins_over_the_convention(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/Models/Table.php",
            _ELOQUENT_HEAD + "class Table extends Model {\n    protected $table = 'venue_tables';\n}\n",
        )
        assert set(_data(tmp_path)) == {("data::venue_tables", "eloquent")}

    def test_the_convention_needs_eloquent_and_a_concrete_class(self, tmp_path: Path) -> None:
        # Another library's `Model`, and an abstract base, name no table.
        _write(tmp_path, "src/Widget.php", "<?php\nuse Acme\\Model;\nclass Widget extends Model {}\n")
        _write(tmp_path, "app/Models/Base.php", _ELOQUENT_HEAD + "abstract class Base extends Model {}\n")
        assert _data(tmp_path) == {}

    def test_migrations_create_and_alter_with_schema_meta(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "database/migrations/2024_01_01_000000_create_orders_table.php",
            "<?php\nreturn new class extends Migration {\n"
            "    public function up(): void {\n"
            "        Schema::create('orders', function (Blueprint $t) { $t->id(); });\n"
            "        Schema::connection('audit')->table('order_log', function (Blueprint $t) {});\n"
            "    }\n"
            "    public function down(): void { Schema::dropIfExists('orders'); }\n"
            "};\n",
        )
        found = _data(tmp_path)
        assert found[("data::orders", "laravel-migration")]["schema"] == "create"
        assert found[("data::order_log", "laravel-migration")]["schema"] == "alter"
        assert len(found) == 2

    def test_query_builder_reads_and_writes(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/Services/Stats.php",
            "<?php\nclass Stats {\n    public function run() {\n"
            "        DB::table('tickets')->where('event_id', 1)->count();\n"
            "        DB::table('event_scan_stats')->updateOrInsert(['id' => 1], ['n' => 2]);\n"
            "        DB::table('audit as a')->insert(['x' => 1]);\n"
            "        DB::table('sessions')->where('old', true)->delete();\n"
            "        DB::table('counters')->increment('hits');\n"
            "    }\n}\n",
        )
        verbs = {cid: meta["verb"] for (cid, _), meta in _data(tmp_path).items()}
        assert verbs == {
            "data::tickets": "select",
            "data::event_scan_stats": "update",
            "data::audit": "insert",
            "data::sessions": "delete",
            "data::counters": "update",
        }

    def test_less_common_spellings(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/Models/Seat.php", _ELOQUENT_HEAD + "final class Seat extends Model {}\n")
        _write(
            tmp_path,
            "app/Jobs/Sync.php",
            "<?php\nclass Sync { public function run() {\n"
            "    DB::connection('reporting')->table('rollups')->upsert([], ['id']);\n"
            "    DB::table('scratch')->truncate();\n"
            "    Schema::connection('audit')->create('audit_log', function ($t) {});\n"
            "} }\n",
        )
        found = _data(tmp_path)
        assert ("data::seats", "eloquent") in found
        assert found[("data::rollups", "laravel-db")]["verb"] == "insert"
        assert found[("data::scratch", "laravel-db")]["verb"] == "delete"
        assert found[("data::audit_log", "laravel-migration")]["schema"] == "create"

    def test_a_semicolon_in_a_string_does_not_end_the_statement(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/Jobs/Purge.php",
            "<?php\nDB::table('tokens')->where('note', 'a;b')->delete();\n",
        )
        assert _data(tmp_path)[("data::tokens", "laravel-db")]["verb"] == "delete"

    def test_a_write_inside_a_callback_is_not_the_outer_statement(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/Jobs/Backfill.php",
            "<?php\nDB::table('orders')->orderBy('id')->each(function ($o) {\n"
            "    DB::table('ledger')->insert(['order' => $o->id]);\n});\n",
        )
        verbs = {cid: meta["verb"] for (cid, _), meta in _data(tmp_path).items()}
        assert verbs == {"data::orders": "select", "data::ledger": "insert"}

    def test_an_aliased_eloquent_base_is_missed_not_guessed(self, tmp_path: Path) -> None:
        # Deliberate ceiling: only the framework's own base-class names count.
        _write(
            tmp_path,
            "app/Models/Seat.php",
            "<?php\nuse Illuminate\\Database\\Eloquent\\Model as Eloquent;\nclass Seat extends Eloquent {}\n",
        )
        assert _data(tmp_path) == {}

    def test_a_ddl_create_carries_schema_meta(self, tmp_path: Path) -> None:
        _write(tmp_path, "schema.sql", "CREATE TABLE a (id INT);\nALTER TABLE b ADD COLUMN x INT;")
        found = _data(tmp_path)
        assert found[("data::a", "sql-ddl")]["schema"] == "create"
        assert found[("data::b", "sql-ddl")]["schema"] == "alter"


class TestSharedTables:
    def _extract(self, root: Path, *aliases: str) -> list:
        return [c for a in aliases for c in DataExtractor().extract(root / a, a)]

    def test_a_model_is_linked_to_the_service_owning_the_migration(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "api",
            "database/migrations/2024_create_events.php",
            "<?php\nSchema::create('events', function ($t) {});\n",
        )
        _write(tmp_path / "api", "app/Models/Event.php", _ELOQUENT_HEAD + "class Event extends Model {}\n")
        _write(tmp_path / "admin", "app/Models/Event.php", _ELOQUENT_HEAD + "class Event extends Model {}\n")
        links = match_contracts(self._extract(tmp_path, "admin", "api"))
        assert [(k.consumer_repo, k.consumer_file, k.provider_repo, k.provider_file) for k in links] == [
            ("admin", "app/Models/Event.php", "api", "database/migrations/2024_create_events.php")
        ]
        assert links[0].contract_type == "data"

    def test_models_alone_say_nothing(self, tmp_path: Path) -> None:
        # Nobody defines the schema, so nothing says whose table it is.
        for alias in ("a", "b"):
            _write(tmp_path / alias, "app/Models/Venue.php", _ELOQUENT_HEAD + "class Venue extends Model {}\n")
        assert match_contracts(self._extract(tmp_path, "a", "b")) == []

    def test_two_apps_migrating_one_table_name_are_not_linked(self, tmp_path: Path) -> None:
        # Every stock Laravel app creates `users`; two of them are two databases.
        for alias in ("shop", "blog"):
            _write(
                tmp_path / alias,
                "database/migrations/0001_create_users_table.php",
                "<?php\nSchema::create('users', function ($t) {});\n",
            )
            _write(tmp_path / alias, "app/Models/User.php", _ELOQUENT_HEAD + "class User extends Model {}\n")
        assert match_contracts(self._extract(tmp_path, "shop", "blog")) == []

    def test_an_alter_only_owner_still_owns(self, tmp_path: Path) -> None:
        _write(tmp_path / "api", "db/002.sql", "ALTER TABLE seats ADD COLUMN row INT;")
        _write(tmp_path / "admin", "app/Models/Seat.php", _ELOQUENT_HEAD + "class Seat extends Model {}\n")
        links = match_contracts(self._extract(tmp_path, "admin", "api"))
        assert [(k.consumer_repo, k.provider_repo) for k in links] == [("admin", "api")]

    def test_one_service_declaring_twice_shares_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path / "api", "db/schema.sql", "CREATE TABLE zones (id INT);")
        _write(tmp_path / "api", "app/Models/Zone.php", _ELOQUENT_HEAD + "class Zone extends Model {}\n")
        assert match_contracts(self._extract(tmp_path, "api")) == []

    def test_readers_and_models_both_link(self, tmp_path: Path) -> None:
        # The shared-table pass adds to the exact pass; it does not replace it.
        _write(tmp_path / "api", "db/schema.sql", "CREATE TABLE refunds (id INT);")
        _write(tmp_path / "admin", "app/Models/Refund.php", _ELOQUENT_HEAD + "class Refund extends Model {}\n")
        _write(tmp_path / "worker", "job.py", 'q = "SELECT id FROM refunds WHERE x = 1"\n')
        links = match_contracts(self._extract(tmp_path, "api", "admin", "worker"))
        assert sorted((k.consumer_repo, k.provider_repo) for k in links) == [
            ("admin", "api"),
            ("worker", "admin"),
            ("worker", "api"),
        ]
