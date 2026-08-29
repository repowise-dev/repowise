"""Extract Method soundness archetypes (Python).

``# unsound:`` marks a region the slicer must never offer; ``# sound:`` marks
one it must keep offering. The test reads the marker lines out of this file, so
the contract lives next to the code it describes.
"""


def clean_extraction(results, threshold):
    total = 0
    count = 0
    for row in results:
        if row > threshold:
            total += row
            count += 1
    # sound: unconditional OUT, no enclosing loop
    average = 0.0
    if count:
        average = total / count
    else:
        average = 0.0
    label = "high" if average > threshold else "low"
    record(label)
    return average


def changed_accumulator(entries, limit):
    """The conditionally-written loop accumulator, plus mutation of the list
    being iterated. This is the shape the repo's own top-ranked plan had."""
    changed = False
    for entry in list(entries):
        record(entry)
        # unsound: changed is written on one branch only
        if entry > limit:
            entries.remove(entry)
            record(entry)
            changed = True
        record(limit)
        record(entry)
    return changed


def else_assigned_twin(entries, limit):
    """The same accumulator with an exhaustive else and no mutation."""
    changed = False
    for entry in entries:
        record(entry)
        # sound: every path writes changed
        if entry > limit:
            record(entry)
            changed = True
        else:
            record(limit)
            changed = False
        record(limit)
        record(entry)
    return changed


def elif_else_chain(values, limit):
    label = ""
    for value in values:
        record(value)
        # sound: exhaustive if/elif/else
        if value > limit:
            record(value)
            label = "high"
        elif value == limit:
            record(limit)
            label = "equal"
        else:
            record(0)
            label = "low"
        record(limit)
        record(value)
    return label


def if_without_else(values, limit):
    label = "unset"
    for value in values:
        record(value)
        # unsound: no else, so one path leaves label untouched
        if value > limit:
            record(value)
            record(limit)
            label = "high"
        record(limit)
        record(value)
    return label


def loop_read_above_span(rows, limit):
    carry = 0
    for row in rows:
        record(carry)
        # unsound: carry is read above the span in the same loop
        if row > limit:
            record(row)
            carry = row + limit
        else:
            record(limit)
            carry = limit
        record(limit)
        record(row)
    return carry


def loop_mutates_iterated(queue, limit):
    seen = 0
    for item in queue:
        record(item)
        # unsound: the span mutates the collection the loop iterates
        if item > limit:
            queue.pop()
            seen = seen + 1
        else:
            queue.append(item)
            seen = seen + 2
        record(limit)
        record(item)
    return seen


def loop_local_span(rows, limit):
    out = []
    for row in rows:
        record(row)
        # sound: only loop-local state crosses the span boundary
        if row > limit:
            record(row)
            scaled = row * 2
        else:
            record(limit)
            scaled = row
        out.append(scaled)
        record(row)
    return out


def loop_rebinds_iterable(entries, limit):
    """The iterable is reassigned on the loop body's first line, so a
    line-range test mistakes it for the loop's own binder."""
    for entry in entries:
        entries = list(entries)
        # unsound: the span mutates the collection the loop iterates
        if entry > limit:
            entries.remove(entry)
            record(entry)
        else:
            record(limit)
            record(entry)
        record(limit)
        record(entry)
    return entries


def for_else_clause(rows, limit):
    """A ``for ... else`` clause runs once, not per iteration."""
    flag = ""
    total = 0
    for row in rows:
        total += row
    else:
        # sound: this clause is not loop-carried
        if total > limit:
            record(total)
            flag = "over"
        else:
            record(limit)
            flag = "under"
        record(limit)
        record(total)
    return flag


def record(value):
    return value


def try_except_assigned(rows, limit):
    """A write split across try/except cannot be proved exhaustive here."""
    rate = 0.0
    for row in rows:
        record(row)
        # unsound: neither arm is provably taken
        try:
            record(row)
            rate = row / limit
        except ZeroDivisionError:
            record(limit)
            rate = 0.0
        record(limit)
        record(row)
    return rate


def calls_a_sibling_closure(rows, prefix):
    """A span that calls a closure declared beside it must pass it in.

    The nested ``def`` is a scope whose reads are its own, and at the same time
    a plain local binding right here. Losing the binding let a span be offered
    whose lifted body called ``emit`` without receiving it, which is how the
    ranked head came to carry a plan that does not run.
    """
    seen = []

    def emit(value):
        seen.append(f"{prefix}:{value}")

    # Pinned by name in test_refactoring_corpus_golden rather than by a
    # sound: marker - the comment attaches to the span it precedes here, so
    # the marker's line arithmetic would not line up.
    for row in rows:
        if isinstance(row, str):
            emit(row.strip())
        elif isinstance(row, (int, float)):
            emit(round(float(row), 2))
        else:
            emit(str(row))
        record(row)
    return seen


def read_then_write_in_span(block, limit):
    """A loop variable read above its write, both inside one candidate span.

    Nothing after the span reads ``expect``, so it is not an OUT; the read at
    the top of the span takes the previous iteration's value. Lifting the pair
    into a helper drops the write and breaks the state machine.
    """
    names = []
    expect = True
    depth = 0
    i = 0
    while i < limit:
        ch = block[i]
        if ch == "(":
            depth += 1
            expect = True
        elif ch == ")":
            depth -= 1
        else:
            # unsound: the write below is read again on the next iteration
            symbol = str(ch)
            if expect and depth >= 1:
                names.append(symbol)
                record(symbol)
            expect = depth == 1
        i += 1
    return names


def holds_a_nested_helper(rows, limit):
    """A span containing a named nested function may not be lifted.

    Def/use never descends into a nested scope, so a sibling closure calling
    ``scale`` is invisible here. Moving the declaration out of this scope would
    move the binding away from those callers with nothing in the facts to show
    it, so the span is refused rather than guessed at.
    """
    total = 0
    # unsound: the span below holds a named nested function
    def scale(value):
        return value * limit

    for row in rows:
        total += scale(row)
        record(row)
    return total
