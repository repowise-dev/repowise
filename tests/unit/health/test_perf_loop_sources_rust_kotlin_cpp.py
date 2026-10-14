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
    for (k in Kind.entries) { stmt.executeQuery("x") }
}
enum class Role { A, B }
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (3, ("unknown", "parameter")),
        (4, ("bounded", "parameter")),
        (5, ("bounded", "constant")),
        (6, ("bounded", "constant")),  # a file-level read-only list
        (7, ("bounded", "constant")),  # an enum declared in the file
        (8, ("bounded", "constant")),
        (10, ("grows_with_data", "query")),  # a JDBC cursor over a table
        (11, ("unknown", "parameter")),
        (12, ("unknown", "parameter")),
        (13, ("grows_with_data", "query")),  # an Exposed read
        (14, ("unknown", "query")),  # capped in the query
        (15, ("grows_with_data", "listing")),
        (18, ("unknown", "unknown")),  # grown before the loop
        (19, ("unknown", "unknown")),  # a spread adds what ``ids`` holds
        (20, ("unknown", "unknown")),  # ``Kind`` may be any class with ``entries``
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


_SINK_KT = 'stmt.executeQuery("x")'


@pytest.mark.parametrize(
    "body",
    [
        "fun f(items: List<String>, stmt: Statement) {\n    for (i in items) { %s }\n}",
        "fun f(groups: List<List<String>>, stmt: Statement) {\n    for (items in groups) { for (i in items) { %s } }\n}",
        "fun f(p: Pair<List<String>, Int>, stmt: Statement) {\n    val (items, n) = p\n    for (i in items) { %s }\n}",
        "class C(val items: List<String>) {\n    fun f(stmt: Statement) { for (i in items) { %s } }\n}",
        "fun f(m: Map<String, List<String>>, stmt: Statement) {\n    m.forEach { (k, items) -> for (i in items) { %s } }\n}",
    ],
)
def test_a_kotlin_file_level_list_does_not_reach_a_name_declared_around_the_loop(body):
    src = 'val items = listOf("a", "b")\n' + body % _SINK_KT + "\n"
    found = _loops("F.kt", "kotlin", src)
    assert found and all(magnitude == "unknown" for magnitude, _ in found.values()), found


def test_a_kotlin_file_level_var_or_a_list_handed_to_a_call_bounds_nothing():
    kt = _loops(
        "F.kt",
        "kotlin",
        "var KS = listOf(1, 2)\n"
        "fun f(stmt: Statement) {\n"
        f"    for (k in KS) {{ {_SINK_KT} }}\n"
        "    val xs = mutableListOf(1)\n"
        "    fill(xs)\n"
        f"    for (x in xs) {{ {_SINK_KT} }}\n"
        "    val ys = listOf(1, 2)\n"
        f"    for (y in ys) {{ {_SINK_KT} }}\n"
        "}\n",
    )
    assert kt[3] == ("unknown", "unknown")
    assert kt[6] == ("unknown", "unknown")
    assert kt[8] == ("bounded", "constant")


def test_a_kotlin_negation_steps_nothing_and_an_increment_does():
    kt = _loops(
        "F.kt",
        "kotlin",
        "fun f(stmt: Statement, ready: Boolean) {\n"
        "    val n = 3\n"
        "    if (!ready) return\n"
        f"    for (i in 0 until n) {{ {_SINK_KT} }}\n"
        "    var m = 3\n"
        "    m++\n"
        f"    for (i in 0 until m) {{ {_SINK_KT} }}\n"
        "}\n",
    )
    assert kt[4] == ("bounded", "constant")
    assert kt[7] == ("unknown", "unknown")


def test_a_kotlin_name_bound_to_itself_is_followed_once():
    kt = _loops(
        "F.kt",
        "kotlin",
        "class C(val length: Int) {\n"
        "    fun f(stmt: Statement) {\n"
        "        val length = length\n"
        f"        for (i in 0 until length) {{ {_SINK_KT} }}\n"
        "    }\n"
        "}\n",
    )
    assert kt[4] == ("unknown", "unknown")


_SINK_RS = 'sqlx::query("x").fetch_one(pool).await?;'


def test_rust_shadowing_mutation_and_macros_bound_nothing():
    rs = _loops(
        "f.rs",
        "rust",
        "static mut XS: [i32; 2] = [1, 2];\n"
        "const KS: [i32; 2] = [1, 2];\n"
        "fn f(pool: &PgPool, KS: Vec<i32>, other: Vec<i32>) {\n"
        f"    for x in XS {{ {_SINK_RS} }}\n"
        f"    for k in KS {{ {_SINK_RS} }}\n"
        "    let ys = vec![1, 2];\n"
        "    let ys = other;\n"
        f"    for y in ys {{ {_SINK_RS} }}\n"
        "    let mut zs = vec![1, 2];\n"
        "    fill(&mut zs);\n"
        f"    for z in zs {{ {_SINK_RS} }}\n"
        f"    for m in my_items!() {{ {_SINK_RS} }}\n"
        "    let ws = vec![1, 2];\n"
        "    show(&ws);\n"
        f"    for w in &ws {{ {_SINK_RS} }}\n"
        "}\n",
    )
    assert rs[4] == ("unknown", "unknown")  # static mut
    assert rs[5] == ("unknown", "parameter")  # a parameter shadows the const
    assert rs[8] == ("unknown", "unknown")  # bound twice
    assert rs[11] == ("unknown", "unknown")  # handed out as ``&mut``
    assert rs[12] == ("unknown", "unknown")  # a macro other than ``vec!``
    assert rs[15] == ("bounded", "constant")  # a shared borrow cannot grow it


_SINK_CPP = 'sqlite3_exec(db, "x", 0, 0, 0);'


