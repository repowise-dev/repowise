"""What a perf loop walks, and the magnitude that proves, in Rust, Kotlin and C++.

Through the real walker, as for the other hook dialects: each shape that grows
or is bounded sits beside one the syntax does not settle, which stays ``unknown``.
"""

from __future__ import annotations

import pytest

from tests.unit.health.test_perf_loop_sources_dialects import _loops

_RUST = """const KINDS: &[&str] = &["a", "b"];
fn f(ids: &[i64], pool: &PgPool, repo_root: &Path, dir: &Path) {
    for id in ids { sqlx::query("x").fetch_one(pool).await?; }
    for id in ids.iter().take(5) { sqlx::query("x").fetch_one(pool).await?; }
    for k in vec!["a", "b"] { sqlx::query("x").fetch_one(pool).await?; }
    for k in KINDS { sqlx::query("x").fetch_one(pool).await?; }
    let rows = sqlx::query("SELECT id FROM orders").fetch_all(pool).await?;
    for r in &rows { sqlx::query("x").fetch_one(pool).await?; }
    let one = sqlx::query("SELECT * FROM orders WHERE id = $1").fetch_all(pool).await?;
    for r in one { sqlx::query("x").fetch_one(pool).await?; }
    let mut s = sqlx::query("SELECT id FROM orders").fetch(pool);
    while let Some(r) = s.try_next().await? { sqlx::query("x").fetch_one(pool).await?; }
    for e in WalkDir::new(repo_root) { sqlx::query("x").fetch_one(pool).await?; }
    for e in WalkDir::new(repo_root).max_depth(1) { sqlx::query("x").fetch_one(pool).await?; }
    for e in std::fs::read_dir(dir)? { sqlx::query("x").fetch_one(pool).await?; }
    for i in 0..ids.len() { sqlx::query("x").fetch_one(pool).await?; }
    let mut grown = vec![1];
    grown.extend(ids);
    for g in grown { sqlx::query("x").fetch_one(pool).await?; }
    let built = builder.build().fetch_all(pool).await?;
    for b in built { sqlx::query("x").fetch_one(pool).await?; }
    for p in &self.providers { sqlx::query("x").fetch_one(pool).await?; }
}
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (3, ("unknown", "parameter")),
        (4, ("bounded", "parameter")),  # a literal cap on what is in hand
        (5, ("bounded", "constant")),
        (6, ("bounded", "constant")),  # a file-level const array
        (8, ("grows_with_data", "query")),
        (10, ("bounded", "caller_key")),
        (12, ("grows_with_data", "query")),  # a sqlx row stream
        (13, ("grows_with_data", "listing")),  # a recursive walk of the repository
        (14, ("unknown", "listing")),  # capped at one level
        (15, ("unknown", "listing")),
        (16, ("unknown", "parameter")),
        (19, ("unknown", "unknown")),  # the literal was extended before the loop
        (21, ("unknown", "query")),  # SQL pushed onto a builder out of sight
        (22, ("unknown", "config_table")),
    ],
)
def test_rust_loop_sources(line, expected):
    assert _loops("f.rs", "rust", _RUST)[line] == expected


_KOTLIN = """private val KINDS = listOf("a", "b")
fun f(ids: List<Long>, n: Int, stmt: Statement, repoRoot: File) {
    for (id in ids) { stmt.executeQuery("x") }
    ids.take(5).forEach { stmt.executeQuery("x") }
    for (k in listOf("a", "b")) { stmt.executeQuery("x") }
    for (k in KINDS) { stmt.executeQuery("x") }
    for (r in Role.entries) { stmt.executeQuery("x") }
    for (r in Role.values()) { stmt.executeQuery("x") }
    val rs = stmt.executeQuery("SELECT * FROM orders")
    while (rs.next()) { stmt.executeQuery("x") }
    for (i in 0 until n) { stmt.executeQuery("x") }
    for (i in 0 until ids.size) { stmt.executeQuery("x") }
    Users.selectAll().forEach { stmt.executeQuery("x") }
    Users.selectAll().limit(10).forEach { stmt.executeQuery("x") }
    for (f in repoRoot.walkTopDown()) { stmt.executeQuery("x") }
    val grown = mutableListOf(1)
    grown.add(2)
    for (g in grown) { stmt.executeQuery("x") }
    for (x in arrayOf(*ids.toTypedArray())) { stmt.executeQuery("x") }
}
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (3, ("unknown", "parameter")),
        (4, ("bounded", "parameter")),
        (5, ("bounded", "constant")),
        (6, ("bounded", "constant")),  # a file-level read-only list
        (7, ("bounded", "constant")),  # an enum's entries
        (8, ("bounded", "constant")),
        (10, ("grows_with_data", "query")),  # a JDBC cursor over a table
        (11, ("unknown", "parameter")),
        (12, ("unknown", "parameter")),
        (13, ("grows_with_data", "query")),  # an Exposed read
        (14, ("unknown", "query")),  # capped in the query
        (15, ("grows_with_data", "listing")),
        (18, ("unknown", "unknown")),  # grown before the loop
        (19, ("unknown", "unknown")),  # a spread adds what ``ids`` holds
    ],
)
def test_kotlin_loop_sources(line, expected):
    assert _loops("F.kt", "kotlin", _KOTLIN)[line] == expected


