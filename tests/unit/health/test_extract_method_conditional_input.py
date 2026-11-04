"""A span that writes a name on one path and reads it afterwards takes the name in (#3523).

The input side of ``_infer_in_out`` went by line order: a write earlier in
the span covered a later read whether or not it ran. With the write under an
``if``, or in a loop whose body may not run, the read can still see the value
from before the span, and the helper was not given it.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions
from repowise.core.analysis.health.dataflow.stages import find_stages


def _spans(src: str, language: str = "python", ext: str = "py"):
    result = analyze_file(
        f"m.{ext}", language, textwrap.dedent(src).lstrip("\n").encode(), flagged_only=False
    )
    if result.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    fn = result.functions[0]
    return {(x.start_line, x.end_line): x for x in find_extractions(fn, get_language_map(language))}


_IF = """
def f(k, xs):
    best = xs[0]
    n = len(xs)
    log(n)
    if k == 1:
        best = {WRITE}
    else:
        log(1)
    log(2)
    if best > 3:
        log(best)
    else:
        log(0)
    done()
    return n
"""


def test_a_write_under_an_if_does_not_cover_the_read_after_it():
    spans = _spans(_IF.replace("{WRITE}", "7"))
    assert (5, 13) in spans and (5, 14) in spans
    for key in ((5, 13), (5, 14)):
        assert "best" in spans[key].params, (key, spans[key].params)


def test_a_write_on_every_path_still_covers_the_read():
    """The control: both arms assign, so the value from before is never read."""
    src = _IF.replace("{WRITE}", "7").replace("log(1)", "best = 0")
    spans = _spans(src)
    assert (5, 13) in spans
    assert "best" not in spans[(5, 13)].params


def test_a_span_after_the_conditional_write_takes_it_as_before():
    spans = _spans(_IF.replace("{WRITE}", "7"))
    assert spans[(9, 13)].params == ("best",)


_LOOP = """
def route(rel_path, matches):
    if not matches:
        return None
    end = 0
    for m in matches:
        end = m.end()
    parts = rel_path[end:].split("/")
    if len(parts) > 1 and parts[-1] == "route":
        parts.pop()
    raw = []
    for part in parts:
        raw.extend(split(part))
    return "/" + "/".join(raw)
"""


def test_a_write_in_a_loop_that_may_not_run_does_not_cover_the_read_after_it():
    spans = _spans(_LOOP)
    holding = [x for (s, e), x in spans.items() if s == 5 and e >= 7]
    assert holding, "expected a span from the loop on"
    for x in holding:
        assert "end" in x.params, (x.start_line, x.end_line, x.params)


_JAVA = """
class C {
    Response challenge(Context context, String error, String field) {
        Form form = context.form();
        Session session = context.session();
        form.setExecution(context.id());
        audit(session);
        trace(context);
        if (session.hidden()) {
            field = PASSWORD;
        }
        if (error != null) {
            if (field != null) {
                form.addError(field, error);
            } else {
                form.setError(error);
            }
        }
        note(form);
        flush(session);
        return create(form);
    }
}
"""


def test_a_reassigned_parameter_is_passed_on_java():
    spans = _spans(_JAVA, "java", "java")
    holding = [x for (s, e), x in spans.items() if s <= 8 and e >= 16]
    assert holding, "expected a span holding both ifs"
    for x in holding:
        assert "field" in x.params, (x.start_line, x.end_line, x.params)


# -- what must stay as it was ---------------------------------------------------------


def test_a_read_before_the_span_does_not_make_an_input():
    """``best`` is read above the span and written in it before every read
    there: the span does not need the value from before."""
    src = """
    def f(k, xs):
        best = xs[0]
        log(best)
        n = len(xs)
        best = n * 2
        if k == 1:
            log(best)
        else:
            log(n)
        log(k)
        done()
        return n
    """
    spans = _spans(src)
    holding = [x for (s, e), x in spans.items() if s == 5]
    assert holding, "expected a span from the write on"
    assert all("best" not in x.params for x in holding)


def test_a_read_after_the_span_does_not_make_an_input():
    """The span sits in one branch and writes ``best`` before it reads it.
    The read below the ``if`` sees that write or the one from before, but it is
    not the span's read: the span hands ``best`` back and takes nothing."""
    src = """
    def f(k, xs):
        best = xs[0]
        n = len(xs)
        if k:
            best = n * 2
            if n > 3:
                log(best)
            else:
                log(n)
            log(k)
        use(best)
        done()
        return n
    """
    spans = _spans(src)
    assert (5, 10) in spans, sorted(spans)
    assert "best" not in spans[(5, 10)].params
    assert spans[(5, 10)].returns == ("best",)


