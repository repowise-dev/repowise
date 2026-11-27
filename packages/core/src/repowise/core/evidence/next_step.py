"""Why a measured function costs what it does, and the next step: one pure fold.

:func:`next_step` reads a measured row's ``inside`` (the callees its time went
to, each with its heaviest ``leaf``; ``profile_convert``) and ``drivers`` and
names what the time is and what to do, from a small per-language lexicon keyed
on the callee and its leaf. :func:`verify_command` says how to check a change
for the row's workload. Nothing here reads a store: the host passes the row,
the language and the static performance findings it holds
(:func:`static_finding` turns a stored ``health_findings`` row into one).

Silence is the default. No step when:

- the workload does not lead (unit tests and benchmarks speak elsewhere);
- no callee family holds :data:`DOMINANT` of the function's time, or the
  function's own code does;
- the dominant time is a wait (an event loop, a thread or lock, a child process
  with no static finding that says it spawns one), the framework spine (router
  and middleware chain), a response write, or a database write or commit that
  no loop repeats: each request needs its own, and "batch" would be wrong;
- the dominant time is irreducible work: a stream decoder reading the next
  value from its reader (decoding the run's input or a request body reads each
  byte once, so "parse once" has nothing to reuse), a package metadata scan
  (plugin discovery runs once per process on the commands that load plugins),
  or one grouped or ``IN`` read that no loop repeats (it already loads the
  rows for every item at once);
- the function is a thin wrapper named like its dominant callee (the cost
  belongs to its drivers);
- the dominant time is a regular expression run by a function whose job is
  matching its input (a lexer, tokenizer, parser or router, by its name or its
  file's): the regex is the work;
- the row is a module body's import time (``<path>::<module>``), unless its
  heaviest item is a library the run loaded and never ran (``"called": false``,
  ``profile_convert``) holding :data:`IMPORT_LEADS` of the import, and the
  repo calls that package only from inside functions (a :func:`package_calls`
  fact): then the step is to import it inside those functions. A package the
  repo calls at import time (``log = structlog.get_logger()``, a decorator, a
  class body) is needed at startup by construction, and one with no proven
  call site may be read some other way, so both stay silent. So does the
  program's entry script, whose items carry no ``called``;
- the time sits under ``runtime.cgocall`` and no database call above it says
  what the C code is: a Go profile taken on Windows samples threads inside cgo
  while they wait, so that time is unknown, never a cost claim.

A value built as an eager ``setdefault`` default (``d.setdefault(k, Tracker())``
builds a Tracker on every call, even on a hit) is named only when the build
holds :data:`DOMINANT` of the time: the function's own callee, or, on the
constructor's own row, a driver holding that much of its time
(:func:`eager_default_fact`, read on the walk; Python only).

A database call says "batch" only when a static loop sits on the driver chain:
an ``io_in_loop`` finding whose loop calls the function, or calls a driver
that holds most of its time. A static finding on the function itself becomes
the "why" only when its boundary agrees with the dominant callee family.

Ceiling: one leaf per callee (``profile_convert`` keeps the heaviest), so a
template engine whose leaves split between file reads and path checks is read
by whichever leads. Upgrade path: keep the top leaves per callee.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from repowise.core.analysis.health.perf.dialects.python_lexicon import (
    PY_DB_NEEDS_EVIDENCE,
    PY_DB_UNAMBIGUOUS,
    PY_SUBPROC_METHODS,
)
from repowise.core.analysis.health.perf.loop_lexicon import words
from repowise.core.frame_resolver import parse_jvm_frame
from repowise.core.ingestion.external_systems.io_kind import classify_io_kind
from repowise.core.ingestion.symbol_identity import base_symbol_id, file_of_symbol

from .format_v1 import is_import_row

#: Workload classes whose rows lead, so their rows get a step.
STEP_CLASSES = frozenset({"entry_point", "load_test", "production"})
#: Share of the function's time one callee family must hold to be the reason.
DOMINANT = 0.5
#: A leaf names its callee's cost when it holds this much of the callee's time.
LEAF_LEADS = 0.5
#: A second family worth naming beside the dominant one (a walk beside its stats).
ALSO = 0.1
#: The rule, and the fact kind, of a value built as an eager ``setdefault`` default.
EAGER_DEFAULT = "eager_default"
_CONSTRUCTORS = frozenset({"__init__", "__new__"})

#: Share of an import row's time one never-run library must hold to be named: an
#: import spreads its time over many packages, and the step moves one of them.
IMPORT_LEADS = 0.25
#: The :func:`next_step` *findings* kind of a :func:`package_calls` fact.
PACKAGE_CALLS = "package_calls"
#: Caller kinds that run only when called; any other caller (a module-level
#: assignment, a class body, the module itself) runs at import time.
_CALLED_KINDS = frozenset({"function", "method"})
#: Functions a step names before it counts them instead.
_NAMED_CALLERS = 3

# --- the lexicon: last name segments, per family -----------------------------

_FS_METADATA = frozenset(
    {
        "stat",
        "lstat",
        "statSync",
        "lstatSync",
        "existsSync",
        "accessSync",
        "is_file",
        "is_dir",
        "is_symlink",
        "isfile",
        "isdir",
        "islink",
        "getmtime",
        "getsize",
        "realpathSync",
        "os.Stat",
        "os.Lstat",
    }
)
_DIR_WALK = frozenset(
    {
        "rglob",
        "iterdir",
        "listdir",
        "scandir",
        "select_recursive",
        "_select_recursive",
        "select_wildcard",
        "readdirSync",
        "opendirSync",
        "filepath.Walk",
        "filepath.WalkDir",
        "os.ReadDir",
        "fs.WalkDir",
    }
)
# Names other libraries spell too (``ast.walk``, ``Redis.exists``): filesystem only
# when the module says so or the stacks end in the filesystem.
_FS_AMBIGUOUS = frozenset({"walk", "glob", "iglob", "readdir", "exists", "access"})
_FS_QUALIFIED = frozenset(
    {
        "os.walk",
        "Path.walk",
        "glob.glob",
        "glob.iglob",
        "Path.glob",
        "Path.exists",
        "path.exists",
        "os.access",
        "fs.readdir",
        "fs.exists",
        "fs.access",
    }
)
# Template engines: the call that renders, and calls that always read and compile.
_TEMPLATE_RENDER = frozenset(
    {
        "renderFile",
        "tryHandleCache",
        "handleCache",
        "renderString",
        "render_template",
        "render_to_string",
        "Template.render",
        "ExecuteTemplate",
    }
)
_TEMPLATE_LOAD = frozenset({"ParseFiles", "ParseGlob"})
_FILE_READ_LEAVES = frozenset(
    {
        "open",
        "openSync",
        "read",
        "readSync",
        "readFileSync",
        "readFile",
        "close",
        "closeSync",
        "fstat",
        "compile",
        "Template",
        "read_text",
        "io.open",
    }
)
_ETAG = frozenset({"etag", "generateETag", "entitytag", "generate_etag"})
_HASH = frozenset(
    {"createHash", "digest", "hexdigest", "sha1", "sha256", "sha512", "md5", "blake2b"}
)
_SPINE = frozenset(
    {
        "handle",
        "next",
        "dispatch",
        "processParams",
        "trim_prefix",
        "handle_request",
        "call_next",
        "wsgi_app",
        "full_dispatch_request",
        "dispatch_request",
        "get_response",
        "_get_response",
        "(*Context).Next",
        "(*Engine).ServeHTTP",
        "(*Engine).handleHTTPRequest",
        "(*ServeMux).ServeHTTP",
        "HandlerFunc.ServeHTTP",
    }
)
_RESPONSE_WRITE = frozenset(
    {
        "end",
        "write",
        "writev",
        "writeHead",
        "_send",
        "flushHeaders",
        "uncork",
        "sendall",
        "(*response).Write",
        "(*response).Flush",
        "(*conn).Write",
    }
)
_SLEEP = frozenset({"sleep", "Sleep", "time.Sleep", "setTimeout"})
# Calls that run or wait on a child process (``subprocess.run`` reads as a wait).
_CHILD_WAITS = PY_SUBPROC_METHODS | {"communicate", "wait", "_wait", "_communicate", "waitpid"}
_IMPORT_PY = frozenset({"_find_and_load", "_gcd_import", "import_module", "__import__"})
_REGEX_COMPILE = frozenset({"regexp.MustCompile", "regexp.Compile", "Pattern.compile"})
_PY_RE_MISS_LEAVES = frozenset({"parse", "_parse", "_parse_sub", "_code", "compile"})
# A regular expression run in the call: the matcher's own frames, by owner. V8
# names the compiled pattern ``RegExp: <source>`` whichever call ran it (test,
# exec, match, replace); Python's are the compiled pattern's C methods as
# pyinstrument (``Pattern.match``) and cProfile name them. Methods that return a
# lazy sequence (``Matcher.results``, Kotlin's ``findAll``) match nothing in the
# call and stay out. Ceiling: py-spy names ``re.match`` bare (``match``), which
# other libraries spell too, so a module-level ``re`` call is not read. Upgrade
# path: key it on the frame's file (``re/__init__.py``) once ``inside`` carries it.
_JVM_REGEX_RUNS = {
    "java.util.regex.Matcher": frozenset(
        {"matches", "find", "lookingAt", "replaceAll", "replaceFirst"}
    ),
    "kotlin.text.Regex": frozenset(
        {"matches", "matchEntire", "containsMatchIn", "find", "replace", "split"}
    ),
}
_GO_REGEX_RUNS = re.compile(r"regexp\.\(\*Regexp\)\.(?:Match\w*|Find\w*|ReplaceAll\w*|Split)")
_PY_PATTERN_VERBS = frozenset({"match", "search", "fullmatch", "sub", "subn"})
_PY_CPROFILE_PATTERN = re.compile(r"<method '(\w+)' of 're\.Pattern' objects>")
# Calls that compile their pattern each time they run them.
_JVM_REGEX_PER_CALL = {
    "java.lang.String": frozenset({"matches", "replaceAll", "replaceFirst"}),
    "java.util.regex.Pattern": frozenset({"matches"}),
}
_GO_REGEX_PER_CALL = frozenset({"regexp.MatchString", "regexp.Match", "regexp.MatchReader"})
# Words of a function, class or file name whose job is matching its input: the
# regex is the work.
_MATCHING_JOB_WORDS = frozenset(
    {
        "lex",
        "lexer",
        "lexing",
        "token",
        "tokens",
        "tokenize",
        "tokenizer",
        "tokenise",
        "tokeniser",
        "scan",
        "scanner",
        "parse",
        "parser",
        "parsing",
        "grammar",
        "match",
        "matcher",
        "matches",
        "matching",
        "regex",
        "regexp",
        "pattern",
        "patterns",
        "glob",
        "route",
        "router",
        "routes",
        "routing",
        "highlight",
        "highlighter",
        "syntax",
    }
)
_PARSE = frozenset(
    {
        "loads",
        "safe_load",
        "load_all",
        "fromstring",
        "literal_eval",
        "json.Unmarshal",
        "yaml.Unmarshal",
        "xml.Unmarshal",
    }
)
# Stream decoders: each call reads the next value off a reader (Go's json, xml,
# gob and yaml ``Decoder``, a type that only reads a stream), so the bytes are
# new every call. PyYAML's ``load_all`` takes a stream or a held string alike,
# so it stays a parse. Ceiling: a decoder built per call over the same buffer
# re-parses it and stays silent too; upgrade path: a static fact that the
# reader wraps a value the function already holds.
_STREAM_DECODE = frozenset({"(*Decoder).Decode"})
_DEEP_COPY = frozenset(
    {"deepcopy", "_deepcopy_dict", "_deepcopy_list", "cloneDeep", "structuredClone"}
)
_PATH_PARSE = frozenset(
    {
        "suffix",
        "suffixes",
        "stem",
        "parts",
        "parent",
        "parents",
        "with_suffix",
        "joinpath",
        "__truediv__",
        "relative_to",
        "_from_parts",
        "_parse_args",
        "_parse_path",
        "_tail",
        "_load_parts",
        "_make_child",
    }
)
_PATH_INTERNALS = frozenset(
    {
        "_tail",
        "_parse_path",
        "_from_parts",
        "_parse_args",
        "_load_parts",
        "splitroot",
        "_make_child",
    }
)
_PACKAGE_METADATA = frozenset({"entry_points", "distributions", "packages_distributions"})
# Query builders that make one read cover many items: a GROUP BY, or an IN over
# a list of keys (gorm's ``(*DB).Group``; SQLAlchemy's ``group_by`` and
# ``ColumnOperators.in_``). Builders cost little, so they show only when the run
# sampled them: silence on what the run saw, never a guess from the name.
_GO_GROUPED = frozenset({"(*DB).Group"})
_PY_GROUPED = frozenset(
    {"Query.group_by", "Select.group_by", "GenerativeSelect.group_by", "ColumnOperators.in_"}
)
_GO_MAP = re.compile(r"^(?:runtime\.(?:mapassign|makemap|mapiter|mapIter)|internal/runtime/maps\.)")
_GO_CGO = "runtime.cgocall"

# Database calls: reads repeat per item, writes and commits are per request.
_DB_WRITE_VERBS = frozenset(
    {
        "Save",
        "Create",
        "CreateInBatches",
        "Update",
        "Updates",
        "UpdateColumn",
        "UpdateColumns",
        "Delete",
        "Exec",
        "ExecContext",
        "Commit",
        "commit",
        "flush",
        "add",
        "add_all",
        "merge",
        "persist",
        "saveOrUpdate",
        "executeUpdate",
        "bulkCreate",
        "destroy",
        "upsert",
        "createMany",
        "updateMany",
        "deleteMany",
        "insertMany",
        "executemany",
        "bulk_save_objects",
    }
)
# Go calls that run a statement (gorm's ``Where`` or ``Preload`` only build one).
_GO_DB_EXEC = (
    frozenset(
        {
            "First",
            "Find",
            "Take",
            "Last",
            "Count",
            "Scan",
            "Pluck",
            "Row",
            "Rows",
            "FirstOrCreate",
            "FirstOrInit",
            "Query",
            "QueryRow",
            "QueryContext",
            "QueryRowContext",
            "Get",
        }
    )
    | _DB_WRITE_VERBS
)
_PY_DB_RECEIVERS = frozenset(
    {
        "Session",
        "AsyncSession",
        "scoped_session",
        "Connection",
        "AsyncConnection",
        "Cursor",
        "Query",
        "Result",
        "Engine",
    }
)
_PY_DB_VERBS = PY_DB_UNAMBIGUOUS | PY_DB_NEEDS_EVIDENCE | {"all", "first", "one", "one_or_none"}
# Django: iterating a queryset (or ``len``, ``bool``, ``iterator()``) runs its query;
# ``filter`` or ``all`` only build one. Left out: single-row lookups (``first``,
# ``get``, ``count``), whose one call per request is the request's own work, and
# raw SQL (``RawQuerySet``, cursor ``execute``), which can be a write: nothing in the
# row says which (ceiling; upgrade path: read them once the static loop analysis
# sees Django ORM calls and the statement kind).
_DJANGO_EVALUATE = frozenset({"__iter__", "__len__", "__bool__", "iterator"})
_JS_DB_READS = frozenset(
    {
        "findOne",
        "findAll",
        "findByPk",
        "findOrCreate",
        "findAndCountAll",
        "findMany",
        "findUnique",
        "findFirst",
        "countDocuments",
    }
)
_JVM_DB_RECEIVERS = frozenset(
    {
        "Session",
        "SessionImpl",
        "EntityManager",
        "Query",
        "TypedQuery",
        "Statement",
        "PreparedStatement",
        "JdbcTemplate",
    }
)
_JVM_DB_VERBS = frozenset(
    {"find", "getResultList", "getSingleResult", "list", "uniqueResult", "executeQuery", "query"}
)
# A function whose own name says it ends a transaction: its SQL time is a commit.
_TRANSACTION_NAME = re.compile(r"(?i)_?(?:atomic|transaction|commit|savepoint|release_savepoint)")
_MIDDLEWARE = re.compile(r"(?i)middleware")

# Static finding boundaries that agree with a callee family (S2).
_AGREES = {
    "filesystem": frozenset({"filesystem", "template_reread", "template_include"}),
    "subprocess": frozenset({"subprocess"}),
    "db": frozenset({"db_read", "db_write_loop"}),
}

_SILENT = frozenset(
    {
        "wait",
        "spine",
        "response_write",
        "db_write",
        "db_grouped",
        "unknown_native",
        "regex_work",
        "input_decode",
        "package_metadata",
    }
)


@dataclass(frozen=True)
class NextStep:
    """What a measured function's time is, and what to do about it."""

    #: The lexicon entry that fired (``filesystem``, ``import``, ``database`` ...).
    rule: str
    #: What the time is: the callee split in words (or the agreeing static finding).
    why: str
    #: What to do.
    next: str
    #: How to check a change for this workload, when it has a command.
    verify: str | None
    #: The dominant callee and its share of the function's time.
    callee: str
    share: float
    #: The callee's heaviest leaf, when it named the cost.
    leaf: str | None = None
    #: The static finding (biomarker) that wrote ``why``, when one agreed.
    static: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def verify_command(workload_name: str | None, workload_class: str | None) -> str | None:
    """How to check a change against *workload_name*: ``perf measure`` for an
    entry point (its name is the command), the load run again for a load test,
    the next window for production."""
    if not workload_name:
        return None
    if workload_class == "entry_point":
        return (
            f"repowise perf measure --save before -- {workload_name} before the change, "
            "then --against before after it"
        )
    if workload_class == "load_test":
        return (
            f'run the load again and repowise evidence add <profile> --workload "{workload_name}"'
            " --class load_test"
        )
    if workload_class == "production":
        return f"compare this row in the next {workload_name} window"
    return None