_CPP = """static const char* kNames[] = {"a", "b"};
void f(sqlite3* db, const std::vector<int>& ids, int n) {
  for (int id : ids) { sqlite3_exec(db, "x", 0, 0, 0); }
  for (auto k : {1, 2, 3}) { sqlite3_exec(db, "x", 0, 0, 0); }
  for (const char* k : kNames) { sqlite3_exec(db, "x", 0, 0, 0); }
  for (size_t i = 0; i < ids.size(); ++i) { sqlite3_exec(db, "x", 0, 0, 0); }
  for (auto it = ids.begin(); it != ids.end(); ++it) { sqlite3_exec(db, "x", 0, 0, 0); }
  std::array<int, 4> fixed;
  for (int x : fixed) { sqlite3_exec(db, "x", 0, 0, 0); }
  std::vector<int> grown = {1};
  grown.push_back(2);
  for (int g : grown) { sqlite3_exec(db, "x", 0, 0, 0); }
  sqlite3_stmt* stmt;
  sqlite3_prepare_v2(db, "SELECT id FROM orders", -1, &stmt, nullptr);
  while (sqlite3_step(stmt) == SQLITE_ROW) { sqlite3_exec(db, "x", 0, 0, 0); }
  for (auto& e : std::filesystem::recursive_directory_iterator(repo_root)) { sqlite3_exec(db, "x", 0, 0, 0); }
  for (auto& e : std::filesystem::directory_iterator(dir)) { sqlite3_exec(db, "x", 0, 0, 0); }
  for (auto& x : items_) { sqlite3_exec(db, "x", 0, 0, 0); }
}
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (3, ("unknown", "parameter")),
        (4, ("bounded", "constant")),
        (5, ("bounded", "constant")),  # a file-level const array
        (6, ("unknown", "parameter")),
        (7, ("unknown", "parameter")),  # an iterator walk over ``ids``
        (9, ("bounded", "constant")),  # a std::array's fixed size
        (12, ("unknown", "unknown")),  # grown before the loop
        (15, ("grows_with_data", "query")),  # a sqlite cursor over a table
        (16, ("grows_with_data", "listing")),
        (17, ("unknown", "listing")),
        (18, ("unknown", "unknown")),  # a field
    ],
)
def test_cpp_loop_sources(line, expected):
    assert _loops("f.cpp", "cpp", _CPP)[line] == expected


def test_a_read_capped_by_a_bound_limit_is_a_query_of_unknown_size():
    rs = _loops(
        "f.rs",
        "rust",
        "fn f(pool: &PgPool, n: i64) {\n"
        '    let page = sqlx::query("SELECT id FROM t LIMIT ? OFFSET ?").bind(n).fetch_all(pool).await?;\n'
        '    for r in page { sqlx::query("x").fetch_one(pool).await?; }\n'
        "}\n",
    )
    assert rs[3] == ("unknown", "query")


def test_a_recursive_read_below_one_row_is_a_query_of_unknown_size():
    rs = _loops(
        "f.rs",
        "rust",
        "fn f(pool: &PgPool, id: i64) {\n"
        '    let rows = sqlx::query("WITH RECURSIVE tree(id) AS (SELECT id FROM s WHERE id = ? '
        'UNION SELECT s.id FROM s JOIN tree ON s.parent_id = tree.id) SELECT * FROM tree")'
        ".bind(id).fetch_all(pool).await?;\n"
        '    for r in rows { sqlx::query("x").fetch_one(pool).await?; }\n'
        "}\n",
    )
    assert rs[3] == ("unknown", "query")


def test_a_binding_that_may_not_reach_the_loop_bounds_nothing():
    rs = _loops(
        "f.rs",
        "rust",
        "fn f(pool: &PgPool, ids: Vec<i64>, small: bool) {\n"
        "    let mut xs = ids;\n"
        "    if small { xs = vec![1, 2]; }\n"
        '    for x in xs { sqlx::query("x").fetch_one(pool).await?; }\n'
        "}\n",
    )
    assert rs[4] == ("unknown", "unknown")


def test_a_statement_prepared_into_a_null_pointer_is_its_read():
    cpp = _loops(
        "f.cpp",
        "cpp",
        "void f(sqlite3* db) {\n"
        "  sqlite3_stmt* stmt = nullptr;\n"
        '  sqlite3_prepare_v2(db, "SELECT id FROM orders", -1, &stmt, nullptr);\n'
        '  while (sqlite3_step(stmt) == SQLITE_ROW) { sqlite3_exec(db, "x", 0, 0, 0); }\n'
        "}\n",
    )
    assert cpp[4] == ("grows_with_data", "query")
