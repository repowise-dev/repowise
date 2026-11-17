"""Line-based liveness over def/use facts: a span's IN / OUT and the bindings it cannot take along."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import TYPE_CHECKING

from .reaching import definitions_at, observed_definitions

if TYPE_CHECKING:
    from collections.abc import Container

    from .analyze import FunctionAnalysis
    from .cfg import CFG
    from .defuse import FunctionDefUse


def _var_lines(def_use: FunctionDefUse) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Per-variable sorted def lines and use lines from the def/use facts.

    Parameter definitions are included (seeded at the signature line), so a
    parameter naturally counts as "defined before" any body span. Reads inside
    nested closures (``def_use.captured.reads``) count as uses at their own line.
    An import is not a def here: a helper re-imports a name rather than take
    or return it, and :func:`_declaration_escapes` refuses a span holding an
    import that later code still reads.
    """
    def_lines: dict[str, list[int]] = defaultdict(list)
    use_lines: dict[str, list[int]] = defaultdict(list)
    imported: set[str] = set()
    for d in def_use.definitions:
        if d.imports:
            imported.add(d.var)
        else:
            def_lines[d.var].append(d.line)
    for bdu in def_use.blocks.values():
        for u in bdu.uses:
            # A may-def's paired use is bookkeeping, not a read: counted, a
            # binder declared inside a ``match`` arm became its own parameter.
            if not u.echo:
                use_lines[u.name].append(u.line)
    # A closure's read counts where the closure is written: lifting the code
    # around it moves the read with it. Only names this function binds matter.
    for u in def_use.captured.reads:
        if u.name in def_lines or u.name in imported:
            use_lines[u.name].append(u.line)
    for lines in def_lines.values():
        lines.sort()
    for lines in use_lines.values():
        lines.sort()
    return def_lines, use_lines


def _import_reads(analysis: FunctionAnalysis) -> tuple[tuple[int, int], ...]:
    """Function-local import lines paired with the reads they reach."""
    imports = {
        (definition.var, definition.line)
        for definition in analysis.reaching.definitions
        if definition.imports
    }
    if not imports:
        return ()
    import_lines: dict[str, list[int]] = defaultdict(list)
    for name, line in sorted(imports):
        import_lines[name].append(line)
    return tuple(
        (definition_line, use_line)
        for name, reads in _reads_observing(analysis, import_lines).items()
        for use_line, seen in reads
        for definition_line in seen
        if (name, definition_line) in imports
    )


def _declared_before_read(def_use: FunctionDefUse) -> dict[str, frozenset[int]]:
    """Per variable, the lines where a declaration of it comes before its first
    read on the same line.

    Lines alone cannot order a write and a read that share one. A C-style
    ``for (int i = 0; i < n; i++)`` declares ``i`` and then reads it, while
    ``total = total + a[i]`` reads ``total`` and then writes it. Only a
    declaration is ordered here, by where its declarator ends: a read past that
    point sees the new name, and a read inside the declaration's own
    initializer (Go's ``x := x + 1`` in an inner scope) still sees the outer
    one. A plain assignment keeps the line rule.
    """
    first_read: dict[tuple[str, int], int] = {}
    for bdu in def_use.blocks.values():
        for u in bdu.uses:
            if u.echo:
                continue
            key = (u.name, u.line)
            first_read[key] = min(u.column, first_read.get(key, u.column))
    declared: dict[str, set[int]] = defaultdict(set)
    for d in def_use.definitions:
        # No read on the line sorts before any declarator, so it never matches.
        if d.declared_at is not None and d.declared_at <= first_read.get((d.var, d.line), -1):
            declared[d.var].add(d.line)
    return {var: frozenset(lines) for var, lines in declared.items()}


def _valueless_lines(def_use: FunctionDefUse) -> dict[str, frozenset[int]]:
    """Per variable, the lines whose only definitions of it are declarations
    that give it no value (``Definition.valueless``): Java's ``List<T> xs;``,
    TypeScript's ``let x: T;``. A line that also assigns the name
    (``int x; x = 1;``) is not one: what a read sees from it is the value."""
    bare: dict[str, set[int]] = defaultdict(set)
    valued: dict[str, set[int]] = defaultdict(set)
    for d in def_use.definitions:
        (bare if d.valueless else valued)[d.var].add(d.line)
    return {var: frozenset(at - valued[var]) for var, at in bare.items()}


