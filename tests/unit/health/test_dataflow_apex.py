"""Apex dataflow: def/use, CFG and Extract Method, in the Java tests' pattern.

Apex shares Java's statement grammar, so the Java dialect is reused; these
tests pin what Apex adds: case-insensitive names, inline SOQL binds, SObject
field pairs, ``switch on ... when`` arms, ``System.runAs`` blocks, static
methods whose bare names are static fields, and the helper written as Apex.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import (
    analyze_file,
    find_extractions,
    get_defuse_dialect,
)
from repowise.core.analysis.health.refactoring import extract_method as em
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.models import RefactoringContext


def _analyses(src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language("apex") is None:
        pytest.skip("tree-sitter language pack missing for apex")
    res = analyze_file("m.cls", "apex", textwrap.dedent(src).encode(), flagged_only=False)
    return res.functions


def _first(src: str):
    fns = _analyses(src)
    assert fns, "no function analysed"
    return fns[0]


def _defs(fn) -> set[str]:
    return {d.var for d in fn.def_use.definitions}


def _uses(fn) -> set[str]:
    return {u.name for b in fn.def_use.blocks.values() for u in b.uses if not u.echo}


def _extractions(fn):
    lmap = get_language_map("apex")
    return find_extractions(fn, lmap, get_defuse_dialect("apex").receiver(fn.fn_node, lmap))


def _line(src: str, marker: str) -> int:
    return next(i for i, ln in enumerate(textwrap.dedent(src).splitlines(), 1) if marker in ln)


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _plans(src: str):
    fns = _analyses(src)
    ctx = RefactoringContext(
        file_path="m.cls",
        language="apex",
        nloc=100,
        findings=[_Finding("complex_method", f.name, f.start_line, 1.5) for f in fns],
        function_analyses=fns,
    )
    return {s.target_symbol: s.plan for s in ExtractMethodDetector().detect(ctx)}


_SHAPES = """
public class Demo {
    Integer shapes(List<Account> accts, Integer base, String nm) {
        Integer total = 0, a = 1;
        total += base;
        a++;
        this.count = total;
        accts[0] = null;
        for (Integer i = 0; i < base; i++) { total += i; }
        for (Account acc : [SELECT Id FROM Account WHERE Name = :nm LIMIT :base]) {
            insert new Contact(LastName = acc.Name, AccountId = acc.Id);
        }
        try {
            update accts;
        } catch (DmlException e) {
            total = e.getNumDml();
        } finally {
            a = 0;
        }
        return total + a;
    }
}
"""


def test_apex_def_use_classification():
    fn = _first(_SHAPES)
    defs = _defs(fn)
    assert {"total", "a", "i", "acc", "e", "accts", "base", "nm"} <= defs
    # Field and list targets, and SObject constructor pairs, bind no local.
    assert not {"count", "LastName", "AccountId"} & defs
    uses = _uses(fn)
    # Only the ``:nm`` / ``:base`` binds of the query are variable reads.
    assert {"nm", "base", "acc", "accts", "e", "total", "a"} <= uses
    assert not {"Id", "Name", "Account", "LastName", "getNumDml"} & uses


def test_apex_names_fold_to_the_declared_spelling():
    fn = _first(
        """
        public class Demo {
            void f(Integer n) {
                List<Account> toInsert = new List<Account>();
                TOINSERT.add(new Account());
                insert toinsert;
                N = n + 1;
            }
        }
        """
    )
    assert {"toInsert", "n"} <= _defs(fn)
    assert not {"TOINSERT", "toinsert", "N"} & (_defs(fn) | _uses(fn))


def test_apex_two_spellings_of_one_name_fold_to_lowercase():
    fn = _first(
        """
        public class Demo {
            void f(Boolean b) {
                if (b) { Integer Acc = 1; System.debug(acc); }
                else { Integer ACC = 2; System.debug(Acc); }
            }
        }
        """
    )
    assert "acc" in _defs(fn) and "acc" in _uses(fn)
    assert not {"Acc", "ACC"} & (_defs(fn) | _uses(fn))


def test_apex_control_flow_builds_branches_loops_and_handlers():
    fn = _first(
        """
        public class Demo {
            Integer f(Integer x, List<Integer> xs) {
                Integer y = 0;
                if (x == 1) { y = 1; } else if (x == 2) { y = 2; } else { y = 3; }
                for (Integer v : xs) { y += v; }
                while (y > 100) { y -= 10; }
                do { y++; } while (y < 5);
                try { y = 1 / x; } catch (MathException e) { y = 0; } finally { x = 0; }
                return y;
            }
        }
        """
    )
    kinds = [b.kind for b in fn.cfg.blocks]
    assert kinds.count("branch") == 2
    assert kinds.count("loop_header") == 3
    assert kinds.count("handler") == 1
    assert len(fn.cfg.back_edges()) == 3
    assert fn.cfg.exit_id in fn.cfg.reachable_ids()


def test_apex_run_as_block_is_built_and_its_user_read():
    src = """
    public class Demo {
        static void f(User u, Integer n) {
            Integer x = 0;
            System.runAs(u) {
                if (n > 0) { return; }
                x = 1;
            }
            System.debug(x);
        }
    }
    """
    fn = _first(src)
    assert "u" in _uses(fn)
    # The ``return`` inside the block is a real edge to the exit.
    block = next(
        b for b in fn.cfg.blocks if any(s.kind == "return_statement" for s in b.statements)
    )
    assert fn.cfg.exit_id in block.successors


def test_apex_when_binds_its_arm_variable_and_reads_no_label():
    fn = _first(
        """
        public class Demo {
            Integer f(SObject rec, Season s) {
                Integer y = 0;
                switch on rec {
                    when Account acct { y = acct.NumberOfEmployees; }
                    when null { y = -1; }
                    when else { y = 2; }
                }
                switch on s {
                    when WINTER, SUMMER { y++; }
                }
                return y;
            }
        }
        """
    )
    acct = next(d for d in fn.def_use.definitions if d.var == "acct")
    assert acct.declares and not acct.may
    assert not {"WINTER", "SUMMER"} & _uses(fn)
    # Arm writes are may-defs, as in Java.
    assert any(d.var == "y" and d.may for d in fn.def_use.definitions)


def test_apex_when_binder_has_its_declared_type():
    fn = _first(
        """
        public class Demo {
            void f(SObject rec) {
                switch on rec { when Account acct { System.debug(acct); } }
            }
        }
        """
    )
    d = next(d for d in fn.def_use.definitions if d.var == "acct")
    row = d.line - 1
    node = fn.fn_node.descendant_for_point_range((row, d.column), (row, d.column + len(d.var)))
    assert get_defuse_dialect("apex").declared_types.of(node) == "Account"


# A long method whose compute-average tail is a clean extraction.
_PROCESS = """
public class Demo {
    private static Integer runs = 0;
    {static}Integer process(List<Integer> records, Integer threshold) {
        Integer errors = 0;
        List<Integer> results = new List<Integer>();
        for (Integer r : records) {
            if (r < 0) {
                errors++;
                continue;
            }
            results.add(r);
        }
        runs = runs + 1;
        Integer total = 0;
        Integer count = 0;
        for (Integer v : results) {
            if (v > threshold) {
                total += v;
                COUNT++;
            } else {
                total -= v;
            }
        }
        Integer average = 0;
        if (count > 0) {
            average = total / count;
        }
        return average + errors;
    }
}
"""


@pytest.mark.parametrize("static", ["", "static "])
def test_apex_extract_method_fires(static):
    best = _extractions(_first(_PROCESS.replace("{static}", static)))[0]
    assert best.returns == ("average",)
    # ``runs`` is a static field written by bare name: never a parameter.
    assert best.params == ("results", "threshold")
    assert best.ccn_removed >= 1


def test_apex_extractions_are_deterministic():
    fn = _first(_PROCESS.replace("{static}", ""))

    def serialize():
        return [(e.start_line, e.end_line, e.params, e.returns) for e in _extractions(fn)]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


def test_apex_static_host_gets_a_static_helper():
    plan = _plans(_PROCESS.replace("{static}", "public static "))["process"]
    sym = plan["new_symbol"]
    assert sym["kind"] == "function"
    assert sym["signature_text"] == (
        "private static Integer extractedHelper(List<Integer> results, Integer threshold) {"
    )
    assert plan["call_site"]["new_text"] == "Integer average = extractedHelper(results, threshold);"
    assert sym["return_text"] == "return average;"


def test_apex_instance_host_gets_an_instance_helper():
    plan = _plans(_PROCESS.replace("{static}", "public "))["process"]
    assert plan["new_symbol"]["kind"] == "method"
    assert plan["new_symbol"]["signature_text"].startswith("private Integer extractedHelper(")


def test_apex_unknown_output_type_is_a_placeholder_not_var():
    from repowise.core.analysis.health.refactoring.render import HelperShape, Slot, render

    shape = HelperShape(
        language="apex",
        name="loadTotals",
        kind="function",
        is_async=False,
        params=(Slot("items", "List<Account>"),),
        returns=(Slot("total", None),),
        out_declared=True,
    )
    assert render(shape)[:2] == (
        "private static <type> loadTotals(List<Account> items) {",
        "<type> total = loadTotals(items);",
    )


_JUMPS = """
public class Demo {
    Integer f(List<Integer> xs, Integer limit) {
        Integer total = 0;
        for (Integer x : xs) {
            if (x > limit) {
                total += x;
                total += 1;
            }
            switch on x {
                when 0 { {jump} }
                when else { total -= 1; }
            }
            total = total * 2;
            total = total + 1;
        }
        return total;
    }
}
"""


@pytest.mark.parametrize("jump", ["break;", "continue;", "return total;"])
def test_apex_extraction_never_spans_a_jump(jump):
    src = _JUMPS.replace("{jump}", jump)
    fn = _first(src)
    arm = _line(src, jump)
    assert all(not e.start_line <= arm <= e.end_line for e in _extractions(fn))


# ``best`` is taken in and conditionally rewritten by the span over the ``if``
# pair; the rewrite below the switch hides that write from the CFG.
_ARM_EXIT = """
public class Demo {
    Integer f(List<Integer> xs, Integer best) {
        for (Integer x : xs) {
            if (x > 5) {
                best = x;
            }
            if (best > 100) {
                System.debug(best);
            }
            switch on x {
                when 0 { {jump} }
            }
            best = 0;
        }
        return best;
    }
}
"""


@pytest.mark.parametrize("jump", ["break;", "continue;"])
def test_apex_arm_jump_keeps_the_state_before_the_switch_live(jump):
    # ``break`` / ``continue`` in a ``when`` arm leave the loop with ``best``
    # as the span left it, skipping ``best = 0``. A helper taking ``best`` must
    # hand it back, or the write is lost on that path.
    src = _ARM_EXIT.replace("{jump}", jump)
    fn = _first(src)
    start = _line(src, "if (x > 5)")
    offered = [e for e in _extractions(fn) if e.start_line == start]
    assert all("best" in e.returns for e in offered if "best" in e.params)


def test_apex_dynamic_query_reads_every_local_in_scope():
    # ``Database.query`` resolves ``:ids`` / ``:stage`` by name where it runs,
    # so a helper holding the call must take them, and a span declaring one
    # ahead of the call must hand it back.
    src = """
    public class Demo {
        List<SObject> f(Set<Id> ids, Boolean wide) {
            String stage = 'Open';
            if (wide) { Integer unused = 1; }
            String q = 'SELECT Id FROM Opportunity WHERE Id IN :ids AND StageName = :stage';
            for (Integer k = 0; k < 2; k++) {
                List<SObject> rows = Database.query(q);
            }
            return Database.query(q);
        }
    }
    """
    fn = _first(src)
    call = _line(src, "List<SObject> rows = Database.query(q);")
    read = {u.name for b in fn.def_use.blocks.values() for u in b.uses if u.line == call}
    assert {"ids", "wide", "stage", "q", "k"} <= read
    assert not {"unused", "rows"} & read


def test_apex_span_never_cuts_a_shared_line():
    src = """
    public class Demo {
        Integer f(List<Integer> xs, Integer limit) {
            Integer total = 0;
            for (Integer x : xs) {
                total += x;
            } // xs
            Integer extra = 0;
            if (limit > 0) {
                extra = limit * 2;
            } else {
                extra = 1;
            }
            Integer result = total + extra;
            System.debug(result);
            return result;
        }
    }
    """
    fn = _first(src)
    shared = _line(src, "} // xs")
    assert all(e.start_line != shared for e in _extractions(fn))


def test_output_declared_over_several_lines_needs_no_declare_note():
    from repowise.core.analysis.health.dataflow import Extraction
    from repowise.core.analysis.health.refactoring.extract_method import _undeclared_outputs_note

    src = """
    public class Demo {
        void f(List<Account> xs) {
            if (xs.isEmpty()) { Account a = null; System.debug(a); }
            Account a = new Account(
                Name = 'x'
            );
            System.debug(a);
        }
    }
    """
    fn = _first(src)
    start = _line(src, "Account a = new Account(")
    span = Extraction(start, start + 2, (), ("a",), slice_nloc=3, ccn_removed=1)
    assert _undeclared_outputs_note(fn, span) == []


def test_apex_constructor_pair_naming_a_local_is_a_may_def():
    fn = _first(
        """
        public class Demo {
            void f(Integer n) {
                Foo f = new Foo(n = 5);
                Account acc = new Account(Name = 'x');
                System.debug(n);
            }
        }
        """
    )
    n_defs = [d for d in fn.def_use.definitions if d.var == "n" and d.line > 3]
    assert n_defs and all(d.may for d in n_defs)
    assert "Name" not in _defs(fn)


def test_apex_dynamic_query_iterable_does_not_read_its_own_loop_variable():
    src = """
    public class Demo {
        void f(String q) {
            for (Account a : Database.query(q)) {
                System.debug(a);
            }
        }
    }
    """
    fn = _first(src)
    header = _line(src, "Database.query(q)")
    read = {u.name for b in fn.def_use.blocks.values() for u in b.uses if u.line == header}
    assert "q" in read and "a" not in read


def test_apex_trigger_plan_has_no_helper_text_and_property_is_not_sliced():
    from repowise.core.ingestion.parser import _get_language

    if _get_language("apex") is None:
        pytest.skip("tree-sitter language pack missing for apex")
    lines = ["trigger T on Account (before insert) {", "    Integer seen = 0;"]
    for k in range(6):
        lines += [f"    if (Trigger.new.size() > {k}) {{", f"        seen += {k};", "    }"]
    lines += ["    System.debug(seen);", "    System.debug(seen);", "}"]
    res = analyze_file("T.trigger", "apex", "\n".join(lines).encode(), flagged_only=False)
    (fn,) = res.functions
    assert get_defuse_dialect("apex").receiver(fn.fn_node, get_language_map("apex")) is None
    x = _extractions(fn)[0]
    symbol = em._symbol_fields(x, None)
    assert symbol["new_symbol"]["kind"] is None
    fields = em._render_fields(fn, x, get_defuse_dialect("apex"), "apex", "h", symbol, async_host=True)
    assert fields["new_symbol"]["signature_text"] is None and fields["call_site"] is None
    prop = _analyses(
        """
        public class Demo {
            public Integer total {
                get { if (cache == null) { cache = 0; } return cache; }
            }
        }
        """
    )
    assert all(not _extractions(f) for f in prop)


_COMPACT = """
public class Demo {
    Integer f(List<Integer> xs, Integer limit) {
        Integer total = 0;
        Integer extra = 0;
        for (Integer x : xs) {
            total += x;
        }
        if (limit > 0) { extra = limit * 2;
            if (extra > 9) {
                extra = 9;
            }
            total += extra;
            total += 1; }
        return total;
    }
}
"""


def test_apex_span_never_shares_a_line_with_a_compact_block_brace():
    # The ``if`` header opens the block on the first statement's line and the
    # closing brace sits on the last one's: lifting those lines takes them too.
    src = _COMPACT
    fn = _first(src)
    header, brace = _line(src, "if (limit > 0) {"), _line(src, "total += 1; }")
    assert all(e.start_line != header and e.end_line != brace for e in _extractions(fn))
