"""What a perf loop walks, and the magnitude that proves, in Go, Java, C# and Ruby.

Through the real walker. Each shape that grows or is bounded is paired with one
the syntax does not settle, which must stay ``unknown``: no reader takes
``unknown`` as bounded.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file


def _loops(path: str, language: str, src: str) -> dict[int, tuple[str, str]]:
    """``{hit line: (magnitude, iterates)}`` for every io_in_loop hit."""
    out: dict[int, tuple[str, str]] = {}
    for hit in walk_file(path, language, src.encode()).perf_hits:
        if hit.kind == "io_in_loop":
            facts = hit.loop
            out[hit.line] = (facts.magnitude, facts.iterates) if facts else ("unknown", "unknown")
    return out


_GO = """package m
import "database/sql"
func f(db *sql.DB, ids []int) {
    rows, _ := db.Query("SELECT id FROM orders")
    for rows.Next() { db.Exec("UPDATE x SET y = 1") }
    for _, id := range ids { db.Exec("DELETE FROM t WHERE id = ?", id) }
    for _, id := range ids[:5] { db.Exec("DELETE FROM t WHERE id = ?", id) }
    for i := 0; i < len(ids); i++ { db.Exec("DELETE FROM t WHERE id = ?", ids[i]) }
    var users []User
    db.Find(&users)
    for _, u := range users { db.Exec("x", u) }
    for _, s := range []string{"a", "b"} { db.Exec("x", s) }
    one, _ := db.Query("SELECT * FROM t WHERE id = ?", 3)
    for one.Next() { db.Exec("x") }
    top, _ := db.Query("SELECT * FROM t LIMIT 10")
    for top.Next() { db.Exec("x") }
    for _, r := range repos { db.Exec("x", r) }
}
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (5, ("grows_with_data", "query")),  # a sql.Rows cursor over a table
        (6, ("unknown", "parameter")),
        (7, ("bounded", "parameter")),  # a fixed slice keeps what it walks
        (8, ("unknown", "parameter")),
        (11, ("grows_with_data", "query")),  # GORM fills ``&users``
        (12, ("bounded", "constant")),
        (14, ("bounded", "caller_key")),
        (16, ("unknown", "query")),  # capped in its SQL
        (17, ("unknown", "config_table")),  # a registry's name bounds nothing
    ],
)
def test_go_loop_sources(line, expected):
    assert _loops("f.go", "go", _GO)[line] == expected