def _reads_observing(
    analysis: FunctionAnalysis, def_lines: dict[str, list[int]]
) -> dict[str, list[tuple[int, frozenset[int]]]]:
    """Per variable, each read's line with the def lines it may observe.

    Reads take reaching definitions over the CFG, so a later write on a path
    that need not run (an ``else`` arm, a ``try`` body that may raise first)
    does not hide the span's value from the read. A closure's read observes
    what reaches the statement that creates the closure, or, where no recorded
    statement covers it, the nearest def above it: a read is never dropped,
    since a dropped read can only drop a return. Only names in *def_lines*
    count for closures, as in :func:`_var_lines`.
    """
    def_use, reaching = analysis.def_use, analysis.reaching
    defs = reaching.definitions
    reads: dict[str, list[tuple[int, frozenset[int]]]] = defaultdict(list)
    for use, seen in observed_definitions(def_use, reaching):
        if not use.echo:
            reads[use.name].append((use.line, frozenset(defs[i].line for i in seen)))
    statements: dict[int, tuple[int, int]] | None = None
    for u in def_use.captured.reads:
        dl = def_lines.get(u.name)
        if not dl:
            continue
        if statements is None:
            statements = _statements_by_line(analysis.cfg)
        at = statements.get(u.line)
        if at is not None:
            observed = frozenset(
                defs[i].line for i in definitions_at(def_use, reaching, at[0], u.name, at[1])
            )
        else:
            k = bisect_left(dl, u.line)
            observed = frozenset(dl[k - 1 : k])
        reads[u.name].append((u.line, observed))
    return reads


def _statements_by_line(cfg: CFG) -> dict[int, tuple[int, int]]:
    """Per line, (block id, start line) of the innermost recorded statement
    covering it."""
    best: dict[int, tuple[int, int, int]] = {}
    for block in cfg.blocks:
        for st in block.statements:
            width = st.end_line - st.start_line
            for line in range(st.start_line, st.end_line + 1):
                held = best.get(line)
                if held is None or width < held[0]:
                    best[line] = (width, block.id, st.start_line)
    return {line: (bid, start) for line, (_w, bid, start) in best.items()}


