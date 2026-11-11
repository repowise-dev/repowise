"""What a perf loop walks, and the magnitude that proves, in Go, Java, C#, Pascal and Ruby.

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


_PASCAL = """unit Orders;
interface
uses FireDAC.Comp.Client;
procedure Sync(Q: TFDQuery; const Ids: array of Integer; Flag: Boolean);
implementation
procedure Sync(Q: TFDQuery; const Ids: array of Integer; Flag: Boolean);
var
  I, N: Integer;
begin
  Q.SQL.Text := 'SELECT id FROM orders';
  Q.Open;
  while not Q.Eof do
  begin
    Q.ExecSQL;
    Q.Next;
  end;
  for I := 0 to High(Ids) do
    Q.ExecSQL;
  N := 3;
  for I := 1 to N do
    Q.ExecSQL;
  if Flag then
    N := Length(Ids);
  for I := 1 to N do
    Q.ExecSQL;
end;
end.
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (14, ("grows_with_data", "query")),  # a data set opened on an uncapped SELECT
        (18, ("unknown", "parameter")),
        (21, ("bounded", "constant")),
        (25, ("unknown", "unknown")),  # N rebound under a branch before the loop
    ],
)
def test_pascal_loop_sources(line, expected):
    assert _loops("Orders.pas", "pascal", _PASCAL)[line] == expected


_PASCAL_SHAPES = """unit Shapes;
interface
uses FireDAC.Comp.Client, System.IOUtils;
implementation
procedure Run(D: TFDQuery; L: TStringList; RepoRoot, Dir: string; Flag: Boolean);
var
  One, Top, Form, Lines, Branch, Arg, Rep: TFDQuery;
  S: string; I, N, M, K: Integer; E: TColor; Names: TArray<string>;
begin
  One.SQL.Text := 'SELECT * FROM t WHERE id = :id';
  One.Open;
  while not One.Eof do begin D.ExecSQL; One.Next; end;
  Top.SQL.Text := 'SELECT * FROM t LIMIT 10';
  Top.Open;
  while not Top.Eof do begin D.ExecSQL; Top.Next; end;
  while not D.Eof do begin D.ExecSQL; D.Next; end;
  Form.Open;
  while not Form.Eof do begin D.ExecSQL; Form.Next; end;
  Lines.SQL.Add('SELECT id');
  Lines.SQL.Add('FROM orders');
  Lines.Open;
  while not Lines.Eof do begin D.ExecSQL; Lines.Next; end;
  if Flag then Branch.Open;
  while not Branch.Eof do begin D.ExecSQL; Branch.Next; end;
  Arg.Open('SELECT id FROM orders');
  while not (Arg.Eof) do begin D.ExecSQL; Arg.Next; end;
  Rep.CommandText := 'SELECT id FROM orders';
  Rep.Open();
  repeat D.ExecSQL; Rep.Next; until Rep.Eof;
  for S in L do D.ExecSQL;
  for I in [1, 2, 3] do D.ExecSQL;
  for E in TColor do D.ExecSQL;
  for E := Low(TColor) to High(TColor) do D.ExecSQL;
  for I := 0 to L.Count - 1 do D.ExecSQL;
  for I := L.Count - 1 downto 0 do D.ExecSQL;
  n := 3;
  for I := 1 to N do D.ExecSQL;
  M := 3;
  Inc(M);
  for I := 1 to M do D.ExecSQL;
  for I := 1 to K do D.ExecSQL;
  Names := ['a', 'b'];
  for S in Names do D.ExecSQL;
  for S in TDirectory.GetFiles(RepoRoot, '*.pas', TSearchOption.soAllDirectories) do D.ExecSQL;
  for S in TDirectory.GetFiles(Dir) do D.ExecSQL;
end;
end.
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (12, ("bounded", "caller_key")),
        (15, ("unknown", "query")),  # a capped read is as large as its cap
        (16, ("unknown", "parameter")),  # a data set the caller opened
        # The statement text is in a form file, or added line by line: unread.
        (18, ("unknown", "query")),
        (22, ("unknown", "query")),
        (24, ("unknown", "unknown")),  # an Open under a branch proves nothing
        (26, ("grows_with_data", "query")),  # Open('SELECT ...')
        (29, ("grows_with_data", "query")),  # repeat ... until Rep.Eof
        (30, ("unknown", "parameter")),
        (31, ("bounded", "constant")),
        (32, ("bounded", "constant")),  # the values of an enumerated type
        (33, ("bounded", "constant")),
        (34, ("unknown", "parameter")),  # Count - 1: the inclusive bound of L
        (35, ("unknown", "parameter")),  # downto: the size is the start value
        (37, ("bounded", "constant")),  # n and N are one name
        (40, ("unknown", "unknown")),  # stepped after its literal
        (41, ("unknown", "unknown")),  # a local never bound
        (43, ("bounded", "constant")),
        (44, ("grows_with_data", "listing")),
        (45, ("unknown", "listing")),
    ],
)
def test_pascal_data_sets_and_counted_loops(line, expected):
    assert _loops("Shapes.pas", "pascal", _PASCAL_SHAPES)[line] == expected


_PASCAL_CASE = """unit Names;
interface
uses FireDAC.Comp.Client;
implementation
procedure F(Q: TFDQuery; n: Integer; items: TStringList);
var I: Integer; E: TColor; S: string; total, tmp: Integer;
begin
  for I := 1 to N do Q.ExecSQL;
  for I := 0 to Items.Count - 1 do Q.ExecSQL;
  for S in ITEMS do Q.ExecSQL;
  for E in tcolor do Q.ExecSQL;
  for I := 1 to High(tmp) do Q.ExecSQL;
  for I := 1 to total do Q.ExecSQL;
end;
end.
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (8, ("unknown", "parameter")),  # N is the parameter n
        (9, ("unknown", "parameter")),
        (10, ("unknown", "parameter")),
        # A type is told from a variable by its spelling alone, so a lower-cased
        # one reads like ``tmp`` and ``total`` and bounds nothing.
        (11, ("unknown", "unknown")),
        (12, ("unknown", "unknown")),
        (13, ("unknown", "unknown")),
    ],
)
def test_pascal_names_match_without_case_but_a_type_needs_its_spelling(line, expected):
    assert _loops("Names.pas", "pascal", _PASCAL_CASE)[line] == expected


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