_JAVA = """import java.sql.*;
class A {
  void f(List<Long> ids, UserRepository userRepository) {
    List<User> us = userRepository.findAll();
    for (User u : us) { userRepository.findById(u.getId()); }
    for (Long id : ids) { userRepository.findById(id); }
    for (int i = 0; i < us.size(); i++) { userRepository.findById(ids.get(i)); }
    ResultSet rs = stmt.executeQuery("SELECT * FROM orders");
    while (rs.next()) { userRepository.findById(rs.getLong(1)); }
    for (String s : List.of("a", "b")) { userRepository.findByName(s); }
    for (Role r : Role.values()) { userRepository.findByRole(r); }
    for (User u : userRepository.findAll().stream().limit(5).toList()) { userRepository.findById(u.getId()); }
    for (User u : userRepository.findAll().stream().filter(x -> x.ok).toList()) { userRepository.findById(u.getId()); }
    for (Long id : Arrays.asList(ids)) { userRepository.findById(id); }
  }
}
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (5, ("grows_with_data", "query")),
        (6, ("unknown", "parameter")),
        (7, ("grows_with_data", "query")),  # ``i < us.size()`` walks ``us``
        (9, ("grows_with_data", "query")),  # a JDBC ResultSet cursor
        (10, ("bounded", "constant")),
        (11, ("bounded", "constant")),  # an enum's constants
        (12, ("bounded", "query")),
        (13, ("grows_with_data", "query")),
        (14, ("unknown", "parameter")),  # ``asList`` of one array walks it
    ],
)
def test_java_loop_sources(line, expected):
    assert _loops("A.java", "java", _JAVA)[line] == expected


_CS = """using Microsoft.EntityFrameworkCore;
class A {
  async Task F(List<int> ids) {
    var us = await _context.Users.Where(u => u.Active).ToListAsync();
    foreach (var u in us) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == u.Id); }
    foreach (var id in ids) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == id); }
    foreach (var id in ids.Where(x => x > 0).ToList()) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == id); }
    foreach (var u in _context.Users) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == u.Id); }
    foreach (var s in new[] { "a", "b" }) { await _context.Orders.FirstOrDefaultAsync(o => o.Name == s); }
    for (int i = 0; i < ids.Count; i++) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == ids[i]); }
    using var reader = await cmd.ExecuteReaderAsync();
    while (await reader.ReadAsync()) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == 1); }
    foreach (var x in ids.Take(5)) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == x); }
    foreach (var y in [1, ..ids]) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == y); }
  }
}
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (5, ("grows_with_data", "query")),
        (6, ("unknown", "parameter")),
        # Sync LINQ on a list the caller passed is not a database read.
        (7, ("unknown", "parameter")),
        (8, ("grows_with_data", "query")),  # walking a DbSet reads the table
        (9, ("bounded", "constant")),
        (10, ("unknown", "parameter")),
        (12, ("grows_with_data", "query")),  # a data reader cursor
        (13, ("bounded", "parameter")),
        (14, ("unknown", "unknown")),  # a spread is as long as what it spreads
    ],
)
def test_csharp_loop_sources(line, expected):
    assert _loops("A.cs", "csharp", _CS)[line] == expected