_GO_IF_INIT = """
package m

func f(c *C, t *T) error {
    err := c.start()
    t.note(err)
    t.mark()
    if err := c.reload(); err != nil {
        t.fail(err)
    }
    if c.busy() {
        t.wait()
    } else {
        t.pass()
    }
    t.done()
    return nil
}
"""


def test_a_name_declared_on_the_reads_own_line_is_not_an_input():
    """Go's ``if err := f(); err != nil``: the read after the declaration sees
    the new ``err``, though an earlier one is still in scope above the span."""
    spans = _spans(_GO_IF_INIT, "go", "go")
    holding = [x for (s, e), x in spans.items() if s == 7]
    assert holding, "expected a span from the if on"
    assert all("err" not in x.params for x in holding)


def test_a_c_style_loop_variable_is_not_an_input():
    src = """
    function f(rows, out) {
      let i = rows.length;
      out.note(i);
      out.mark();
      for (let i = 0; i < 3; i += 1) {
        if (rows[i]) {
          out.add(rows[i]);
        } else {
          out.skip(i);
        }
      }
      out.flush();
      out.done();
      return out;
    }
    """
    spans = _spans(src, "typescript", "ts")
    holding = [x for (s, e), x in spans.items() if s == 5]
    assert holding, "expected a span from the loop on"
    assert all("i" not in x.params for x in holding)


_ARM = """
fn pick(k: i32, xs: &[i32]) -> usize {
    let mut best = xs[0];
    let n = xs.len();
    log(n);
    trace(k);
    match k {
        1 => {
            best = 7;
        }
        _ => {}
    }
    if best > 3 {
        log(best);
    } else {
        log(0);
    }
    done();
    flush();
    n
}
"""


def test_a_write_in_an_arm_does_not_cover_the_read_after_it():
    """The input side of #3451: the arm may not run."""
    spans = _spans(_ARM, "rust", "rs")
    holding = [x for (s, e), x in spans.items() if s <= 6 and e >= 16]
    assert holding, "expected a span holding the match and the read"
    for x in holding:
        assert "best" in x.params, (x.start_line, x.end_line, x.params)


# --- A declaration that gives the name no value -----------------------------
#
# The compiler has proved a later read is assigned on every path; the
# may-analysis cannot see that through a ``do`` body or an arm, and would pass
# a variable that holds nothing. Java refuses that call.

_JAVA_DO = """
class C {
    void cleanup(Queue<Integer> queue, boolean resend) {
        boolean interrupted = Thread.interrupted();
        List<Integer> results{INIT};
        do {
            results = queue.poll();
            send(results);
        } while (!results.isEmpty());
        if (resend) {
            awaitReplies();
        }
        done(interrupted);
    }
}
"""


def test_a_java_local_declared_without_a_value_is_not_passed_in():
    """kestra's ``GrpcWorkerIOSender.cleanup``: ``List<T> results;`` and a
    ``do`` loop that assigns it before every read."""
    spans = _spans(_JAVA_DO.replace("{INIT}", ""), "java", "java")
    assert (5, 11) in spans
    assert spans[(5, 11)].params == ("queue", "resend")


def test_the_same_local_with_a_value_is_passed_in():
    """The control: with ``= null`` there is a value to see, and the analysis
    cannot rule it out, so the rule of #3523 applies."""
    spans = _spans(_JAVA_DO.replace("{INIT}", " = null"), "java", "java")
    assert spans[(5, 11)].params == ("queue", "resend", "results")


_TS_IF = """
function run(query: string, args: unknown, out: Sink) {
  out.note(query);
  let parsedArgs{INIT};
  if (args) {
    parsedArgs = typeof args === 'string' ? JSON.parse(args) : args;
  } else {
    out.mark();
  }
  out.note(1);
  const body = { statement: query, ...(parsedArgs ? parsedArgs : {}) };
  out.send(body);
  out.done();
}
"""


@pytest.mark.parametrize("init", ["", ": Args | undefined", " = null"])
def test_a_typescript_let_that_holds_undefined_is_passed_in(init):
    """ToolJet's couchbase ``query_operations``: ``let parsedArgs;``. With a
    falsy ``args`` the read sees ``undefined``, which is a value: the helper
    needs it like any other."""
    spans = _spans(_TS_IF.replace("{INIT}", init), "typescript", "ts")
    holding = [x for (s, e), x in spans.items() if s == 4 and e >= 10]
    assert holding, sorted(spans)
    assert all("parsedArgs" in x.params for x in holding)