def test_cpp_a_namespace_const_does_not_reach_a_local_or_member_of_the_same_name():
    cpp = _loops(
        "f.cpp",
        "cpp",
        "namespace n { static const int kItems[] = {1, 2}; }\n"
        "void f(sqlite3* db, const std::vector<int>& kItems) {\n"
        f"  for (int x : kItems) {{ {_SINK_CPP} }}\n"
        "}\n"
        "void g(sqlite3* db) {\n"
        "  std::vector<int> kItems;\n"
        "  fill(kItems);\n"
        f"  for (int x : kItems) {{ {_SINK_CPP} }}\n"
        "}\n"
        "void h(sqlite3* db) {\n"
        "  const int* kItems = nullptr;\n"
        f"  for (int i = 0; i < 2; ++i) {{ for (int x : kItems) {{ {_SINK_CPP} }} }}\n"
        "}\n"
        "struct S {\n"
        "  std::vector<int> kItems;\n"
        f"  void m(sqlite3* db) {{ for (int x : kItems) {{ {_SINK_CPP} }} }}\n"
        "};\n"
        "void k(sqlite3* db) {\n"
        f"  for (int x : kItems) {{ {_SINK_CPP} }}\n"
        "  std::vector<int> local = {1, 2};\n"
        "  fill(local);\n"
        f"  for (int x : local) {{ {_SINK_CPP} }}\n"
        "  my::array<int, 4> fake;\n"
        f"  for (int x : fake) {{ {_SINK_CPP} }}\n"
        "}\n",
    )
    assert cpp[3] == ("unknown", "parameter")
    assert cpp[8] == ("unknown", "unknown")  # declared without a value
    assert cpp[12] == ("unknown", "unknown")  # a null pointer, not the const
    assert cpp[16] == ("unknown", "unknown")  # a member
    assert cpp[19] == ("bounded", "constant")  # the namespace const itself
    assert cpp[22] == ("unknown", "unknown")  # handed to a call by reference
    assert cpp[24] == ("unknown", "unknown")  # not ``std::array``


@pytest.mark.parametrize(
    ("language", "src"),
    [
        ("rust", "fn f(xs: &[i64]) {{ for x in xs{} {{ }} }}", ),
        ("kotlin", "fun f(xs: List<Int>) {{ for (x in xs{}) {{ }} }}"),
    ],
)
def test_a_header_chain_hundreds_of_links_long_stays_unknown(language, src):
    from tree_sitter import Parser

    from repowise.core.analysis.health.perf.dialects import PERF_DIALECTS
    from repowise.core.ingestion.parser import _get_language

    link = ".iter().map(|x| x)" if language == "rust" else ".filter { it > 0 }"
    tree = Parser(_get_language(language)).parse(src.format(link * 2000).encode())
    loop = next(n for n in _nodes(tree.root_node) if n.type in ("for_expression", "for_statement"))
    assert PERF_DIALECTS[language].loop_source(loop, lambda call: None) is None


def _nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


_RECURSIVE = (
    "WITH RECURSIVE tree(id) AS (SELECT id FROM s WHERE id = ? "
    "UNION SELECT s.id FROM s JOIN tree ON s.parent_id = tree.id) SELECT * FROM tree"
)


@pytest.mark.parametrize("sql", ["SELECT id FROM t", "SELECT id FROM t LIMIT ?", _RECURSIVE])
def test_a_bound_limit_or_a_read_below_one_row_only_moves_a_loop_to_unknown(sql):
    """In every dialect on the shared reader, not only the three above. Python
    sizes a read with its own rules, which these do not change."""
    grows = sql == "SELECT id FROM t"
    go = _loops(
        "f.go",
        "go",
        'package m\nimport "database/sql"\n'
        "func f(db *sql.DB, n int) {\n"
        f'    rows, _ := db.Query("{sql}", n)\n'
        '    for rows.Next() { db.Exec("x") }\n'
        "}\n",
    )
    java = _loops(
        "A.java",
        "java",
        "import java.sql.*;\n"
        "class A { void f(Statement stmt, UserRepository userRepository) {\n"
        f'  ResultSet rs = stmt.executeQuery("{sql}");\n'
        "  while (rs.next()) { userRepository.findById(rs.getLong(1)); } } }\n",
    )
    py = _loops(
        "a.py",
        "python",
        "import sqlite3\n"
        "def f(conn):\n"
        f'    rows = conn.execute("{sql}").fetchall()\n'
        "    for r in rows:\n"
        '        conn.execute("UPDATE t SET x = 1 WHERE id = ?", (r[0],))\n',
    )
    for found, line in ((go, 5), (java, 4)):
        assert found[line] == ("grows_with_data" if grows else "unknown", "query")
    assert py[5][0] == "grows_with_data"


_ALL_ROOTS = (
    "WITH RECURSIVE tree(id) AS (SELECT id FROM s WHERE parent_id IS NULL "
    "UNION SELECT s.id FROM s JOIN tree ON s.parent_id = tree.id WHERE s.id = ?) SELECT * FROM tree"
)


@pytest.mark.parametrize(("sql", "parent"), [(_RECURSIVE, True), (_ALL_ROOTS, False)])
def test_a_recursive_read_is_one_row_s_tree_only_when_its_seed_keys_on_one_id(sql, parent):
    from repowise.core.analysis.health.perf import loop_lexicon as lex

    assert lex.is_parent_key_filter(sql.encode()) is parent
    go = _loops(
        "f.go",
        "go",
        'package m\nimport "database/sql"\n'
        "func f(db *sql.DB, n int) {\n"
        f'    rows, _ := db.Query("{sql}", n)\n'
        '    for rows.Next() { db.Exec("x") }\n'
        "}\n",
    )
    assert go[5] == ("unknown" if parent else "grows_with_data", "query")
