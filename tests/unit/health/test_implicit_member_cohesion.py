"""LCOM4 over implicit receivers: Java, C#, C++ and Kotlin reach their own
fields and sibling methods without ``this.``.

Before this, only ``this.x`` counted, so a class mixing ``this.x = x`` in its
constructor with bare reads elsewhere splintered into one component per
method and read as low cohesion.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file


def _classes(language: str, source: str, path: str = "Sample") -> dict:
    fcx = walk_file(path, language, source.encode())
    if not fcx.classes:
        pytest.skip(f"tree-sitter grammar missing for {language}")
    return {c.name: c for c in fcx.classes}


JAVA = """
class ShardId {
    private final Index index;
    private final int shardId;
    private static final String NAME = "shard";

    ShardId(Index index, int shardId) { this.index = index; this.shardId = shardId; }
    Index getIndex() { return index; }
    String getIndexName() { return index.getName(); }
    int id() { return this.shardId; }
    int getId() { return id(); }
    String name() { return NAME; }
    boolean sameAs(ShardId other) { return other.shardId == shardId; }
}

class Splintered {
    int a1;
    int a2;
    int b1;
    int b2;
    void setA(int v) { a1 = v; a2 = v; }
    int getA() { return a1 + a2; }
    void setB(int v) { b1 = v; b2 = v; }
    int getB() { return b1 + b2; }
    int loner(int a1) { return a1 + 1; }
    boolean isB() { return b1 > 0; }
}
"""


def test_java_bare_field_reads_and_sibling_calls_link_methods():
    classes = _classes("java", JAVA, "ShardId.java")
    shard = classes["ShardId"]
    # Every method reaches ``index`` or ``shardId``. ``name()`` reads only a
    # static constant: it touches no visible member, so it is left out rather
    # than counted as a component of its own.
    assert shard.lcom4 == 1
    assert shard.field_count == 2
    assert [set(g.methods) for g in shard.components] == [
        {"ShardId", "getIndex", "getIndexName", "id", "getId", "sameAs"}
    ]


def test_java_parameter_shadows_field_and_splinters_stay_found():
    splintered = _classes("java", JAVA, "Splintered.java")["Splintered"]
    # Two real field clusters. ``loner(int a1)`` reads its parameter, not the
    # field, so it touches no member and is left out.
    assert splintered.lcom4 == 2
    assert splintered.field_count == 4
    assert [set(g.methods) for g in splintered.components] == [
        {"setA", "getA"},
        {"setB", "getB", "isB"},
    ]


def test_accessors_over_one_field_are_not_a_responsibility():
    source = """
class Request {
    private String id;
    private String name;
    private boolean refresh;
    Request(String id, String name) { this.id = id; this.name = name; }
    String describe() { return id + name; }
    void setRefresh(boolean r) { refresh = r; }
    boolean getRefresh() { return refresh; }
}
"""
    request = _classes("java", source, "Request.java")["Request"]
    assert request.lcom4 == 1
    assert [set(g.fields) for g in request.components] == [{"id", "name"}]


CSHARP = """
class Settings
{
    private int _count;
    public string Key { get; set; }
    public const int Max = 3;

    public void Add(int n) { _count += n; }
    public int Count() { return _count; }
    public string Describe() { return Key + Count(); }
    public void Rename(string key) { Key = key; }
    public bool Same(Settings other) { return other.Key == Key; }
}

public sealed partial class MainWindow : Window
{
    private int _a;
    private int _b;
    public void A1() { _a = 1; }
    public void A2() { _a = 2; }
    public void B1() { _b = 1; }
    public void B2() { _b = 2; }
    public void Loner() { }
}
"""


def test_csharp_bare_fields_and_properties_link_methods():
    settings = _classes("csharp", CSHARP, "Settings.cs")["Settings"]
    assert settings.lcom4 == 1
    assert settings.field_count == 2  # _count and Key; the const is not state


def test_csharp_partial_class_has_no_cohesion_signal():
    window = _classes("csharp", CSHARP, "MainWindow.xaml.cs")["MainWindow"]
    assert window.method_count == 5
    assert window.lcom4 == 1
    assert window.components == []


CPP = """
class Module : public Iface {
    bool m_enabled;
    std::wstring* m_name;
    static int s_count;
    void refresh();

public:
    void enable() { m_enabled = true; refresh(); }
    void disable() { m_enabled = false; }
    bool is_enabled() { return m_enabled; }
    const wchar_t* name() { return m_name->c_str(); }
    void rename(std::wstring* m_name_) { m_name = m_name_; refresh(); }
    void copy(Module& other) { other.m_enabled = true; }
};
"""


def test_cpp_bare_member_access_and_out_of_line_methods():
    module = _classes("cpp", CPP, "dllmain.cpp")["Module"]
    # ``refresh`` is declared here and defined elsewhere: a shared call, not a field.
    assert module.field_count == 2
    # ``other.m_enabled`` is another instance's field, so ``copy`` touches
    # none of ours and is left out.
    assert [set(g.methods) for g in module.components] == [
        {"enable", "disable", "is_enabled", "name", "rename"}
    ]
    assert module.lcom4 == 1


KOTLIN = """
class Counter(val start: Int, step: Int) {
    var total = 0