def test_a_javascript_let_is_passed_in():
    src = _TS_IF.replace("query: string, args: unknown, out: Sink", "query, args, out")
    spans = _spans(src.replace("{INIT}", ""), "javascript", "js")
    holding = [x for (s, e), x in spans.items() if s == 4 and e >= 10]
    assert holding, sorted(spans)
    assert all("parsedArgs" in x.params for x in holding)


_TS_DO = """
function cleanup(queue: Queue, resend: boolean, out: Sink) {
  const interrupted = out.interrupted();
  let results{INIT};
  do {
    results = queue.poll();
    out.send(results);
  } while (results.length > 0);
  if (resend) {
    out.awaitReplies();
  }
  out.done(interrupted);
}
"""


def test_a_typed_typescript_let_is_not_passed_in():
    """``let results: number[];`` cannot be read until it is assigned, so the
    compiler has proved the ``do`` body's write; passing it would not compile."""
    spans = _spans(_TS_DO.replace("{INIT}", ": number[]"), "typescript", "ts")
    assert (4, 10) in spans
    assert spans[(4, 10)].params == ("out", "queue", "resend")


def test_the_same_let_without_a_type_is_passed_in():
    spans = _spans(_TS_DO.replace("{INIT}", ""), "typescript", "ts")
    assert spans[(4, 10)].params == ("out", "queue", "resend", "results")


_JAVA_SAME_LINE = """
class C {
    void f(boolean c, Sink out) {
        out.note(1);
        int x; x = 1;
        if (c) {
            x = 7;
        } else {
            out.mark();
        }
        out.note(2);
        if (x > 3) {
            out.log(x);
        } else {
            out.log(0);
        }
        out.done();
    }
}
"""


def test_an_assignment_on_the_declarations_line_is_a_value():
    """``int x; x = 1;``: the line holds a bare declaration and a value, and
    what the read sees from it is the value."""
    spans = _spans(_JAVA_SAME_LINE, "java", "java")
    holding = [x for (s, e), x in spans.items() if s == 5 and e >= 14]
    assert holding, sorted(spans)
    assert all("x" in x.params for x in holding)


def _valueless(src: str, language: str, ext: str) -> dict[str, bool]:
    result = analyze_file(
        f"m.{ext}", language, textwrap.dedent(src).lstrip("\n").encode(), flagged_only=False
    )
    if result.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    flags: dict[str, bool] = {}
    for d in result.functions[0].def_use.definitions:
        flags.setdefault(d.var, d.valueless)  # the declaration, not a later write
    return flags


@pytest.mark.parametrize(
    ("language", "ext", "src", "want"),
    [
        (
            "java",
            "java",
            "class C { void f() { int a; int b = 1; String c; a = 2; use(a, b, c); } }",
            {"a": True, "b": False, "c": True},
        ),
        (
            "typescript",
            "ts",
            "function f() { let a; let b = 1; let c: string; var d; a = 2; use(a, b, c, d); }",
            {"a": False, "b": False, "c": True, "d": False},
        ),
        # Only a type that cannot hold ``undefined`` makes the name unreadable.
        (
            "typescript",
            "ts",
            "function f() { let e: string | undefined; let g!: string; let h: any;"
            " let i: number | (string | undefined); let j: Box<undefined>;"
            " let k: unknown; let l: void; let m: string = ''; let n: number | string;"
            " use(e, g, h, i, j, k, l, m, n); }",
            {
                "e": False,
                "g": False,
                "h": False,
                "i": False,
                "j": True,
                "k": False,
                "l": False,
                "m": False,
                "n": True,
            },
        ),
        (
            "javascript",
            "js",
            "function f() { let a; var d; a = 2; use(a, d); }",
            {"a": False, "d": False},
        ),
        (
            "csharp",
            "cs",
            "class C { void F() { int a; int b = 1; string c; a = 2; Use(a, b, c); } }",
            {"a": True, "b": False, "c": True},
        ),
        (
            "cpp",
            "cpp",
            "void f() { int a; int b = 1; int* p; static int s; a = 2; use(a, b, p, s); }",
            {"a": True, "b": False, "p": True, "s": False},
        ),
        (
            "rust",
            "rs",
            "fn f() { let a; let b = 1; let c: u8; a = 2; c = 3; go(a, b, c); }",
            {"a": True, "b": False, "c": True},
        ),
        # Go's zero value is a value, and Python has no bare declaration.
        (
            "go",
            "go",
            "package p\nfunc f() { var a int; b := 1; use(a, b) }",
            {"a": False, "b": False},
        ),
        ("python", "py", "def f():\n    b = 1\n    use(b)\n", {"b": False}),
    ],
)
def test_which_declarations_give_no_value(language, ext, src, want):
    got = _valueless(src, language, ext)
    assert {name: got[name] for name in want} == want


