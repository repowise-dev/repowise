"""Java anonymous-class and Kotlin object-expression methods, end to end.

Each member is a symbol named by the declarations holding its class and the
class's supertype (``Gson::FILTER::Filter::check``), so two anonymous classes
overriding one method keep two nodes, and the calls inside them resolve with
the method as caller. Dead code never flags them.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.lang_helpers.anonymous_classes import is_anonymous_member

_FILES = {
    "src/app/Filter.java": """\
package app;

public interface Filter {
    boolean check(Class<?> c);
}
""",
    "src/app/Gson.java": """\
package app;

import java.util.Comparator;

public class Gson {
    static final Filter FILTER = new Filter() {
        public boolean check(Class<?> c) {
            return helper();
        }

        boolean helper() {
            return true;
        }
    };

    static final Filter OTHER = new Filter() {
        public boolean check(Class<?> c) {
            return go();
        }
    };

    boolean go() {
        return true;
    }

    boolean helper() {
        return false;
    }

    Object make() {
        run(new Runnable() {
            public void run() {
                go();
            }
        });
        run(new Runnable() {
            public void run() {
                helper();
            }
        });
        Comparator<String> byLength = new java.util.Comparator<String>() {
            public int compare(String a, String b) {
                return a.length() - b.length();
            }
        };
        return byLength;
    }

    void run(Runnable r) {
        r.run();
    }

    enum Op {
        PLUS {
            int apply() {
                return new Object() {
                    int one() {
                        return 1;
                    }
                }.one();
            }
        };

        abstract int apply();
    }
}
""",
    "src/app/Alloc.java": """\
package app;

public abstract class Alloc {
    public abstract Object make();

    static Alloc create() {
        Store store = new Store();
        return new Alloc() {
            public Object make() {
                store.save();
                tidy();
                return null;
            }

            private void tidy() {}
        };
    }
}
""",
    "src/app/Store.java": """\
package app;

public class Store {
    void save() {}
}

class Disk {
    void save() {}
}
""",
    "src/app/User.java": """\
package app;

class User {
    static final Alloc INSTANCE = Alloc.create();

    Object use() {
        return User.INSTANCE.make();
    }
}
""",
    "src/app/Deck.kt": """\
package app

class Tape {
    fun play() {}
}

class Reel {
    fun play() {}
}

class Deck {
    fun watch(): Runnable {
        val tape = Tape()
        return object : Runnable {
            override fun run() {
                tape.play()
            }

            fun spin() {
                listOf(Reel()).forEach { tape -> tape.play() }
            }
        }
    }
}
""",
    "src/app/Shapes.java": """\
package app;

class Over {
    void over(int a) {
        new Runnable() {
            public void run() {
                ping();
            }
        }.run();
    }

    void over(String a) {
        new Runnable() {
            public void run() {}
        }.run();
    }

    void ping() {}
}

class Blocks {
    static { Runnable r = new Runnable() { public void run() {} }; }
    static { Runnable r = new Runnable() { public void run() {} }; }

    void m() {
        { Runnable q = new Runnable() { public void run() {} }; }
        { Runnable q = new Runnable() { public void run() {} }; }
    }
}

class Leak {
    void make2() {
        Runnable s = new Runnable() {
            public void run() {
                Bar y = null;
            }
        };
        Runnable u = new Runnable() {
            public void run() {
                y.miss();
            }
        };
    }
}

class Twice {
    Runnable outer(Bar b) {
        return new Runnable() {
            public void run() {
                new Runnable() {
                    public void run() {
                        b.miss();
                    }
                }.run();
            }
        };
    }
}

class Bar {
    void miss() {}
}

class Baz {
    void miss() {}
}
""",
    "src/app/Clock.kt": """\
package app

fun tick() {}

class Clock {
    fun start() {
        val r = object : Runnable {
            override fun run() {
                tick()
            }

            fun tick() {}
        }
    }

    class Leaf {
        fun leaf() {}
    }
}
""",
    "src/app/Screen.kt": """\
package app

class Screen {
    val listener = object : Callback<Int>(), Closeable {
        override fun onEvent() {
            refresh()
        }
    }

    fun start() {
        val task = object : Runnable {
            override fun run() {
                refresh()
            }
        }
        post(object {
            fun tick() {}
        })
    }

    fun refresh() {}

    fun post(any: Any) {}
}