def _infer_in_out(
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    s: int,
    e: int,
    declared_first: dict[str, frozenset[int]],
    *,
    reads: dict[str, list[tuple[int, frozenset[int]]]],
    valueless: dict[str, frozenset[int]] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Infer IN (parameters) and OUT (return) variables for span ``[s, e]``.

    IN: a variable read in the span whose first in-span read is not preceded by
    an in-span write, and which has a definition before the span (a parameter or
    an earlier assignment). A write on the same line as that read precedes it
    only where *declared_first* (:func:`_declared_before_read`) says the line
    declares the name first. An earlier in-span write covers a later read only
    on the paths that run it, so a variable is also IN when some in-span read
    of it may still observe a definition from before the span (*reads*,
    :func:`_reads_observing`): the write sits under an ``if``, in a loop
    body, or in an arm. A declaration that gives the name no value
    (*valueless*, :func:`_valueless_lines`) is not such a definition: the
    compiler has proved the read assigned where the may-analysis cannot, and
    a call that passed the name would not compile. OUT: a variable written in
    the span with a read after it that may observe one of those writes
    (*reads* again). A read before the span that a write in it reaches around
    a loop is not an OUT: :func:`_loop_carry_free` refuses those spans. Both
    line lists are sorted (:func:`_var_lines`), so the span tests are bisects.
    """
    params: list[str] = []
    returns: list[str] = []
    for var in sorted((set(def_lines) | set(use_lines)) - exclude):
        dl = def_lines.get(var, [])
        ul = use_lines.get(var, [])
        u_lo, u_hi = bisect_left(ul, s), bisect_right(ul, e)
        d_lo, d_hi = bisect_left(dl, s), bisect_right(dl, e)

        if u_lo < u_hi and d_lo > 0:
            first_use = ul[u_lo]
            declaring = declared_first.get(var, ())
            covered = first_use in declaring or (d_lo < d_hi and dl[d_lo] < first_use)
            bare = valueless.get(var, ()) if valueless else ()
            if not covered or _sees_value_from_before(reads.get(var, ()), s, e, declaring, bare):
                params.append(var)

        if d_lo < d_hi and _span_write_seen_after(reads.get(var, ()), s, e):
            returns.append(var)
    return tuple(params), tuple(returns)


def _sees_value_from_before(
    var_reads: list[tuple[int, frozenset[int]]],
    s: int,
    e: int,
    declaring: Container[int],
    bare: Container[int],
) -> bool:
    """Some read in ``[s, e]`` may observe a valued definition from before the span."""
    return any(
        s <= ln <= e and ln not in declaring and any(d < s and d not in bare for d in seen)
        for ln, seen in var_reads
    )


def _span_write_seen_after(var_reads: list[tuple[int, frozenset[int]]], s: int, e: int) -> bool:
    """Some read after line *e* may observe a write inside ``[s, e]``."""
    return any(ln > e and any(s <= d <= e for d in seen) for ln, seen in var_reads)


def _hoisted_bindings(
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
) -> list[tuple[int, int]]:
    """``(first_def, first_use)`` for names the function reads before defining them.

    Such a read works only by hoisting (a JS/TS ``function foo()``), and an OUT
    cannot express a value needed before the span runs, so a span holding a
    ``first_def`` whose ``first_use`` sits above it is refused. Computed once
    per function; both line lists are sorted, so their heads are the earliest.
    """
    hoisted: list[tuple[int, int]] = []
    for var, defs in def_lines.items():
        uses = use_lines.get(var)
        if defs and uses and uses[0] < defs[0]:
            hoisted.append((defs[0], uses[0]))
    return sorted(hoisted)


def _declaration_lines(
    def_use: FunctionDefUse, *, binding: bool = False
) -> dict[str, frozenset[int]]:
    """Per variable, the lines that declare it (a ``let`` / ``var`` / ``:=`` /
    typed local, as the dialect marks with ``declared_at``, or an import).
    With *binding*, the lines that create a new binding instead
    (``declares``: multi-line declarators and loop binders included, a TS/JS
    ``var`` not)."""
    lines: dict[str, set[int]] = defaultdict(set)
    for d in def_use.definitions:
        if (d.declares if binding else d.declared_at is not None or d.imports):
            lines[d.var].add(d.line)
    return {var: frozenset(found) for var, found in lines.items()}


def _declaration_escapes(
    s: int,
    e: int,
    returns: tuple[str, ...],
    decl_lines: dict[str, frozenset[int]],
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    declared_first: dict[str, frozenset[int]],
) -> bool:
    """True when the span declares a name the code after it still refers to.

    A returned name is fine: the caller declares it from the helper's result.
    Otherwise the declaration leaves with the span and the caller's next
    reference names nothing, even when that reference is a plain assignment
    (Go ``var out T`` in the span, ``out = x`` after it, which is why liveness
    did not make it an OUT). A later reference that declares the name afresh
    (``y, err := g()``, a second ``for i := ...``, whose reads on that line
    follow the declaration per *declared_first*) needs nothing from the span.
    Block scopes are not modelled, so a same-named variable of an outer scope
    read after the span also refuses: a missed span, never a broken one.
    """
    return any(
        var not in returns
        and any(s <= ln <= e for ln in declared)
        and _needed_after(var, e, declared, def_lines, use_lines, declared_first)
        for var, declared in decl_lines.items()
    )


def _bindings_escape(
    s: int,
    e: int,
    returns: tuple[str, ...],
    decl_lines: dict[str, frozenset[int]],
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    declared_first: dict[str, frozenset[int]],
    outer: dict[str, frozenset[int]],
) -> bool:
    """A binding the span cannot take along: a declaration the code after it
    still needs (:func:`_declaration_escapes`), or a ``global`` / ``nonlocal``
    write whose declaration stays behind (:func:`_outer_write_escapes`)."""
    return _declaration_escapes(
        s, e, returns, decl_lines, def_lines, use_lines, declared_first
    ) or _outer_write_escapes(s, e, outer, def_lines)


def _outer_write_escapes(
    s: int, e: int, outer: dict[str, frozenset[int]], def_lines: dict[str, list[int]]
) -> bool:
    """True when the span writes a ``global`` / ``nonlocal`` name it cannot
    take its declaration along for: one declared outside the span, or also
    written outside it. In a helper without the declaration the write binds a
    local and the module value never changes."""
    for var, declared in outer.items():
        writes = def_lines.get(var, ())
        if not any(s <= ln <= e for ln in writes):
            continue
        if any(not s <= ln <= e for ln in (*declared, *writes)):
            return True
    return False


def _needed_after(
    var: str,
    e: int,
    declared: frozenset[int],
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    declared_first: dict[str, frozenset[int]],
) -> bool:
    """True when the first reference to *var* after line *e* relies on an
    earlier declaration: it is a plain use or assignment, not a fresh one."""
    uses = use_lines.get(var, ())
    after = [ln for ln in (*def_lines.get(var, ()), *uses) if ln > e]
    if not after:
        return False
    first = min(after)
    if first not in declared:
        return True
    return first in uses and first not in declared_first.get(var, ())