    fun add(n: Int) { total += n }
    fun reset() { total = start }
    fun describe(): String { return total.toString() }
    fun copy(other: Counter) { other.total = 1 }
    fun loner(total: Int): Int { return total }
}
"""


def test_kotlin_bare_properties_and_constructor_vals():
    counter = _classes("kotlin", KOTLIN, "Counter.kt")["Counter"]
    # ``copy`` writes another instance and ``loner`` reads its parameter.
    assert [set(g.methods) for g in counter.components] == [{"add", "reset", "describe"}]
    assert counter.field_count == 2  # total and start; plain ``step`` is not a property


def test_python_still_requires_self():
    source = """
class C:
    def __init__(self):
        self.a = 1
    def bare(self):
        return a
    def own(self):
        return self.a
"""
    c = _classes("python", source, "c.py")["C"]
    assert c.lcom4 == 2
    assert [set(g.methods) for g in c.components] == [{"__init__", "own"}, {"bare"}]


STATELESS_AND_CONTRACTS = """
class Retry {
    static Policy retry(int n) { return retry(n, null); }
    static Policy retry(int n, Handler h) { return build(n, h); }
    static Policy forever() { return build(-1, null); }
    private static Policy build(int n, Handler h) { return new Policy(n, h); }
    static Policy never() { return null; }
}

class Route {
    private String upstream;
    private String downstream;
    private int timeout;
    Route(String up, String down, int t) { upstream = up; downstream = down; timeout = t; }
    Route copy() { return new Route(upstream, downstream, timeout); }
    int timeoutOr(int d) { return timeout > 0 ? timeout : d; }
    String key() { return upstream + downstream; }
    private String label;
    private String tag;
    @Override public String toString() { return label + tag; }
}
"""


def test_java_stateless_class_has_no_cohesion_signal():
    retry = _classes("java", STATELESS_AND_CONTRACTS, "Retry.java")["Retry"]
    assert retry.method_count == 5
    assert retry.lcom4 == 1
    assert retry.components == []


def test_java_override_only_component_is_not_a_responsibility():
    route = _classes("java", STATELESS_AND_CONTRACTS, "Route.java")["Route"]
    # ``toString`` alone reads ``label`` and ``tag``; it is a contract, so it
    # does not count as a second responsibility.
    assert route.lcom4 == 1
    assert [set(g.fields) for g in route.components] == [{"upstream", "downstream", "timeout"}]


def test_csharp_overloads_are_one_node_and_lone_accessors_do_not_count():
    source = """
class Logger
{
    private ILogger _logger;
    private IScope _scope;
    private bool _disposed;

    public void LogTrace(string message) { LogTrace(() => message); }
    public void LogTrace(Func<string> message) { Write(_logger, _scope, message); }
    public void LogDebug(string message) { Write(_logger, _scope, () => message); }
    private void Write(ILogger l, IScope s, Func<string> m) { }
    public void Dispose() { _disposed = true; }
    public override string ToString() { return _scope.Name + _logger.Name; }
}
"""
    logger = _classes("csharp", source, "Logger.cs")["Logger"]
    # ``Dispose`` alone over ``_disposed`` is an accessor, ``ToString`` an
    # override; the rest is one component through ``_logger`` / ``_scope``.
    assert logger.lcom4 == 1


def test_java_nested_types_own_their_members_and_inner_classes_link_outer_state():
    source = """
class Cache {
    private final Counter hits = new Counter();
    private final Counter misses = new Counter();
    private final Map entries;
    private final Lock lock;