fun untilReady(): Condition = object : Condition() {
    override fun apply() = true
}
""",
}


def _build(tmp_path: Path):
    for rel, text in _FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(tmp_path).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    graph = builder.graph()
    report = DeadCodeAnalyzer(
        graph, {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=tmp_path
    ).analyze({"detect_unused_internals": True})
    calls = {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"}
    return graph, calls, report


GSON = "src/app/Gson.java::"
SCREEN = "src/app/Screen.kt::"


def test_each_anonymous_method_is_its_own_node(tmp_path: Path) -> None:
    graph, _, _ = _build(tmp_path)
    lines = {
        "Gson::FILTER::Filter::check": 7,
        "Gson::FILTER::Filter::helper": 11,
        "Gson::OTHER::Filter::check": 17,
        "Gson::make::Runnable::run": 32,
        # A second same-typed class in one declaration takes javac's ordinal.
        "Gson::make::Runnable$2::run": 37,
        "Gson::make::byLength::Comparator::compare": 42,
        "Op::PLUS::apply": 55,
        "Op::PLUS::apply::Object::one": 57,
    }
    for sym, line in lines.items():
        assert graph.nodes[GSON + sym]["start_line"] == line, sym
        assert graph.nodes[GSON + sym]["kind"] == "method"
    assert graph.nodes[SCREEN + "Screen::listener::Callback::onEvent"]["start_line"] == 5
    assert graph.nodes[SCREEN + "Screen::start::task::Runnable::run"]["start_line"] == 12
    assert graph.nodes[SCREEN + "Screen::start::object::tick"]["start_line"] == 17
    # A top-level function's object expression is qualified by the function.
    assert graph.nodes[SCREEN + "untilReady::Condition::apply"]["start_line"] == 27
    assert SCREEN + "apply" not in graph


def test_calls_resolve_from_the_anonymous_method(tmp_path: Path) -> None:
    _, calls, _ = _build(tmp_path)
    # Its own member first, then the enclosing class's.
    assert (GSON + "Gson::FILTER::Filter::check", GSON + "Gson::FILTER::Filter::helper") in calls
    assert (GSON + "Gson::OTHER::Filter::check", GSON + "Gson::go") in calls
    assert (GSON + "Gson::make::Runnable::run", GSON + "Gson::go") in calls
    assert (GSON + "Gson::make::Runnable$2::run", GSON + "Gson::helper") in calls
    assert (GSON + "Gson::make", GSON + "Gson::go") not in calls
    assert (SCREEN + "Screen::listener::Callback::onEvent", SCREEN + "Screen::refresh") in calls
    assert (SCREEN + "Screen::start::task::Runnable::run", SCREEN + "Screen::refresh") in calls
    assert (SCREEN + "Screen::start", SCREEN + "Screen::refresh") not in calls


def test_an_anonymous_member_is_named_only_from_its_own_body(tmp_path: Path) -> None:
    _, calls, _ = _build(tmp_path)
    alloc = "src/app/Alloc.java::Alloc::"
    # A local of the method around the class types its receiver.
    assert (alloc + "create::Alloc::make", "src/app/Store.java::Store::save") in calls
    assert (alloc + "create::Alloc::make", alloc + "create::Alloc::tidy") in calls
    # A bare name from another file never reaches the anonymous override.
    assert ("src/app/User.java::User::use", alloc + "create::Alloc::make") not in calls


def test_a_name_the_anonymous_body_binds_is_not_the_captured_one(tmp_path: Path) -> None:
    _, calls, _ = _build(tmp_path)
    deck = "src/app/Deck.kt::Deck::watch::Runnable::"
    assert (deck + "run", "src/app/Deck.kt::Tape::play") in calls
    # The lambda's own ``tape`` shadows the captured local.
    assert (deck + "spin", "src/app/Deck.kt::Tape::play") not in calls


def test_overloads_and_sibling_blocks_keep_their_anonymous_classes_apart(tmp_path: Path) -> None:
    graph, calls, _ = _build(tmp_path)
    shapes = "src/app/Shapes.java::"
    # The host's overload discriminator is carried into the holder segment.
    for host in ("Over::over#1(int)", "Over::over#1(String)"):
        assert graph.nodes[shapes + host + "::Runnable::run"]["kind"] == "method"
    assert (shapes + "Over::over#1(int)::Runnable::run", shapes + "Over::ping") in calls
    # One name held twice in one scope, in sibling blocks or static blocks.
    for holder in ("Blocks::r", "Blocks::m::q"):
        assert shapes + holder + "::Runnable::run" in graph
        assert shapes + holder + "::Runnable$2::run" in graph


def test_a_sibling_anonymous_body_does_not_type_a_capture(tmp_path: Path) -> None:
    _, calls, _ = _build(tmp_path)
    shapes = "src/app/Shapes.java::"
    assert (shapes + "Leak::make2::u::Runnable::run", shapes + "Bar::miss") not in calls
    # A capture two anonymous classes deep is typed by the outermost function.
    inner = shapes + "Twice::outer::Runnable::run::Runnable::run"
    assert (inner, shapes + "Bar::miss") in calls


def test_an_anonymous_member_outranks_a_top_level_function(tmp_path: Path) -> None:
    _, calls, _ = _build(tmp_path)
    clock = "src/app/Clock.kt::"
    run = clock + "Clock::start::r::Runnable::run"
    assert (run, clock + "Clock::start::r::Runnable::tick") in calls
    assert (run, clock + "tick") not in calls
    # A named nested class keeps its bare parent, so it is no anonymous member.
    parsed = {
        fi.path: ASTParser().parse_file(fi, Path(fi.abs_path).read_bytes())
        for fi in FileTraverser(tmp_path).traverse()
    }
    leaf = next(s for s in parsed["src/app/Clock.kt"].symbols if s.name == "leaf")
    assert leaf.parent_name == "Leaf" and not is_anonymous_member(leaf)


def test_dead_code_never_flags_the_new_methods(tmp_path: Path) -> None:
    _, _, report = _build(tmp_path)
    flagged = {f.symbol_name for f in report.findings if f.symbol_name}
    assert not flagged & {"check", "helper", "run", "compare", "one", "onEvent", "tick"}
