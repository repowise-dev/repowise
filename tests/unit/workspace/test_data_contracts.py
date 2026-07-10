"""Data (app-to-database) contract extraction tests.

Table-name normalization is tested first and hardest: it is where the false
links would come from (quoting, schema prefixes, case). Then per-dialect
provider/consumer extraction, then the cross-repo end-to-end match.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import match_contracts, normalize_contract_id
from repowise.core.workspace.extractors.data import DataExtractor
from repowise.core.workspace.extractors.data.names import (
    normalize_table_name,
    split_qualified,
)
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
        assert any(c.contract_id == "data::orders" for c in contracts)

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