    Cache(Map entries, Lock lock) { this.entries = entries; this.lock = lock; }
    Object get(Object k) { return segment().get(k); }
    void put(Object k, Object v) { lock.lock(); entries.put(k, v); }
    void clear() { lock.lock(); entries.clear(); }
    Stats stats() { return new Stats(hits.sum(), misses.sum()); }
    Segment segment() { return new Segment(); }

    class Segment {
        Object get(Object k) {
            Object v = entries.get(k);
            if (v == null) { misses.increment(); } else { hits.increment(); }
            return v;
        }
    }

    enum Mode {
        FAST, SAFE;
        private int weight;
        int weight() { return weight; }
        Mode heavier() { weight++; return this; }
    }
}
"""
    cache = _classes("java", source, "Cache.java")["Cache"]
    # ``Mode``'s methods are not the cache's; ``Segment`` reads the counters
    # and the entries together, so ``stats`` is not a split of its own.
    assert "weight" not in {m.name for m in cache.methods}
    assert cache.lcom4 == 1


def test_csharp_static_factory_initializers_are_not_instance_state():
    source = """
public class PasteFormat
{
    public string Name { get; init; }
    public string Prompt { get; init; }
    public bool IsSaved { get; init; }
    public int Format { get; init; }
    public bool IsEnabled { get; set; }

    public PasteFormat(int format, bool enabled) { Format = format; IsEnabled = enabled; }
    public bool Supports(int f) { return IsEnabled && Format == f; }
    public bool Supports(string f) { return IsEnabled && Format.ToString() == f; }
    public static PasteFormat Standard(int f) => new PasteFormat(f, true) { Name = "std" };
    public static PasteFormat Custom(string p) => new PasteFormat(0, true) { Name = "ai", Prompt = p, IsSaved = true };
}
"""
    fmt = _classes("csharp", source, "PasteFormat.cs")["PasteFormat"]
    assert fmt.lcom4 == 1


def test_constructor_only_component_is_not_a_split():
    # Fields the constructor sets and only a property (C#) or an out-of-line
    # method (C++) reads leave the constructor alone in its component.
    csharp = """
class Totals
{
    private readonly int a;
    private readonly int b;
    private int c;
    private int d;
    public int A => a + b;
    public Totals(int x) { a = x; b = x; }
    public void M1() { c = 1; d = 1; }
    public void M2() { c = 2; d = 2; }
    public int M3() { return c + d; }
    public int M4() { return c - d; }
}
"""
    assert _classes("csharp", csharp, "Totals.cs")["Totals"].lcom4 == 1
    cpp = """
class Totals {
    int a;
    int b;
    int c;
    int d;
public:
    Totals(int p) : a(p), b(p) {}
    void m1() { c = 1; d = 1; }
    void m2() { c = 2; d = 2; }
    int m3() { return c + d; }
    int m4() { return c - d; }
    int sum();
};
"""
    assert _classes("cpp", cpp, "totals.h")["Totals"].lcom4 == 1


def test_pattern_and_scoped_names_shadow_or_skip_members():
    java = """
class Box {
    private int a;
    private int b;
    Box(int x) { a = x; b = x; }
    int f(Object o) { return switch (o) { case Integer a -> a; default -> 0; }; }
}
"""
    box = _classes("java", java, "Box.java")["Box"]
    f = next(g for g in box.components if "f" in g.methods) if box.components else None
    assert f is None or "a" not in f.fields
    csharp = """
class Pair
{
    private int a;
    private int q;
    public void Read((int, int) t) { var (a, q) = t; }
    public void Set() { a = 1; q = 2; }
}
"""
    pair = _classes("csharp", csharp, "Pair.cs")["Pair"]
    assert all("Read" not in g.methods for g in pair.components)
    cpp = """
class Node {
    int a;
    int b;
    void reset() { Other::a = 1; Other::b = 2; }
    void set() { a = 1; b = 2; }
};
"""
    node = _classes("cpp", cpp, "node.h")["Node"]
    assert all("reset" not in g.methods for g in node.components)


def test_cohesion_does_not_apply_to_test_files_of_implicit_languages():
    from repowise.core.analysis.health.complexity.class_analysis import cohesion_applies

    assert not cohesion_applies("src/test/java/org/x/DateLiteralTest.java", "java")
    assert not cohesion_applies("tests/Unit/LoggerTests.cs", "csharp")
    assert cohesion_applies("src/main/java/org/x/Date.java", "java")
    # Python keeps its old behaviour, test files included.
    assert cohesion_applies("tests/test_thing.py", "python")


def test_cpp_module_interface_overrides_and_constructor_are_not_a_split():
    source = """