_RUBY = """require "active_record"
def f(ids)
  users = User.where(active: true)
  users.each do |u|
    Order.where(user_id: u.id).to_a
  end
  User.find_each { |u| Order.where(user_id: u.id).to_a }
  for x in ids
    Order.where(user_id: x).to_a
  end
  %w[a b].each { |s| Order.where(name: s).to_a }
  ids.first(5).each_with_index { |x, i| Order.where(user_id: x).to_a }
  ids.size.times { |i| Order.where(user_id: ids[i]).to_a }
  User.all.each { |u| Order.where(user_id: u.id).to_a }
  Dir.glob("*.rb").each { |p| Order.where(path: p).to_a }
  @posts.each { |p| Order.where(post_id: p.id).to_a }
  User.limit(10).each { |u| Order.where(user_id: u.id).to_a }
end
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (5, ("grows_with_data", "query")),  # a relation on a model, one hop back
        (7, ("grows_with_data", "query")),  # find_each walks the table in batches
        (9, ("unknown", "parameter")),
        (11, ("bounded", "constant")),
        (12, ("bounded", "parameter")),
        (13, ("unknown", "parameter")),  # ``ids.size.times`` walks ``ids``
        (14, ("grows_with_data", "query")),
        (15, ("unknown", "listing")),  # a flat glob of unknown size
        (16, ("unknown", "unknown")),
        (17, ("unknown", "query")),  # capped in the relation
    ],
)
def test_ruby_loop_sources(line, expected):
    assert _loops("a.rb", "ruby", _RUBY)[line] == expected


def test_a_ruby_block_loop_never_claims_its_key_is_unused():
    # Facts on block loops say what they walk; they leave batch advice as it was.
    hits = walk_file(
        "a.rb",
        "ruby",
        b'require "active_record"\n'
        b"def f(ids)\n"
        b"  ids.each { |id| Order.find_by(id: 1) }\n"
        b"end\n",
    ).perf_hits
    facts = [h.loop for h in hits if h.kind == "io_in_loop"]
    assert facts and all(f is None or not f.key_unused for f in facts)


def test_an_empty_literal_filled_before_the_loop_is_no_constant():
    cs = _loops(
        "A.cs",
        "csharp",
        "using Microsoft.EntityFrameworkCore;\n"
        "class A {\n"
        "  async Task F(List<int> ids) {\n"
        "    List<int> made = [];\n"
        "    foreach (var id in ids) { made.Add(id); }\n"
        "    foreach (var m in made) { await _context.Orders.FirstOrDefaultAsync(o => o.Id == m); }\n"
        "  }\n"
        "}\n",
    )
    assert cs[6] == ("unknown", "unknown")
    rb = _loops(
        "a.rb",
        "ruby",
        'require "active_record"\n'
        "def f(xs)\n"
        "  seen = ['a']\n"
        "  xs.each { |x| seen << x }\n"
        "  seen.each { |s| Order.find_by(name: s) }\n"
        "end\n",
    )
    assert rb[5] == ("unknown", "unknown")


def test_a_counter_stepped_after_its_literal_is_no_constant():
    cs = _loops(
        "A.cs",
        "csharp",
        "using Microsoft.EntityFrameworkCore;\n"
        "class A {\n"
        "  async Task F(int total) {\n"
        "    int rows = 1;\n"
        "    while (total / rows >= rows) { rows++; }\n"
        "    for (int r = 0; r < rows; r++) { await _context.Orders.FirstOrDefaultAsync(o => o.Id == r); }\n"
        "  }\n"
        "}\n",
    )
    assert cs[6] == ("unknown", "unknown")


def test_go_a_slice_written_by_create_is_still_the_literal():
    go = _loops(
        "f.go",
        "go",
        'package m\nimport "database/sql"\n'
        "func f(db *sql.DB) {\n"
        '    menus := []Menu{{Name: "a"}, {Name: "b"}}\n'
        "    db.Create(&menus)\n"
        '    for _, m := range menus { db.Exec("x", m) }\n'
        "}\n",
    )
    assert go[6] == ("bounded", "constant")


def test_go_an_empty_slice_literal_is_no_constant():
    go = _loops(
        "f.go",
        "go",
        'package m\nimport "database/sql"\n'
        "func f(db *sql.DB) {\n"
        "    xs := []string{}\n"
        '    for _, x := range xs { db.Exec("x", x) }\n'
        "}\n",
    )
    assert go[5] == ("unknown", "unknown")


def test_java_a_recursive_walk_of_the_repository_root_grows():
    java = _loops(
        "A.java",
        "java",
        "import java.nio.file.*;\n"
        "class A { void f(Path repoRoot, Path dir, UserRepository userRepository) {\n"
        " for (Path p : Files.walk(repoRoot).toList()) { userRepository.findById(1L); }\n"
        " for (Path p : Files.list(dir).toList()) { userRepository.findById(1L); } } }\n",
    )
    assert java[3] == ("grows_with_data", "listing")
    assert java[4] == ("unknown", "listing")


def test_the_children_of_one_parent_are_a_query_of_unknown_size():
    go = _loops(
        "f.go",
        "go",
        'package m\nimport "database/sql"\n'
        "func f(db *sql.DB, id int) {\n"
        "    var menus []Menu\n"
        '    db.Where("authority_id = ?", id).Find(&menus)\n'
        '    for _, m := range menus { db.Exec("x", m) }\n'
        "}\n",
    )
    assert go[6] == ("unknown", "query")
    rb = _loops(
        "a.rb",
        "ruby",
        'require "active_record"\n'
        "def f(user)\n"
        "  Post.where(user_id: user.id).each { |p| Comment.find_by(post_id: p.id) }\n"
        "  User.active.joins(:posts).limit(30).each { |u| Comment.find_by(user_id: u.id) }\n"
        "end\n",
    )
    assert rb[3] == ("unknown", "query")
    assert rb[4][0] == "unknown"  # a read's own cap, on a scope we cannot see into


def test_a_binding_under_a_branch_leaves_the_loop_unknown():
    # ``items`` is a read or, on one path, a literal: neither size is sure.
    go = _loops(
        "f.go",
        "go",
        'package m\nimport "database/sql"\n'
        "func f(db *sql.DB) {\n"
        "    var items []Item\n"
        "    db.Find(&items)\n"
        "    if len(items) == 0 {\n"
        '        items = []Item{{Name: "a"}}\n'
        "    }\n"
        '    for _, it := range items { db.Exec("x", it) }\n'
        "    var only []Item\n"
        "    if ok {\n"
        '        only = []Item{{Name: "a"}}\n'
        "    }\n"
        '    for _, it := range only { db.Exec("x", it) }\n'
        "}\n",
    )
    assert go[9][0] == "unknown"
    assert go[14][0] == "unknown"
    java = _loops(
        "A.java",
        "java",
        "class A { void f(boolean ok, UserRepository userRepository) {\n"
        "  List<String> names = List.of(\"a\", \"b\");\n"
        "  if (ok) { names = userRepository.findAllNames(); }\n"
        "  for (String n : names) { userRepository.findByName(n); } } }\n",
    )
    assert java[4][0] == "unknown"


def test_a_read_in_an_if_initializer_still_reaches_the_loop():
    go = _loops(
        "f.go",
        "go",
        'package m\nimport "database/sql"\n'
        "func f(db *sql.DB) error {\n"
        "    var users []User\n"
        "    if err := db.Find(&users).Error; err != nil {\n"
        "        return err\n"
        "    }\n"
        '    for _, u := range users { db.Exec("x", u) }\n'
        "    return nil\n"
        "}\n",
    )
    assert go[8] == ("grows_with_data", "query")


@pytest.mark.parametrize(
    ("path", "language", "src", "line"),
    [
        (
            "a.rb",
            "ruby",
            'require "active_record"\ndef f(ids, n)\n'
            "  ids.first(n).each { |x| Order.find_by(user: x) }\nend\n",
            3,
        ),
        (
            "A.cs",
            "csharp",
            "using Microsoft.EntityFrameworkCore;\nclass A {\n"
            "  async Task F(List<int> ids, int n) {\n"
            "    foreach (var x in ids.Take(n)) { await _context.Orders.FirstOrDefaultAsync(o => o.UserId == x); }\n"
            "  }\n}\n",
            4,
        ),
        (
            "A.java",
            "java",
            "class A { void f(List<Long> ids, int max, UserRepository userRepository) {\n"
            "  for (Long id : ids.stream().limit(max).toList()) { userRepository.findById(id); } } }\n",
            2,
        ),
    ],
)
def test_a_cap_that_is_not_a_literal_bounds_nothing(path, language, src, line):
    assert _loops(path, language, src)[line][0] == "unknown"


def test_returning_the_error_of_a_call_is_no_first_match():
    hits = walk_file(
        "f.go",
        "go",
        b'package m\nimport "database/sql"\n'
        b"func f(db *sql.DB, ids []int) error {\n"
        b"    for _, id := range ids {\n"
        b'        if _, err := db.Exec("DELETE FROM t WHERE id = ?", id); err != nil {\n'
        b"            return err\n"
        b"        }\n"
        b"    }\n"
        b"    return nil\n"
        b"}\n",
    ).perf_hits
    go = [h.loop for h in hits if h.kind == "io_in_loop"]
    assert go and all(f is None or f.not_batchable is None for f in go)
    hits = walk_file(
        "f.py",
        "python",
        b"import requests\n"
        b"def push(urls):\n"
        b"    for url in urls:\n"
        b"        resp, err = send(requests.post(url))\n"
        b"        if err:\n"
        b"            return err\n",
    ).perf_hits
    py = [h.loop for h in hits if h.kind == "io_in_loop"]
    assert py and all(f is None or f.not_batchable is None for f in py)
