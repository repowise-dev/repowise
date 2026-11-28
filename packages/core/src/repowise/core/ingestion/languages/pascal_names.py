"""Pascal call sites and heritage clauses, rewritten to the names the resolvers key on.

Object Pascal is case-insensitive, and every resolver compares names exactly,
so :func:`spell_pascal_names` runs once before the graph is built:

* a call target, a type-like receiver, a heritage name and a type reference
  that the repository declares in exactly one other spelling take that
  spelling (``tfoo.create`` -> ``TFoo.Create``). A name declared in two
  spellings is left as written, a missed edge rather than a guessed one;
* a receiver that is no type name is a variable and is folded to lower case,
  as receiver typing folds the Pascal names it scans;
* ``Self`` in any case becomes ``self``, the own-instance receiver;
* ``inherited Foo`` (scope ``inherited`` from pascal.scm) becomes a call on
  ``super``, and the bare ``inherited;``, whose target is the keyword, names
  the enclosing method.

Idempotent, so a cached parse may pass through twice.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

_INHERITED = "inherited"
_TYPE_KINDS = frozenset({"class", "interface", "struct", "enum", "type_alias", "record"})


class _Spellings:
    """``{folded name: declared spellings}`` over the repository's Pascal symbols."""

    def __init__(self, symbols: Iterable[Any]) -> None:
        self.names: dict[str, set[str]] = defaultdict(set)
        self.types: dict[str, set[str]] = defaultdict(set)
        for symbol in symbols:
            self.names[symbol.name.lower()].add(symbol.name)
            if symbol.kind in _TYPE_KINDS:
                self.types[symbol.name.lower()].add(symbol.name)

    def spelled(self, name: str, declared: dict[str, set[str]] | None = None) -> str:
        """*name* as the repository spells it, when that is one spelling and *name* is none."""
        table = self.names if declared is None else declared
        spellings = table.get(name.lower())
        if not spellings or name in spellings or len(spellings) > 1:
            return name
        return next(iter(spellings))


def _spelled_call(call: Any, repo: _Spellings, names: dict[str, str]) -> Any:
    """*call* respelled; *names* maps the file's symbol ids to their names."""
    target = call.target_name
    receiver = call.receiver_name
    scope = call.scope_name
    if scope is not None and scope.lower() == _INHERITED:
        if target.lower() == _INHERITED:
            target = names.get(call.caller_symbol_id or "", target)
        receiver, scope = "super", None
    elif receiver is not None and receiver.lower() == "self":
        receiver = "self"
    elif receiver is not None and receiver.isidentifier():
        # A type name takes its declared spelling; any other receiver is a
        # variable, folded to match the folded names receiver typing scans.
        spelled = repo.spelled(receiver, repo.types)
        receiver = spelled if spelled in repo.types.get(spelled.lower(), ()) else receiver.lower()
    target = repo.spelled(target)
    if (target, receiver, scope) == (call.target_name, call.receiver_name, call.scope_name):
        return call
    return replace(call, target_name=target, receiver_name=receiver, scope_name=scope)


def spell_pascal_names(parsed_files: Iterable[Any]) -> None:
    """Rewrite Pascal call sites, heritage and type references in place (see module doc)."""
    pascal = [parsed for parsed in parsed_files if parsed.file_info.language == "pascal"]
    if not pascal:
        return
    repo = _Spellings(symbol for parsed in pascal for symbol in parsed.symbols)
    for parsed in pascal:
        names = {symbol.id: symbol.name for symbol in parsed.symbols}
        parsed.calls[:] = [_spelled_call(call, repo, names) for call in parsed.calls]
        for relation in parsed.heritage:
            relation.parent_name = repo.spelled(relation.parent_name, repo.types)
            relation.child_name = repo.spelled(relation.child_name, repo.types)
        for ref in parsed.type_refs:
            ref.type_name = repo.spelled(ref.type_name, repo.types)