def static_finding(
    file_path: str,
    function_name: str | None,
    biomarker_type: str,
    reason: str | None,
    details: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """A stored performance ``health_findings`` row in the shape :func:`next_step` reads."""
    details = details or {}
    return {
        "file_path": file_path,
        "function": function_name,
        "kind": biomarker_type,
        "reason": reason,
        "boundary": details.get("boundary_kind"),
        "owner": details.get("role_owner"),
        "path": list(details.get("path") or ()),
        "sink": details.get("sink_call"),
    }


def eager_default_fact(symbol_id: str, eager_defaults_json: str | None) -> dict[str, Any] | None:
    """A function's stored eager defaults (``function_facts.eager_defaults_json``)
    in the shape :func:`next_step` reads beside the static findings; ``None``
    when there are none or the value does not parse."""
    try:
        raw = json.loads(eager_defaults_json or "[]")
    except ValueError:
        return None
    builds = [
        (b[0], b[1])
        for b in (raw if isinstance(raw, list) else ())
        if isinstance(b, list) and len(b) == 2 and isinstance(b[0], str) and isinstance(b[1], int)
    ]
    if not builds:
        return None
    return {
        "kind": EAGER_DEFAULT,
        "symbol": symbol_id,
        "file_path": file_of_symbol(symbol_id),
        "function": symbol_id.rsplit("::", 1)[-1],
        "builds": builds,
    }


def never_called(inside: Iterable[Any] | None) -> list[str]:
    """The libraries an import row's *inside* says the run loaded and never ran."""
    return [
        str(item["name"])
        for item in _items(inside)
        if item.get("kind") == "library" and item.get("called") is False and item.get("name")
    ]


def package_calls(package: str, callers: Iterable[tuple[str, str | None]]) -> dict[str, Any]:
    """A package's production call sites as :func:`next_step` reads them.

    *callers* are ``(caller symbol id, its symbol kind)`` per call site the
    index proved (``external_call_sites``); a kind outside function and method
    (a module-level assignment, a class body, ``None`` for the module itself)
    runs the call at import time.
    """
    callers = list(callers)
    functions = sorted({sid for sid, kind in callers if kind in _CALLED_KINDS})
    return {
        "kind": PACKAGE_CALLS,
        "package": package,
        "callers": functions[:_NAMED_CALLERS],
        "callers_total": len(functions),
        "at_import": any(kind not in _CALLED_KINDS for _, kind in callers),
    }


def next_step(
    row: Mapping[str, Any],
    language: str,
    findings: Iterable[Mapping[str, Any]] = (),
) -> NextStep | None:
    """The next step for one measured *row*, or ``None`` (silence).

    *row* carries ``inside``, ``drivers``, ``workload_name``, ``workload_class``
    and ``symbol_id`` (a ``functions`` route row; ``inside`` and ``drivers`` as
    lists). *language* is the function's file language (``python``, ``go``,
    ``javascript`` ...). *findings* are :func:`static_finding` dicts: those on the
    function or naming it or its drivers in a loop are read, the rest ignored;
    and :func:`package_calls` facts, which an import row reads.
    """
    inside = _items(row.get("inside"))
    symbol = row.get("symbol_id") or ""
    if row.get("workload_class") not in STEP_CLASSES or not inside:
        return None
    if is_import_row(symbol):
        return _unused_import(row, inside, findings)
    lang = _family(language)
    findings = list(findings)
    eager = _eager_default(row, symbol, inside, lang, findings)
    if eager is not None:
        return eager
    own_static = [f for f in findings if _on(f, symbol)]
    picked = _dominant(inside, lang, own_static, symbol)
    if picked is None:
        return None
    family, share, group = picked
    drivers = _items(row.get("drivers"))
    family, loop = _refine(
        family, group, inside, lang, symbol, drivers, findings, row.get("workload_class")
    )
    if family in _SILENT:
        return None
    lead = _lead(group)
    why, step = _WORDS[family](_Ctx(lang, family, group, share, lead, drivers, loop))
    static = _agreeing(own_static, family)
    leaf = lead.get("leaf") if isinstance(lead.get("leaf"), Mapping) else None
    return NextStep(
        rule=family,
        why=f"{static['reason']} (measured: {why})" if static and static.get("reason") else why,
        next=step,
        verify=verify_command(row.get("workload_name"), row.get("workload_class")),
        callee=str(lead.get("name") or ""),
        share=round(share, 4),
        leaf=str(leaf["name"]) if leaf and family in _LEAF_RULES else None,
        static=static["kind"] if static else None,
    )


def _eager_default(
    row: Mapping[str, Any],
    symbol: str,
    inside: Sequence[Mapping[str, Any]],
    lang: str,
    findings: Sequence[Mapping[str, Any]],
) -> NextStep | None:
    """The eager ``setdefault`` step: the function's own dominant callee is a
    value it builds as one, or this row is that build and a driver holding
    :data:`DOMINANT` of its time builds it so."""
    facts = _eager_facts(findings) if lang == "python" else {}
    found = _eager_build(symbol, row, inside, facts) if facts and symbol else None
    if found is None:
        return None
    why, fact, (call, line), where, callee, share = found
    return NextStep(
        rule=EAGER_DEFAULT,
        why=(
            f"{why} as the default of setdefault on line {line}: the default is built before "
            "the lookup, so one is built on every call, even when the key is already there"
        ),
        next=_eager_next(fact["file_path"], line, call, where),
        verify=verify_command(row.get("workload_name"), row.get("workload_class")),
        callee=callee,
        share=round(share, 4),
    )


def _unused_import(
    row: Mapping[str, Any],
    inside: Sequence[Mapping[str, Any]],
    findings: Iterable[Mapping[str, Any]],
) -> NextStep | None:
    """An import row's step: its heaviest item is a library the run never ran,
    which the repo calls only from inside functions (module docstring)."""
    lead = _lead(inside)
    share = float(lead.get("share") or 0.0)
    package = str(lead.get("name") or "")
    if share < IMPORT_LEADS or package not in never_called([lead]):
        return None
    fact = next(
        (f for f in findings if f.get("kind") == PACKAGE_CALLS and f.get("package") == package),
        None,
    )
    if fact is None or fact.get("at_import") or not fact.get("callers"):
        return None
    callers = [str(c) for c in fact["callers"]]
    total = int(fact.get("callers_total") or len(callers))
    files = {file_of_symbol(c) for c in callers}
    named = _and([_fn(c) for c in callers]) + (f" in {files.pop()}" if len(files) == 1 else "")
    if total == 1:
        where = f"{named}, the one function that calls it"
    elif total <= len(callers):
        where = f"{named}, the functions that call it"
    else:
        where = f"the {total} functions that call it"
    return NextStep(
        rule="unused_import",
        why=f"{_pct(share)} of this import loads {package}, and no sample of the run ran {package}",
        next=(
            f"import {package} inside {where}, not at module level, so a run that never calls "
            f"them skips loading it; a name the module reads from {package} at import time keeps "
            "the load, so move that read too"
        ),
        verify=verify_command(row.get("workload_name"), row.get("workload_class")),
        callee=package,
        share=round(share, 4),
    )


def _eager_facts(findings: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The eager default facts among *findings*, by function symbol id."""
    return {f["symbol"]: f for f in findings if f.get("kind") == EAGER_DEFAULT and f.get("symbol")}


def _eager_build(
    symbol: str,
    row: Mapping[str, Any],
    inside: Sequence[Mapping[str, Any]],
    facts: Mapping[str, Mapping[str, Any]],
) -> tuple[Any, ...] | None:
    return _own_eager_build(symbol, inside, facts) or _driver_eager_build(symbol, row, facts)


def _own_eager_build(
    symbol: str, inside: Sequence[Mapping[str, Any]], facts: Mapping[str, Mapping[str, Any]]
) -> tuple[Any, ...] | None:
    """A dominant callee of the function that is one of its own eager defaults."""
    own = facts.get(symbol)
    for callee in inside if own is not None else ():
        share = float(callee.get("share") or 0.0)
        if share < DOMINANT or callee.get("kind") in ("own", "wait"):
            continue
        name = str(callee.get("name") or "")
        build = next((b for b in own["builds"] if _builds(name, b[0])), None)
        if build is not None:
            why = f"{_pct(share)} of it builds {build[0]}() ({_short(name)})"
            return why, own, build, None, name, share
    return None


def _driver_eager_build(
    symbol: str, row: Mapping[str, Any], facts: Mapping[str, Mapping[str, Any]]
) -> tuple[Any, ...] | None:
    """On a constructor's row, a driver holding :data:`DOMINANT` of its time that
    builds its class as an eager default."""
    if not _built_class(symbol):
        return None
    frame = _frame_of(symbol)
    for driver in _items(row.get("drivers")):
        share = float(driver.get("share") or 0.0)
        fact = facts.get(str(driver.get("symbol") or ""))
        if share < DOMINANT or fact is None:
            continue
        build = next((b for b in fact["builds"] if _builds(frame, b[0])), None)
        if build is not None:
            where = _fn(str(driver["symbol"]))
            why = f"{where}, which drives {_pct(share)} of its time, builds a {build[0]}"
            return why, fact, build, where, build[0], share
    return None


def _builds(callee: str, call: str) -> bool:
    """Whether the profile's *callee* is the value *call* builds: the called
    function itself, or the constructor of the class it names. A call written
    with a module or class in front (``hot.Tracker``) must name the frame's
    (``pkg.cheap.Tracker.__init__`` is no ``hot.Tracker()``); ``self`` and
    ``cls`` name nothing a frame shows."""
    segs = _short(callee).split(".")
    if segs[-1] in _CONSTRUCTORS:
        segs = segs[:-1]
    want = call.split(".")
    if want[0] in ("self", "cls"):
        want = want[-1:]
    return len(segs) >= len(want) and segs[-len(want) :] == want


def _frame_of(symbol: str) -> str:
    """A symbol id as a profile frame names it: ``pkg/hot.py::Tracker::__init__``
    -> ``pkg.hot.Tracker.__init__`` (a package's ``__init__.py`` is the package)."""
    path, _, rest = base_symbol_id(symbol).partition("::")
    module = path.rsplit(".", 1)[0].split("/")
    if module[-1] == "__init__":
        module = module[:-1]
    return ".".join([*module, *rest.split("::")])


def _built_class(symbol: str) -> str:
    """The class a constructor row builds (``m.py::Tracker::__init__``), else ``""``."""
    parts = base_symbol_id(symbol).split("::")
    return parts[-2] if len(parts) >= 3 and parts[-1] in _CONSTRUCTORS else ""


def _eager_next(file_path: str, line: int, call: str, where: str | None) -> str:
    at = f"at {file_path}:{line}" + (f" in {where}" if where else "")
    return (
        f"{at}, build the default only when the key is missing: with the mapping and the key "
        f"evaluated once, d[key] if key in d else d.setdefault(key, {call}(...)) keeps the value "
        f"a plain dict stores and returns; leave it eager only if building a {call} has a side "
        "effect every call needs"
    )


def _and(names: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _items(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value or () if isinstance(item, Mapping)]


def _lead(group: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(group, key=lambda c: c.get("share") or 0.0)


def _dominant(
    inside: Sequence[Mapping[str, Any]],
    lang: str,
    own_static: Sequence[Mapping[str, Any]],
    symbol: str,
) -> tuple[str, float, list[Mapping[str, Any]]] | None:
    """The callee family holding :data:`DOMINANT` of the time, its share and callees;
    ``None`` for own code, an unknown callee, or a thin wrapper named like its callee."""
    families: dict[str, float] = {}
    members: dict[str, list[Mapping[str, Any]]] = {}
    for callee in inside:
        family = _classify(callee, lang, own_static)
        families[family] = families.get(family, 0.0) + float(callee.get("share") or 0.0)
        members.setdefault(family, []).append(callee)
    family, share = max(families.items(), key=lambda kv: (kv[1], kv[0]))
    if share < DOMINANT or family == "own" or family.startswith("other:"):
        return None
    lead = str(_lead(members[family]).get("name"))
    # A V8 pattern's source is no call name (``RegExp: ^\/api\/end`` under ``end``).
    if not lead.startswith("RegExp: ") and _last(symbol.rsplit("::", 1)[-1]) == _last(lead):
        return None  # a thin wrapper: the cost belongs to whoever calls it
    return family, share, members[family]


def _refine(
    family: str,
    group: Sequence[Mapping[str, Any]],
    inside: Sequence[Mapping[str, Any]],
    lang: str,
    symbol: str,
    drivers: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    workload_class: Any,
) -> tuple[str, str | None]:
    """A database family split into read and write, with the loop that repeats it:
    a write a loop repeats in one entry point run is ``db_write_loop``; one
    grouped or ``IN`` read that no loop repeats (in a caller or in the function
    itself) is ``db_grouped``; a regex run by a function whose job is matching
    its input (a lexer, a parser, a router) is the work (``regex_work``)."""
    if family == "regex_match":
        return ("regex_work" if _matching_is_the_job(symbol) else family), None
    if family != "database":
        return family, None
    family = _database_family(group, symbol)
    loop = _loop_owner(symbol, drivers, findings)
    if family == "db_write" and loop and workload_class == "entry_point":
        family = "db_write_loop"
    if (
        family == "db_read"
        and not loop
        and _one_grouped_read(group, inside, lang, symbol, findings)
    ):
        family = "db_grouped"
    return family, loop


def _own_loop(
    group: Sequence[Mapping[str, Any]], symbol: str, findings: Sequence[Mapping[str, Any]]
) -> bool:
    """A database loop in the function *symbol* whose sink may be its read."""
    read = _last(str(_lead(group).get("name") or ""))
    return any(
        f.get("kind") == "io_in_loop"
        and f.get("boundary") == "db"
        and _on(f, symbol)
        and _sink_may_be(f.get("sink"), read)
        for f in findings
    )


# --- classification ----------------------------------------------------------


def _family(language: str) -> str:
    if language in ("typescript", "javascript", "tsx", "jsx"):
        return "javascript"
    if language in ("java", "kotlin", "scala"):
        return "jvm"
    return language


def _last(name: str) -> str:
    """The last segment of a frame name: ``gorm.io/gorm.(*DB).First`` -> ``First``."""
    return name.split(" (inline)")[0].rsplit("/", 1)[-1].rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def _short(name: str) -> str:
    """A frame name without its Go module path: ``gorm.io/gorm.(*DB).First`` -> ``gorm.(*DB).First``."""
    return name.split(" (inline)")[0].rsplit("/", 1)[-1]


def _names(name: str) -> set[str]:
    """The spellings a lexicon set may hold for *name*: last segment, the last two
    (``os.Stat``, ``Connection.execute``, ``(*Context).Next``), and the short name."""
    short = _short(name)
    segs = short.split(".")
    out = {segs[-1], short}
    if len(segs) >= 2:
        out.add(".".join(segs[-2:]))
    return out


def _receiver(name: str) -> str:
    segs = _short(name).split(".")
    return segs[-2].strip("(*)") if len(segs) >= 2 else ""


def _db_package(name: str) -> bool:
    """A Go frame from a database package (``classify_io_kind`` on its import path)."""
    head = name.split(".(", 1)[0] if ".(" in name else name.rsplit(".", 1)[0]
    return any(classify_io_kind(p) == "db" for p in (head, head.rsplit("/", 1)[-1]) if p)


@dataclass(frozen=True)
class _Callee:
    """One ``inside`` item, read once for the lexicon rules."""

    name: str
    kind: Any
    names: set[str]
    leaf: str
    leaf_names: set[str]
    #: The leaf holds :data:`LEAF_LEADS` of the callee's time.
    leaf_leads: bool
    lang: str
    #: A static finding on the function says it spawns a process.
    spawns: bool


def _read_callee(
    callee: Mapping[str, Any], lang: str, own_static: Sequence[Mapping[str, Any]]
) -> _Callee:
    name = str(callee.get("name") or "")
    leaf = callee.get("leaf") if isinstance(callee.get("leaf"), Mapping) else {}
    leaf_name = str(leaf.get("name") or "")
    share = callee.get("share") or 0.0
    return _Callee(
        name=name,
        kind=callee.get("kind"),
        names=_names(name),
        leaf=leaf_name,
        leaf_names=_names(leaf_name) if leaf_name else set(),
        leaf_leads=bool(leaf_name) and (leaf.get("share") or 0.0) >= LEAF_LEADS * share,
        lang=lang,
        spawns=any(f.get("boundary") == "subprocess" for f in own_static),
    )


def _filesystem_callee(c: _Callee) -> bool:
    if c.names & (_FS_METADATA | _DIR_WALK | _FS_QUALIFIED):
        return True
    # A library whose time is a filesystem call, or a name other libraries share
    # (``walk``, ``exists``) whose stacks end in the filesystem.
    fs_leaf = bool(c.leaf_names & (_FS_METADATA | _DIR_WALK))
    return fs_leaf and (c.leaf_leads or bool(c.names & _FS_AMBIGUOUS))


def _regex_callee(c: _Callee) -> bool:
    if c.names & _REGEX_COMPILE:
        return True
    return (
        c.lang == "python" and _last(c.name) == "_compile" and _last(c.leaf) in _PY_RE_MISS_LEAVES
    )


def _jvm_owner(name: str) -> tuple[str, str] | None:
    """A JVM frame's class with its package and its method, in any spelling
    (``java/util/regex/Matcher.matches()Z_[j]`` -> ``java.util.regex.Matcher``, ``matches``)."""
    parsed = parse_jvm_frame(name)
    return (".".join((*parsed.package, parsed.cls)), parsed.method) if parsed else None


def _regex_run(name: str, kind: Any, lang: str) -> str | None:
    """``per_call`` for a call that compiles its pattern each time, ``match`` for a
    matcher run on a built pattern, ``None`` for anything else."""
    if lang == "javascript":
        return "match" if name.startswith("RegExp: ") else None
    if lang == "go":
        bare = name.split(" (inline)")[0]
        if bare in _GO_REGEX_PER_CALL:
            return "per_call"
        return "match" if _GO_REGEX_RUNS.fullmatch(bare) else None
    if lang == "jvm":
        owner = _jvm_owner(name)
        if owner is None:
            return None
        if owner[1] in _JVM_REGEX_PER_CALL.get(owner[0], ()):
            return "per_call"
        return "match" if owner[1] in _JVM_REGEX_RUNS.get(owner[0], ()) else None
    if lang == "python":
        found = _PY_CPROFILE_PATTERN.fullmatch(name)
        # pyinstrument's short spelling only for the built-in: a class of one's own
        # named ``Pattern`` has a file, so it is no native frame.
        short = kind == "native" and _receiver(name) == "Pattern"
        verb = found.group(1) if found else (_last(name) if short else "")
        return "match" if verb in _PY_PATTERN_VERBS else None
    return None


def _regex_machinery(leaf: str, lang: str) -> bool:
    """Whether a leaf is the regex engine itself, not a callback it ran."""
    if lang == "go":
        return leaf.startswith(("regexp.", "regexp/syntax."))
    if lang == "jvm":
        owner = _jvm_owner(leaf)
        return owner is not None and owner[0].startswith(("java.util.regex.", "kotlin.text."))
    if lang == "python":
        # Only the compiled pattern's own C methods: a bare ``_parse`` may be a callback's.
        return _regex_run(leaf, "native", lang) is not None
    return lang == "javascript" and leaf.startswith("RegExp: ")


def _regex_match_callee(c: _Callee) -> bool:
    """A regular expression run in the callee's own frames; a callee whose time
    ends in something else (a replacement callback's file read) is not one."""
    if _regex_run(c.name, c.kind, c.lang) is None:
        return False
    return not c.leaf_leads or _regex_machinery(c.leaf, c.lang)


def _reflection_callee(c: _Callee) -> bool:
    if c.lang == "go":
        return c.name.startswith("reflect.")
    return c.lang == "jvm" and _last(c.name) == "invoke" and _receiver(c.name) == "Method"


def _path_callee(c: _Callee) -> bool:
    if c.lang != "python" or not c.names & _PATH_PARSE:
        return False
    return bool(c.names & _PATH_INTERNALS) or _last(c.leaf) in _PATH_INTERNALS


def _cgo_callee(c: _Callee) -> bool:
    return c.name == _GO_CGO or (c.lang == "go" and c.leaf == _GO_CGO and c.leaf_leads)


#: The lexicon, in order: the first rule that holds names a callee's family.
#: ``other`` keeps the callee as a family of its own (its own row, or rendering).
_RULES: tuple[tuple[str, Callable[[_Callee], bool]], ...] = (
    ("own", lambda c: c.kind == "own"),
    ("other", lambda c: c.kind == "repo"),
    ("import", lambda c: c.kind == "import" or (c.lang == "python" and bool(c.names & _IMPORT_PY))),
    ("sleep", lambda c: c.kind == "wait" and bool(c.names & _SLEEP)),
    # A child-process wait, when a static finding says the function spawns one.
    ("subprocess", lambda c: c.kind == "wait" and c.spawns and _last(c.name) in _CHILD_WAITS),
    ("wait", lambda c: c.kind == "wait"),
    # Before the name sets: a V8 pattern's source can end in any word (``\/end``).
    ("regex_match", _regex_match_callee),
    ("spine", lambda c: bool(c.names & _SPINE)),
    ("response_write", lambda c: bool(c.names & _RESPONSE_WRITE)),
    ("database", lambda c: _database(c.name, c.lang)),
    ("template_reread", lambda c: bool(c.names & _TEMPLATE_LOAD)),
    (
        "template_include",
        lambda c: bool(c.names & _TEMPLATE_RENDER and c.leaf_names & _FS_METADATA),
    ),
    (
        "template_reread",
        lambda c: bool(c.names & _TEMPLATE_RENDER and c.leaf_names & _FILE_READ_LEAVES),
    ),
    ("other", lambda c: bool(c.names & _TEMPLATE_RENDER)),  # rendering itself is the work
    # Before the filesystem rule: the metadata scan's leaves are file reads and stats.
    ("package_metadata", lambda c: c.lang == "python" and bool(c.names & _PACKAGE_METADATA)),
    ("etag", lambda c: bool(c.names & _ETAG)),
    ("filesystem", _filesystem_callee),
    ("hash", lambda c: bool(c.names & _HASH)),
    ("subprocess", lambda c: _subprocess(c.name, c.lang)),
    ("regex_compile", _regex_callee),
    ("input_decode", lambda c: bool(c.names & _STREAM_DECODE)),
    ("parse", lambda c: bool(c.names & _PARSE)),
    ("deep_copy", lambda c: bool(c.names & _DEEP_COPY)),
    ("map_build", lambda c: c.lang == "go" and bool(_GO_MAP.match(c.name))),
    ("reflection", _reflection_callee),
    ("path_parse", _path_callee),
    # cgo time with nothing above it saying what it is.
    ("unknown_native", _cgo_callee),
)


def _classify(callee: Mapping[str, Any], lang: str, own_static: Sequence[Mapping[str, Any]]) -> str:
    """The lexicon family of one ``inside`` item; ``other:<name>`` when none fits."""
    c = _read_callee(callee, lang, own_static)
    family = next((family for family, holds in _RULES if holds(c)), "other")
    return f"other:{c.name}" if family == "other" else family


def _database(name: str, lang: str) -> bool:
    last = _last(name)
    if lang == "go":
        return last in _GO_DB_EXEC and _db_package(name)
    if lang == "python":
        receiver = _receiver(name)
        if receiver == "QuerySet":
            return last in _DJANGO_EVALUATE
        return receiver in _PY_DB_RECEIVERS and (last in _PY_DB_VERBS or last in _DB_WRITE_VERBS)
    if lang == "javascript":
        return last in _JS_DB_READS
    if lang == "jvm":
        return _receiver(name) in _JVM_DB_RECEIVERS and (
            last in _JVM_DB_VERBS or last in _DB_WRITE_VERBS
        )
    return False


def _sink_may_be(sink: Any, call: str) -> bool:
    """Whether a loop finding's sink call (``Find``, ``db.Find(&rows)``) may be
    *call*; a finding that names no sink may be any call."""
    return not sink or _last(str(sink).split("(")[0]) == call


def _one_grouped_read(
    group: Sequence[Mapping[str, Any]],
    inside: Sequence[Mapping[str, Any]],
    lang: str,
    symbol: str,
    findings: Sequence[Mapping[str, Any]],
) -> bool:
    """One database call (the family's only one, whatever its share) whose query
    the run saw built with a GROUP BY or an ``IN`` (:data:`_GO_GROUPED`,
    :data:`_PY_GROUPED`), and no database loop in the function repeats it: it
    already loads every item's rows at once."""
    if len(group) != 1 or not any(_grouping_builder(c, lang) for c in inside):
        return False
    return not _own_loop(group, symbol, findings)


def _grouping_builder(callee: Mapping[str, Any], lang: str) -> bool:
    name = str(callee.get("name") or "")
    if callee.get("kind") != "library":
        return False
    if lang == "go":
        return bool(_names(name) & _GO_GROUPED) and _db_package(name)
    return lang == "python" and bool(_names(name) & _PY_GROUPED)


def _subprocess(name: str, lang: str) -> bool:
    if lang == "go":
        return name.startswith("os/exec.")
    if lang == "python":
        return _receiver(name) == "Popen" or (
            name.startswith("subprocess.") and _last(name) in PY_SUBPROC_METHODS
        )
    if lang == "javascript":
        return _last(name) in {"spawnSync", "execSync", "execFileSync"}
    return False


def _database_family(callees: Sequence[Mapping[str, Any]], symbol: str) -> str:
    """``db_write`` when the dominant database call writes or the function itself
    ends a transaction (its SQL time is the commit), else ``db_read``."""
    lead = max(callees, key=lambda c: c.get("share") or 0.0)
    if _last(str(lead.get("name"))) in _DB_WRITE_VERBS:
        return "db_write"
    if _TRANSACTION_NAME.fullmatch(_last(symbol.rsplit("::", 1)[-1])):
        return "db_write"
    return "db_read"


def _matching_is_the_job(symbol: str) -> bool:
    """Whether the function's, a class's or its file's name says it matches its
    input (a lexer, a tokenizer, a parser, a router): the regex is the work."""
    path, _, rest = base_symbol_id(symbol).partition("::")
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return any(w in _MATCHING_JOB_WORDS for name in (stem, *rest.split("::")) for w in words(name))


def _on(finding: Mapping[str, Any], symbol: str) -> bool:
    """Whether *finding* sits in the function *symbol* (its file and bare name)."""
    if not symbol or "::" not in symbol:
        return False
    return (
        finding.get("file_path") == file_of_symbol(symbol)
        and finding.get("function") == symbol.rsplit("::", 1)[-1]
    )


def _agreeing(own_static: Sequence[Mapping[str, Any]], family: str) -> Mapping[str, Any] | None:
    """A static finding on the function whose boundary agrees with *family* (S2)."""
    for finding in own_static:
        if family in _AGREES.get(finding.get("boundary") or "", ()):
            return finding
    return None


def _loop_owner(
    symbol: str, drivers: Sequence[Mapping[str, Any]], findings: Sequence[Mapping[str, Any]]
) -> str | None:
    """The function whose loop repeats *symbol*, from static ``io_in_loop`` findings:
    a loop that calls it, or calls the driver holding most of its time."""
    if not symbol:
        return None
    lead = {d["symbol"] for d in drivers if d.get("symbol") and (d.get("share") or 0) >= DOMINANT}
    callers = {d["symbol"] for d in drivers if d.get("symbol")}
    loops = (f for f in findings if f.get("kind") == "io_in_loop")
    owners = (
        _loop_in_path(f, symbol, callers, lead) or _loop_around_call(f, symbol, callers)
        for f in loops
    )
    return next((owner for owner in owners if owner), None)


def _loop_in_path(
    finding: Mapping[str, Any], symbol: str, callers: set[str], lead: set[str]
) -> str | None:
    """A loop (``path[0]``) that calls this function from one of its measured
    drivers, or calls the driver that holds most of its time."""
    path = finding.get("path") or []
    if len(path) < 2:
        return None
    direct = path[1] == symbol and path[0] in callers
    return path[0] if direct or path[1] in lead else None


def _loop_around_call(finding: Mapping[str, Any], symbol: str, callers: set[str]) -> str | None:
    """A loop written in a driver around a direct call to this function."""
    owner, sink = finding.get("owner"), finding.get("sink")
    if owner not in callers or not sink:
        return None
    return owner if _last(str(sink).split("(")[0]) == symbol.rsplit("::", 1)[-1] else None


# --- words -------------------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    lang: str
    family: str
    callees: Sequence[Mapping[str, Any]]
    share: float
    lead: Mapping[str, Any]
    drivers: Sequence[Mapping[str, Any]]
    loop: str | None

    @property
    def pct(self) -> str:
        return _pct(self.share)

    @property
    def callee(self) -> str:
        return _short(str(self.lead.get("name")))

    @property
    def leaf(self) -> str | None:
        leaf = self.lead.get("leaf")
        return _short(str(leaf["name"])) if isinstance(leaf, Mapping) else None

    @property
    def split(self) -> str:
        """``57% is_file, 15% stat``: the family's callees by share."""
        ranked = sorted(self.callees, key=lambda c: -(c.get("share") or 0.0))
        ranked = [c for c in ranked if (c.get("share") or 0.0) >= 0.01] or ranked[:1]
        return ", ".join(
            f"{_pct(c.get('share') or 0.0)} {_short(str(c.get('name')))}" for c in ranked
        )


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def _fn(symbol: str) -> str:
    """A symbol id's function as people write it: ``Table.insert_all``."""
    return symbol.rsplit("/", 1)[-1].split("::", 1)[-1].replace("::", ".")


def _driver(ctx: _Ctx) -> str | None:
    if not ctx.drivers:
        return None
    top = max(ctx.drivers, key=lambda d: d.get("share") or 0.0)
    name = top.get("symbol") or top.get("name")
    return f"{_fn(str(name))} {_pct(top.get('share') or 0.0)}" if name else None


def _filesystem(ctx: _Ctx) -> tuple[str, str]:
    names = {_last(str(c.get("name"))) for c in ctx.callees}
    leaves = {
        _last(str(c["leaf"]["name"])) for c in ctx.callees if isinstance(c.get("leaf"), Mapping)
    }
    walk = bool(
        (names | leaves) & ({_last(n) for n in _DIR_WALK} | {"walk", "glob", "iglob", "readdir"})
    )
    meta = bool((names | leaves) & ({_last(n) for n in _FS_METADATA} | {"exists", "access"}))
    if walk and meta:
        step = {
            "python": "walk with os.scandir and use each entry's is_file() and stat(), which reuse "
            "the directory read, instead of a Path.is_file or stat per path",
            "go": "walk with filepath.WalkDir and use each DirEntry's Type() instead of an os.Stat per entry",
            "javascript": "read directories with withFileTypes: true and use each Dirent instead of a stat per entry",
        }.get(
            ctx.lang,
            "use the file type the directory listing already returns instead of a stat per entry",
        )
        return (
            f"{ctx.pct} of it is filesystem metadata calls during a directory walk ({ctx.split}): a stat per entry",
            step,
        )
    if walk:
        return (
            f"{ctx.pct} of it walks a directory tree ({ctx.split})",
            "walk once and reuse the listing, or narrow the walk to the directories that matter",
        )
    leaf = f", {ctx.leaf} below it" if ctx.leaf and ctx.leaf != ctx.callee else ""
    return (
        f"{ctx.pct} of it asks the filesystem about a path on every call ({ctx.split}{leaf})",
        "cache the answer for paths that do not change during the run, or check once up front instead of per call",
    )


def _import(ctx: _Ctx) -> tuple[str, str]:
    return (
        f"{ctx.pct} of it is a module import that runs inside the function on its first call ({ctx.callee})",
        "keep that import off this path: import the module only where it is needed; moving it to the "
        "top of the file only moves the cost",
    )


def _template_reread(ctx: _Ctx) -> tuple[str, str]:
    step = {
        "javascript": "turn on the view cache: NODE_ENV=production or app.enable('view cache')",
        "python": "load templates once: keep one template environment with its cache on and auto reload off",
        "go": "parse the templates once at startup and execute the parsed set per request",
    }.get(ctx.lang, "turn on the template cache so each template is read and compiled once")
    leaf = f", {ctx.leaf} leads" if ctx.leaf else ""
    return (
        f"{ctx.pct} of it is the template engine reading and compiling template files per render ({ctx.callee}{leaf})",
        step,
    )


def _template_include(ctx: _Ctx) -> tuple[str, str]:
    return (
        f"{ctx.pct} of it is the template engine ({ctx.callee}) checking include paths on disk "
        f"({ctx.leaf}) at every render, even with compiled templates cached",
        "fewer includes on the hot pages, or a cached path lookup in front of the engine's include "
        "resolution; the template cache does not remove these checks",
    )


def _etag(ctx: _Ctx) -> tuple[str, str]:
    step = (
        "turn ETags off for dynamic responses (app.set('etag', false)) or give app.set('etag', fn) "
        "a cheaper function; a weak ETag still hashes the body"
        if ctx.lang == "javascript"
        else "skip the ETag for dynamic responses or use a cheaper validator"
    )
    leaf = f", {ctx.leaf} below it" if ctx.leaf else ""
    return f"{ctx.pct} of it hashes each response body for an ETag ({ctx.callee}{leaf})", step


def _hash(ctx: _Ctx) -> tuple[str, str]:
    return (
        f"{ctx.pct} of it computes a hash ({ctx.split})",
        "keep the digest when the same input repeats, or use a cheaper non-cryptographic hash where "
        "security does not depend on it",
    )


def _db_read(ctx: _Ctx) -> tuple[str, str]:
    calls = sum(1 for c in ctx.callees if (c.get("share") or 0.0) >= 0.01) or 1
    what = "one ORM or driver call" if calls == 1 else f"{calls} ORM or driver calls"
    cgo = any(
        isinstance(c.get("leaf"), Mapping) and c["leaf"].get("name") == _GO_CGO for c in ctx.callees
    )
    note = (
        "; that time is inside the database driver (runtime.cgocall), which a profile cannot split "
        "into query work and waiting"
        if cgo
        else ""
    )
    why = f"{ctx.pct} of it is database work behind {what} each time it runs ({ctx.split}){note}"
    return why, _db_read_next(ctx)


def _db_read_next(ctx: _Ctx) -> str:
    if ctx.loop:
        return (
            f"it runs once per item of a loop in {_fn(ctx.loop)} (N+1): load the rows for all "
            "items in one query"
        )
    top = max(ctx.drivers, key=lambda d: d.get("share") or 0.0) if ctx.drivers else {}
    top_name = str(top.get("symbol") or top.get("name") or "")
    if _MIDDLEWARE.search(top_name):
        return (
            f"it runs on every request from {_fn(top_name)}: cache the row by key or carry what the "
            "handlers need in the session or token"
        )
    driver = _driver(ctx)
    by = f" (driven by {driver})" if driver else ""
    return (
        f"count the queries one request or run sends through it{by}; when it repeats with the same "
        "arguments, cache the result or load it once with its caller's data"
    )


def _db_write_loop(ctx: _Ctx) -> tuple[str, str]:
    return (
        f"{ctx.pct} of it is a database write or commit per call ({ctx.split})",
        f"it runs once per item of a loop in {_fn(ctx.loop or '')}: wrap the loop in one transaction or "
        "write in larger batches",
    )


def _subprocess_words(ctx: _Ctx) -> tuple[str, str]:
    return (
        f"{ctx.pct} of it starts or waits on another process ({ctx.callee})",
        "count the calls: run one process for many inputs, or cache the output when the same command repeats",
    )


def _regex_label(name: str, lang: str) -> str:
    """A regex callee as a row says it: V8's pattern source cut short, a JVM
    frame by class and method, else the call."""
    if name.startswith("RegExp: "):
        source = name[len("RegExp: ") :]
        return f"RegExp /{source[:40]}.../" if len(source) > 40 else f"RegExp /{source}/"
    owner = _jvm_owner(name) if lang == "jvm" else None
    return f"{owner[0]}.{owner[1]}" if owner else _short(name)


def _regex_match(ctx: _Ctx) -> tuple[str, str]:
    ranked = sorted(ctx.callees, key=lambda c: -(c.get("share") or 0.0))
    split = ", ".join(
        f"{_pct(c.get('share') or 0.0)} {_regex_label(str(c.get('name')), ctx.lang)}"
        for c in ranked
    )
    per_call = [
        c for c in ranked if _regex_run(str(c.get("name")), c.get("kind"), ctx.lang) == "per_call"
    ]
    # The compile-per-call step only when those calls hold most of the regex time.
    if sum(c.get("share") or 0.0 for c in per_call) * 2 >= ctx.share:
        build = {
            "jvm": "a static final Pattern whose matcher each call uses",
            "go": "a package-level regexp.MustCompile for a literal pattern",
        }.get(ctx.lang, "a pattern compiled once")
        return (
            f"{ctx.pct} of it is a regular expression whose pattern is compiled on each call ({split})",
            f"{_regex_label(str(per_call[0].get('name')), ctx.lang)} compiles the pattern each time: "
            f"when the pattern is a constant, compile it once ({build}) and reuse it",
        )
    methods = {
        "javascript": "startsWith or includes",
        "jvm": "startsWith or indexOf",
        "python": "str.startswith or in",
        "go": "strings.HasPrefix or strings.Contains",
    }.get(ctx.lang, "a prefix or substring test")
    state = (
        "; leave a global or sticky pattern's calls unguarded, since lastIndex carries between them"
        if ctx.lang == "javascript"
        else ""
    )
    return (
        f"{ctx.pct} of it is regular expression matching ({split})",
        "settle the common inputs without the regex: a cheap check first (the length, a character "
        f"at a known position, or {methods}) that rejects what the pattern can never match or "
        "accepts the common simple case, and the regex only for the rest; for a small fixed "
        "grammar, a hand-written scan that accepts exactly the same strings can replace it"
        f"{state}",
    )


def _simple(why: str, step: str):
    def words(ctx: _Ctx) -> tuple[str, str]:
        return why.format(pct=ctx.pct, split=ctx.split, callee=ctx.callee), step

    return words


_WORDS = {
    "filesystem": _filesystem,
    "import": _import,
    "template_reread": _template_reread,
    "template_include": _template_include,
    "etag": _etag,
    "hash": _hash,
    "db_read": _db_read,
    "db_write_loop": _db_write_loop,
    "subprocess": _subprocess_words,
    "regex_match": _regex_match,
    "sleep": _simple(
        "{pct} of it sleeps ({callee})",
        "wait on the condition itself (an event, or a poll with backoff) instead of a fixed sleep, or shorten it",
    ),
    "regex_compile": _simple(
        "{pct} of it compiles a regular expression ({callee})",
        "compile the pattern once at module level and reuse it",
    ),
    "parse": _simple(
        "{pct} of it parses serialized data ({split})",
        "parse once and reuse the result, or parse only the part you need",
    ),
    "deep_copy": _simple(
        "{pct} of it deep-copies data ({split})",
        "copy only what changes, or share an immutable value instead of a deep copy per call",
    ),
    "reflection": _simple(
        "{pct} of it is reflection ({split})",
        "resolve the reflected types or methods once and cache them",
    ),
    "map_build": _simple(
        "{pct} of it builds and walks a map per call ({split})",
        "build the map once and reuse it, or look up the keys you need directly",
    ),
    "path_parse": _simple(
        "{pct} of it builds and parses a path object per call ({split})",
        "test the plain string first (str.endswith, os.path) and build the path object only when that passes",
    ),
}

#: Rules whose callee's leaf is part of what the row says.
_LEAF_RULES = frozenset({"filesystem", "template_reread", "template_include", "etag", "db_read"})


__all__ = [
    "DOMINANT",
    "EAGER_DEFAULT",
    "IMPORT_LEADS",
    "PACKAGE_CALLS",
    "STEP_CLASSES",
    "NextStep",
    "eager_default_fact",
    "never_called",
    "next_step",
    "package_calls",
    "static_finding",
    "verify_command",
]