class Module : public PowertoyModuleIface {
    std::wstring app_name;
    std::wstring app_key;
    bool m_enabled = false;
    PROCESS_INFORMATION p_info = {};
    bool is_process_running() { return WaitForSingleObject(p_info.hProcess, 0) == 0; }
    void launch_process() { CreateProcess(&p_info); }
public:
    Module() { app_name = L"x"; app_key = L"y"; }
    virtual const wchar_t* get_key() override { return app_key.c_str(); }
    virtual void set_config(const wchar_t* c) override { app_name = c; }
    virtual void enable() { m_enabled = true; launch_process(); }
    virtual void disable() { if (m_enabled) { TerminateProcess(p_info.hProcess, 1); } m_enabled = false; }
    virtual bool is_enabled() override { return m_enabled; }
};
"""
    assert _classes("cpp", source, "dllmain.cpp")["Module"].lcom4 == 1


RUST = """
struct Builder { a: u8, b: u8, c: u8, d: u8 }

impl Builder {
    pub fn new() -> Builder { Builder { a: 0, b: 0, c: 0, d: 0 } }
    pub fn a(&mut self, v: u8) -> &mut Self { self.a = v; self }
    pub fn b(&mut self, v: u8) -> &mut Self { self.b = v; self }
    pub fn c(&mut self, v: u8) -> &mut Self { self.c = v; self }
    pub fn d(&mut self, v: u8) -> &mut Self { self.d = v; self }
    pub fn get_a(&self) -> u8 { self.a }
}

enum Kind { One, Two }

impl Kind {
    fn is_one(&self) -> bool { matches!(self, Kind::One) }
    fn is_two(&self) -> bool { matches!(self, Kind::Two) }
    fn name(&self) -> &str { match self { Kind::One => "one", Kind::Two => "two" } }
    fn code(&self) -> u8 { match *self { Kind::One => 1, Kind::Two => 2 } }
    fn default() -> Kind { Kind::One }
}

struct Split { a1: u8, a2: u8, b1: u8, b2: u8 }

impl Split {
    fn new() -> Split { Split { a1: 0, a2: 0, b1: 0, b2: 0 } }
    fn set_a(&mut self, v: u8) { self.a1 = v; self.a2 = v; }
    fn sum_a(&self) -> u8 { self.a1 + self.a2 }
    fn set_b(&mut self, v: u8) { self.b1 = v; self.b2 = v; }
    fn sum_b(&self) -> u8 { self.b1 + self.b2 }
}
"""


def test_rust_builders_accessors_and_enums_are_not_low_cohesion():
    classes = _classes("rust", RUST, "lib.rs")
    assert classes["Builder"].lcom4 == 1
    assert classes["Kind"].lcom4 == 1


def test_rust_split_over_two_field_groups_stays_found():
    split = _classes("rust", RUST, "lib.rs")["Split"]
    assert split.lcom4 == 2
    assert [set(g.fields) for g in split.components] == [{"a1", "a2"}, {"b1", "b2"}]


def test_rust_calls_to_methods_outside_the_impl_are_not_fields():
    source = """
struct S { a: u8, b: u8 }
impl S {
    fn x1(&self) { self.helper(); self.a; }
    fn x2(&self) { self.a; }
    fn y1(&self) { self.clone(); self.b; }
    fn y2(&self) { self.b; }
    fn y3(&self) { self.b; }
}
"""
    s = _classes("rust", source, "lib.rs")["S"]
    assert s.lcom4 == 1


def test_rust_constructor_like_method_is_an_ordinary_component():
    # Rust has no constructor or override exemption: ``reset`` touching two
    # fields is a responsibility like any other method.
    source = """
struct R { a1: u8, a2: u8, b1: u8, b2: u8 }
impl R {
    fn reset(&mut self) { self.a1 = 0; self.a2 = 0; }
    fn sum_a(&self) -> u8 { self.a1 + self.a2 }
    fn set_b(&mut self, v: u8) { self.b1 = v; self.b2 = v; }
    fn sum_b(&self) -> u8 { self.b1 + self.b2 }
    fn new() -> R { R { a1: 0, a2: 0, b1: 0, b2: 0 } }
}
"""
    r = _classes("rust", source, "lib.rs")["R"]
    assert r.lcom4 == 2
    assert [set(g.methods) for g in r.components] == [{"reset", "sum_a"}, {"set_b", "sum_b"}]