def test_a_later_assignment_to_the_name_is_a_value():
    """Only the declaration is without one: ``a = 2`` is an ordinary write."""
    result = analyze_file(
        "m.java", "java", b"class C { void f() { int a; a = 2; use(a); } }", flagged_only=False
    )
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for java")
    flags = [d.valueless for d in result.functions[0].def_use.definitions if d.var == "a"]
    assert flags == [True, False]


# --- the staged split goes through the same rule ----------------------------
#
# ``stages.py`` sizes each stage with ``_infer_in_out``. Without the input its
# composition check saw the read lose its value and refused the whole plan.


def _stages(src: str, language: str = "python", ext: str = "py", *, max_returns: int = 3):
    result = analyze_file(
        f"m.{ext}", language, textwrap.dedent(src).lstrip("\n").encode(), flagged_only=False
    )
    if result.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    plan = find_stages(result.functions[0], get_language_map(language), max_returns=max_returns)
    return plan, [(x.start_line, x.end_line, x.params, x.returns) for x in plan.stages]


_PY_STAGES = """
def load(items, strict):
    seen = []
    invalid = False
    for it in items:
        if it.a:
            seen.append(it.a)
        elif it.b:
            seen.append(it.b)
        if it.c and strict:
            seen.append(it.c)
    if not seen:
        if strict:
            invalid = True
    elif len(seen) > 9:
        if strict:
            invalid = True
    if invalid and strict:
        report(seen)
    return seen
"""


def test_a_stage_takes_the_name_a_branch_of_it_may_write():
    """The shape of hermes-webui's ``_load_extension_state``: a flag set
    before the stage, raised on some paths inside it and read at its end."""
    plan, stages = _stages(_PY_STAGES)
    assert plan.reason is None
    assert stages == [
        (2, 10, ("items", "strict"), ("invalid", "seen")),
        (11, 18, ("invalid", "seen", "strict"), ()),
    ]


_TS_STAGES = """
function load(items: Item[], strict: boolean, seen: string[]): string[] {
  let worst{INIT};
  for (const it of items) {
    if (it.a) {
      seen.push(it.a);
    } else if (it.b) {
      seen.push(it.b);
    } else if (it.c && strict) {
      seen.push(it.c);
    }
    if (it.d || it.e) {
      seen.push(it.d);
    }
  }
  if (seen.length === 0) {
    if (strict) {
      worst = "none";
    }
  } else if (seen.length > 9) {
    if (strict) {
      worst = "many";
    }
  } else if (seen.length > 5 && strict) {
    worst = "some";
  }
  if (worst && strict) {
    report(worst);
  }
  return seen;
}
"""


def test_a_stage_takes_it_in_typescript_too():
    plan, stages = _stages(
        _TS_STAGES.replace("{INIT}", " = null"), "typescript", "ts", max_returns=1
    )
    assert plan.reason is None
    assert stages == [
        (2, 14, ("items", "seen", "strict"), ("worst",)),
        (15, 28, ("seen", "strict", "worst"), ()),
    ]


_TS_DRAIN = """
function drain(items: Item[], strict: boolean, seen: string[], queue: Queue): string[] {
  let batch{INIT};
  for (const it of items) {
    if (it.a) {
      seen.push(it.a);
    } else if (it.b) {
      seen.push(it.b);
    } else if (it.c && strict) {
      seen.push(it.c);
    }
    if (it.d || it.e) {
      seen.push(it.d);
    }
  }
  do {
    batch = queue.poll(seen);
    if (batch.length > 9 && strict) {
      report(batch);
    } else if (batch.length > 5 || seen.length === 0) {
      seen.pop();
    }
    if (strict && seen.length > 3) {
      queue.resend(seen);
    }
    if (seen.length > 7) {
      seen.shift();
    } else if (seen.length > 6 && strict) {
      queue.flush();
    }
  } while (batch.length > 0 && strict);
  return seen;
}
"""


def test_no_stage_is_handed_a_declaration_without_a_value():
    """With ``let batch: string[];`` the second stage is not given ``batch``,
    and the composition check then declines the split, as it did before
    #3523. Handed on, the first stage would return a name never assigned."""
    plan, stages = _stages(
        _TS_DRAIN.replace("{INIT}", ": string[]"), "typescript", "ts", max_returns=1
    )
    assert stages == []
    assert plan.reason == "composition"


def test_a_stage_is_handed_a_let_that_holds_undefined():
    plan, stages = _stages(_TS_DRAIN.replace("{INIT}", ""), "typescript", "ts", max_returns=1)
    assert plan.reason is None
    assert stages == [
        (2, 14, ("items", "seen", "strict"), ("batch",)),
        (15, 30, ("batch", "queue", "seen", "strict"), ()),
    ]
