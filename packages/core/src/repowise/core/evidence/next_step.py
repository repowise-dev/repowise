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
- the function is a thin wrapper named like its dominant callee (the cost
  belongs to its drivers);
- the time sits under ``runtime.cgocall`` and no database call above it says
  what the C code is: a Go profile taken on Windows samples threads inside cgo
  while they wait, so that time is unknown, never a cost claim.

A database call says "batch" only when a static loop sits on the driver chain:
an ``io_in_loop`` finding whose loop calls the function, or calls a driver
that holds most of its time. A static finding on the function itself becomes
the "why" only when its boundary agrees with the dominant callee family.

Ceiling: one leaf per callee (``profile_convert`` keeps the heaviest), so a
template engine whose leaves split between file reads and path checks is read
by whichever leads. Upgrade path: keep the top leaves per callee.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from repowise.core.analysis.health.perf.dialects.python_lexicon import (
    PY_DB_NEEDS_EVIDENCE,
    PY_DB_UNAMBIGUOUS,
    PY_SUBPROC_METHODS,
)
from repowise.core.ingestion.external_systems.io_kind import classify_io_kind
from repowise.core.ingestion.symbol_identity import file_of_symbol

#: Workload classes whose rows lead, so their rows get a step.
STEP_CLASSES = frozenset({"entry_point", "load_test", "production"})
#: Share of the function's time one callee family must hold to be the reason.
DOMINANT = 0.5
#: A leaf names its callee's cost when it holds this much of the callee's time.
LEAF_LEADS = 0.5
#: A second family worth naming beside the dominant one (a walk beside its stats).
ALSO = 0.1

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
        "(*Decoder).Decode",
    }
)
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

_SILENT = frozenset({"wait", "spine", "response_write", "db_write", "unknown_native"})


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
    function or naming it or its drivers in a loop are read, the rest ignored.
    """
    if row.get("workload_class") not in STEP_CLASSES:
        return None
    inside = [c for c in row.get("inside") or () if isinstance(c, Mapping)]
    if not inside:
        return None
    lang = _family(language)
    symbol = row.get("symbol_id") or ""
    findings = list(findings)
    own_static = [f for f in findings if _on(f, symbol)]
    families: dict[str, float] = {}
    members: dict[str, list[Mapping[str, Any]]] = {}
    for callee in inside:
        family = _classify(callee, lang, own_static)
        families[family] = families.get(family, 0.0) + float(callee.get("share") or 0.0)
        members.setdefault(family, []).append(callee)
    family, share = max(families.items(), key=lambda kv: (kv[1], kv[0]))
    if share < DOMINANT or family == "own" or family.startswith("other:"):
        return None
    lead = max(members[family], key=lambda c: c.get("share") or 0.0)
    if _last(symbol.rsplit("::", 1)[-1]) == _last(str(lead.get("name"))):
        return None  # a thin wrapper: the cost belongs to whoever calls it
    drivers = [d for d in row.get("drivers") or () if isinstance(d, Mapping)]
    group = members[family]
    if family == "database":
        family = _database_family(group, symbol)
    loop = _loop_owner(symbol, drivers, findings) if family.startswith("db") else None
    if family == "db_write" and loop and row.get("workload_class") == "entry_point":
        family = "db_write_loop"
    if family in _SILENT:
        return None
    words = _WORDS[family](_Ctx(lang, family, group, share, lead, families, drivers, loop))
    if words is None:
        return None
    why, step = words
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


def _classify(callee: Mapping[str, Any], lang: str, own_static: Sequence[Mapping[str, Any]]) -> str:
    """The lexicon family of one ``inside`` item; ``other:<name>`` when none fits."""
    name = str(callee.get("name") or "")
    kind = callee.get("kind")
    leaf = callee.get("leaf") if isinstance(callee.get("leaf"), Mapping) else None
    leaf_name = str(leaf.get("name")) if leaf else ""
    leaf_leads = bool(leaf) and (leaf.get("share") or 0.0) >= LEAF_LEADS * (
        callee.get("share") or 0.0
    )
    names = _names(name)
    if kind == "own":
        return "own"
    if kind == "repo":
        return f"other:{name}"  # its own row says what it costs
    if kind == "import" or (lang == "python" and names & _IMPORT_PY):
        return "import"
    if kind == "wait":
        if names & _SLEEP:
            return "sleep"
        # A child-process wait, when a static finding says the function spawns one.
        if _last(name) in _CHILD_WAITS and any(
            f.get("boundary") == "subprocess" for f in own_static
        ):
            return "subprocess"
        return "wait"
    if names & _SPINE:
        return "spine"
    if names & _RESPONSE_WRITE:
        return "response_write"
    database = _database(name, lang)
    if database:
        return "database"
    if names & _TEMPLATE_LOAD:
        return "template_reread"
    if names & _TEMPLATE_RENDER:
        if leaf_name and _names(leaf_name) & _FS_METADATA:
            return "template_include"
        if leaf_name and _names(leaf_name) & _FILE_READ_LEAVES:
            return "template_reread"
        return f"other:{name}"  # rendering itself is the work
    if names & _ETAG:
        return "etag"
    fs_leaf = bool(leaf_name) and bool(_names(leaf_name) & (_FS_METADATA | _DIR_WALK))
    if names & (_FS_METADATA | _DIR_WALK | _FS_QUALIFIED) or (leaf_leads and fs_leaf):
        return "filesystem"
    if names & _FS_AMBIGUOUS and fs_leaf:
        return "filesystem"
    if names & _HASH:
        return "hash"
    if _subprocess(name, lang):
        return "subprocess"
    if names & _REGEX_COMPILE or (
        lang == "python" and _last(name) == "_compile" and _last(leaf_name) in _PY_RE_MISS_LEAVES
    ):
        return "regex_compile"
    if names & _PARSE:
        return "parse"
    if names & _DEEP_COPY:
        return "deep_copy"
    if lang == "go" and _GO_MAP.match(name):
        return "map_build"
    if lang == "go" and name.startswith("reflect."):
        return "reflection"
    if lang == "jvm" and _last(name) == "invoke" and _receiver(name) == "Method":
        return "reflection"
    if (
        lang == "python"
        and names & _PATH_PARSE
        and (names & _PATH_INTERNALS or _last(leaf_name) in _PATH_INTERNALS)
    ):
        return "path_parse"
    if lang == "python" and names & _PACKAGE_METADATA:
        return "package_metadata"
    if name == _GO_CGO or (lang == "go" and leaf_name == _GO_CGO and leaf_leads):
        return "unknown_native"  # cgo time with nothing above it saying what it is
    return f"other:{name}"


def _database(name: str, lang: str) -> bool:
    last = _last(name)
    if lang == "go":
        return last in _GO_DB_EXEC and _db_package(name)
    if lang == "python":
        return _receiver(name) in _PY_DB_RECEIVERS and (
            last in _PY_DB_VERBS or last in _DB_WRITE_VERBS
        )
    if lang == "javascript":
        return last in _JS_DB_READS
    if lang == "jvm":
        return _receiver(name) in _JVM_DB_RECEIVERS and (
            last in _JVM_DB_VERBS or last in _DB_WRITE_VERBS
        )
    return False


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
    name = symbol.rsplit("::", 1)[-1]
    for finding in findings:
        if finding.get("kind") != "io_in_loop":
            continue
        path = finding.get("path") or []
        # A loop that calls this function from one of its measured drivers, or
        # calls the driver that holds most of its time.
        if len(path) >= 2 and ((path[1] == symbol and path[0] in callers) or path[1] in lead):
            return path[0]
        # A loop written in a driver around a direct call to this function.
        owner, sink = finding.get("owner"), finding.get("sink")
        if owner in callers and sink and _last(str(sink).split("(")[0]) == name:
            return owner
    return None


# --- words -------------------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    lang: str
    family: str
    callees: Sequence[Mapping[str, Any]]
    share: float
    lead: Mapping[str, Any]
    families: Mapping[str, float]
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
    if ctx.loop:
        return why, (
            f"it runs once per item of a loop in {_fn(ctx.loop)} (N+1): load the rows for all items in one query"
        )
    driver = _driver(ctx)
    top = max(ctx.drivers, key=lambda d: d.get("share") or 0.0) if ctx.drivers else None
    if top and _MIDDLEWARE.search(str(top.get("symbol") or top.get("name") or "")):
        return why, (
            f"it runs on every request from {_fn(str(top.get('symbol') or top.get('name')))}: cache the "
            "row by key or carry what the handlers need in the session or token"
        )
    by = f" (driven by {driver})" if driver else ""
    return why, (
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
    "package_metadata": _simple(
        "{pct} of it reads installed package metadata ({callee}), which scans every installed distribution",
        "run it only on the commands that need it, and at most once per process",
    ),
}

#: Rules whose callee's leaf is part of what the row says.
_LEAF_RULES = frozenset({"filesystem", "template_reread", "template_include", "etag", "db_read"})


__all__ = [
    "DOMINANT",
    "STEP_CLASSES",
    "NextStep",
    "next_step",
    "static_finding",
    "verify_command",
]
