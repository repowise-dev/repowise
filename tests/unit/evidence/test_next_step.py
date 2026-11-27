"""The next step for a measured row, from its callees, leaves and drivers.

Rows are the shapes ``profile_convert`` writes for the gate profiles (a CLI
status command, an Express app and a Go service under load, a Python CLI that
inserts a CSV), trimmed to what the rule reads.
"""

from __future__ import annotations

from typing import Any

import pytest

from repowise.core.evidence.next_step import next_step, static_finding, verify_command


def _row(
    symbol: str,
    inside: list[dict[str, Any]],
    drivers: list[dict[str, Any]] | None = None,
    workload: str = "app",
    klass: str = "load_test",
) -> dict[str, Any]:
    return {
        "symbol_id": symbol,
        "inside": inside,
        "drivers": drivers or [],
        "workload_name": workload,
        "workload_class": klass,
    }


def _c(
    name: str, kind: str, share: float, leaf: tuple[str, str, float] | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "kind": kind, "share": share}
    if leaf:
        item["leaf"] = {"name": leaf[0], "kind": leaf[1], "share": leaf[2]}
    return item


GORM_FIRST = _c("gorm.io/gorm.(*DB).First", "library", 0.999, ("runtime.cgocall", "native", 0.99))


def test_stat_per_entry_in_a_directory_walk_names_scandir() -> None:
    row = _row(
        "cli/status_cmd.py::_index_storage_bytes",
        [
            _c("is_file", "library", 0.569, ("stat", "native", 0.569)),
            _c("select_recursive", "library", 0.249, ("select_wildcard", "library", 0.211)),
            _c("stat", "library", 0.146, ("stat", "native", 0.146)),
        ],
        workload="repowise status",
        klass="entry_point",
    )
    step = next_step(row, "python")
    assert step is not None and step.rule == "filesystem"
    assert "directory walk" in step.why and "57% is_file" in step.why
    assert "os.scandir" in step.next
    assert step.verify.startswith("repowise perf measure --save before -- repowise status")


def test_an_import_inside_the_function_is_kept_off_the_path() -> None:
    row = _row(
        "state.py::read_index_synced_at",
        [
            _c("_find_and_load", "import", 0.816, ("open_code", "native", 0.162)),
            _c("state.py::synced_at_iso", "repo", 0.116),
        ],
        klass="entry_point",
    )
    step = next_step(row, "python")
    assert step.rule == "import" and "only moves the cost" in step.next


def test_template_engine_reading_files_per_render_turns_on_the_view_cache() -> None:
    row = _row(
        "lib/view.js::View::render",
        [
            _c("exports.renderFile", "library", 0.80, ("open", "native", 0.186)),
            _c("lib/response.js::render", "repo", 0.197),
        ],
    )
    step = next_step(row, "javascript")
    assert step.rule == "template_reread" and step.leaf == "open"
    assert "app.enable('view cache')" in step.next


def test_template_include_checks_never_say_turn_on_the_cache() -> None:
    # The view cache is on: ejs still checks each include path with existsSync.
    row = _row(
        "lib/view.js::View::render",
        [
            _c("exports.renderFile", "library", 0.552, ("existsSync", "native", 0.397)),
            _c("lib/response.js::render", "repo", 0.441),
        ],
    )
    step = next_step(row, "javascript")
    assert step.rule == "template_include" and step.leaf == "existsSync"
    assert "include" in step.why
    assert "turn on" not in step.next


def test_etag_hash_per_response() -> None:
    row = _row(
        "lib/utils.js::createETagGenerator",
        [_c("etag", "library", 0.991, ("Hash", "library", 0.42))],
    )
    step = next_step(row, "javascript")
    assert step.rule == "etag" and "app.set('etag', false)" in step.next


def test_stat_per_call_keyed_on_the_leaf_or_the_callee() -> None:
    by_callee = _row(
        "lib/view.js::tryStat", [_c("statSync", "library", 0.993, ("stat", "native", 0.757))]
    )
    assert next_step(by_callee, "javascript").rule == "filesystem"
    # A library callee whose time is a stat underneath.
    by_leaf = _row("app.js::load", [_c("lookup", "library", 0.9, ("lstatSync", "native", 0.8))])
    assert next_step(by_leaf, "javascript").rule == "filesystem"


@pytest.mark.parametrize(
    ("symbol", "inside"),
    [
        # The router and middleware chain, even with a stat among its leaves.
        (
            "lib/application.js::handle",
            [
                _c("handle", "library", 0.922, ("stat", "native", 0.9)),
                _c("(own code)", "own", 0.07),
            ],
        ),
        # A socket write.
        (
            "lib/response.js::send",
            [
                _c("end", "library", 0.735, ("writev", "native", 0.337)),
                _c("lib/utils.js::createETagGenerator", "repo", 0.069),
            ],
        ),
        # gin's spine.
        (
            "gin.go::Engine::ServeHTTP",
            [_c("github.com/gin-gonic/gin.(*Context).Next", "library", 0.95)],
        ),
        # An event loop the function waits on.
        ("cli/console.py::run_async", [_c("run", "wait", 1.0, ("select", "native", 0.9))]),
        # Its own code.
        (
            "utils.py::ValueTracker::get_tests",
            [_c("(own code)", "own", 0.492), _c("str.startswith", "native", 0.445)],
        ),
        # No family holds half.
        (
            "traverser.py::is_candidate_source_path",
            [
                _c("traverser.py::in_blocked_dir", "repo", 0.239),
                _c("match_file", "library", 0.174),
                _c("flow.py::is_legacy_flow_path", "repo", 0.174),
            ],
        ),
        # cgo time with nothing above it saying what it is.
        ("db.go::load", [_c("runtime.cgocall", "native", 0.97)]),
        (
            "db.go::load",
            [
                _c(
                    "github.com/x/sqlite3.(*conn).step",
                    "library",
                    0.97,
                    ("runtime.cgocall", "native", 0.96),
                )
            ],
        ),
    ],
)
def test_silence(symbol: str, inside: list[dict[str, Any]]) -> None:
    language = (
        "go"
        if symbol.endswith((".go",)) or ".go::" in symbol
        else ("javascript" if ".js::" in symbol else "python")
    )
    assert next_step(_row(symbol, inside), language) is None


def test_orm_read_per_call_says_count_never_batch_without_a_loop() -> None:
    row = _row(
        "users/models.go::UserModel::isFollowing",
        [GORM_FIRST, _c("gorm.io/gorm.(*DB).Where", "library", 0.001)],
        [
            {
                "name": "users.serializers.ProfileSerializer.Response",
                "share": 1.0,
                "symbol": "users/serializers.go::ProfileSerializer::Response",
            }
        ],
    )
    step = next_step(row, "go")
    assert step.rule == "db_read"
    assert "one ORM or driver call" in step.why and "runtime.cgocall" in step.why
    assert "cannot split" in step.why  # never a CPU cost claim
    assert "batch" not in step.next and "ProfileSerializer.Response 100%" in step.next


def test_orm_read_in_a_loop_on_the_driver_chain_says_batch() -> None:
    drivers = [
        {
            "name": "Response",
            "share": 1.0,
            "symbol": "users/serializers.go::ProfileSerializer::Response",
        }
    ]
    loop = static_finding(
        "articles/serializers.go",
        "Response",
        "io_in_loop",
        "a database call ...",
        {
            "boundary_kind": "db",
            "cross_function": True,
            "path": [
                "articles/serializers.go::ArticlesSerializer::Response",
                "users/serializers.go::ProfileSerializer::Response",
                "users/models.go::UserModel::isFollowing",
            ],
        },
    )
    step = next_step(
        _row("users/models.go::UserModel::isFollowing", [GORM_FIRST], drivers), "go", [loop]
    )
    assert "N+1" in step.next and "ArticlesSerializer.Response" in step.next
    # A loop in some other caller, not a measured driver, does not count.
    other = {**loop, "path": ["x.go::Other", "users/models.go::UserModel::isFollowing"]}
    assert (
        "N+1"
        not in next_step(
            _row("users/models.go::UserModel::isFollowing", [GORM_FIRST], drivers), "go", [other]
        ).next
    )


def test_orm_read_from_middleware_runs_per_request() -> None:
    row = _row(
        "users/middlewares.go::UpdateContextUserModel",
        [GORM_FIRST],
        [
            {
                "name": "AuthMiddleware",
                "share": 1.0,
                "symbol": "users/middlewares.go::AuthMiddleware",
            }
        ],
    )
    assert "every request from AuthMiddleware" in next_step(row, "go").next


def test_two_queries_name_both_and_builders_are_not_queries() -> None:
    row = _row(
        "articles/models.go::FindManyArticle",
        [
            _c("gorm.io/gorm.(*DB).Find", "library", 0.85, ("runtime.cgocall", "native", 0.76)),
            _c("gorm.io/gorm.(*DB).Count", "library", 0.149, ("runtime.cgocall", "native", 0.14)),
            _c("gorm.io/gorm.(*DB).Model", "library", 0.001),
        ],
    )
    step = next_step(row, "go")
    assert "2 ORM or driver calls" in step.why and "Count" in step.why and "Model" not in step.why


def test_a_write_per_request_is_silent() -> None:
    row = _row(
        "articles/models.go::SaveOne",
        [_c("gorm.io/gorm.(*DB).Save", "library", 1.0, ("runtime.cgocall", "native", 0.98))],
        [
            {
                "name": "ArticleCommentCreate",
                "share": 1.0,
                "symbol": "articles/routers.go::ArticleCommentCreate",
            }
        ],
    )
    assert next_step(row, "go") is None


def test_a_commit_per_item_of_a_loop_in_a_cli_run_says_one_transaction() -> None:
    drivers = [{"name": "insert_chunk", "share": 0.999, "symbol": "db.py::Table::insert_chunk"}]
    loop = static_finding(
        "db.py",
        "insert_all",
        "io_in_loop",
        "a database call is reached once per loop iteration",
        {
            "boundary_kind": "db",
            "cross_function": True,
            "path": [
                "db.py::Table::insert_all",
                "db.py::Table::insert_chunk",
                "db.py::Table::add_column",
            ],
        },
    )
    row = _row(
        "db.py::Database::atomic",
        [_c("Connection.execute", "native", 1.0)],
        drivers,
        workload="sqlite-utils insert",
        klass="entry_point",
    )
    step = next_step(row, "python", [loop])
    assert step.rule == "db_write_loop"
    assert "Table.insert_all" in step.next and "one transaction" in step.next
    # Without the loop, or under load (each request commits), it stays silent.
    assert next_step(row, "python") is None
    assert next_step({**row, "workload_class": "load_test"}, "python", [loop]) is None


def test_a_thin_wrapper_named_like_its_callee_is_silent() -> None:
    row = _row(
        "db.py::Database::execute", [_c("Connection.execute", "native", 1.0)], klass="entry_point"
    )
    assert next_step(row, "python") is None


def test_a_static_finding_writes_why_only_when_it_agrees_with_the_callee() -> None:
    sub = static_finding(
        "git_cli.py",
        "run_git",
        "hot_path_sync_io",
        "a blocking subprocess spawn",
        {"boundary_kind": "subprocess"},
    )
    row = _row(
        "git_cli.py::run_git",
        [_c("run", "wait", 1.0, ("_ThreadHandle.join", "native", 0.85))],
        klass="entry_point",
    )
    # A wait is silent unless a static finding says the function spawns a process.
    assert next_step(row, "python") is None
    step = next_step(row, "python", [sub])
    assert step.rule == "subprocess" and step.static == "hot_path_sync_io"
    assert step.why.startswith("a blocking subprocess spawn (measured: 100%")
    # A finding about something else on the same function stays out of the row.
    other = static_finding(
        "cli/status_cmd.py",
        "_index_storage_bytes",
        "io_in_loop",
        "a db call",
        {"boundary_kind": "db"},
    )
    fs_row = _row(
        "cli/status_cmd.py::_index_storage_bytes",
        [_c("is_file", "library", 0.9)],
        klass="entry_point",
    )
    assert next_step(fs_row, "python", [other]).static is None


@pytest.mark.parametrize(
    ("symbol", "language", "inside", "rule"),
    [
        (
            "context.go::getMapFromFormData",
            "go",
            [
                _c("runtime.mapassign_faststr", "native", 0.33),
                _c("runtime.mapIterNext", "native", 0.27),
                _c("runtime.mapIterStart", "native", 0.12),
            ],
            "map_build",
        ),
        (
            "flow.py::is_legacy_flow_path",
            "python",
            [_c("suffix", "library", 0.875, ("_tail", "library", 0.375))],
            "path_parse",
        ),
        ("conf.go::load", "go", [_c("encoding/json.Unmarshal", "library", 0.8)], "parse"),
        ("conf.go::load", "go", [_c("regexp.MustCompile", "library", 0.7)], "regex_compile"),
        (
            "conf.py::load",
            "python",
            [_c("loads", "library", 0.8, ("raw_decode", "native", 0.7))],
            "parse",
        ),
        ("conf.py::snapshot", "python", [_c("deepcopy", "library", 0.8)], "deep_copy"),
        ("poll.py::wait_ready", "python", [_c("sleep", "wait", 0.9)], "sleep"),
        ("run.go::build", "go", [_c("os/exec.(*Cmd).Run", "library", 0.9)], "subprocess"),
        ("app.py::rows", "python", [_c("Session.execute", "library", 0.9)], "db_read"),
    ],
)
def test_lexicon_entries(
    symbol: str, language: str, inside: list[dict[str, Any]], rule: str
) -> None:
    step = next_step(_row(symbol, inside, klass="entry_point"), language)
    assert step is not None and step.rule == rule


# Irreducible work (F-NS-2, F-NS-4): decoding the run's input, plugin discovery.


def test_a_stream_decoder_reading_the_input_is_silent() -> None:
    # A CLI's input iterator: a json.Decoder over stdin returns the next value
    # each call (the run's input, read once); "parse once" would be wrong.
    row = _row(
        "cli/inputs.go::newJSONInputIter",
        [
            _c(
                "encoding/json.(*Decoder).Decode",
                "library",
                1.0,
                ("encoding/json.(*Decoder).readValue", "library", 0.1183),
            )
        ],
        [{"name": "Next", "share": 1.0, "symbol": "cli/inputs.go::jsonInputIter::Next"}],
        klass="entry_point",
    )
    assert next_step(row, "go") is None
    for decoder in ("encoding/xml.(*Decoder).Decode", "gopkg.in/yaml.v3.(*Decoder).Decode"):
        assert next_step(_row("api.go::bind", [_c(decoder, "library", 0.9)]), "go") is None


def test_parsing_a_value_the_function_holds_still_says_parse_once() -> None:
    for symbol, language, callee in (
        ("conf.go::load", "go", "encoding/json.Unmarshal"),
        ("conf.go::load", "go", "encoding/xml.Unmarshal"),
        ("conf.py::load", "python", "loads"),
        ("conf.py::load", "python", "safe_load"),
        # load_all reads a stream or a held string alike: a parse.
        ("conf.py::documents", "python", "load_all"),
    ):
        step = next_step(_row(symbol, [_c(callee, "library", 0.8)], klass="entry_point"), language)
        assert step is not None and step.rule == "parse", callee
        assert "parse once" in step.next


def test_plugin_discovery_scanning_package_metadata_is_silent() -> None:
    # Plugin discovery: entry_points scans installed metadata once per process,
    # on the commands that load plugins, so neither half of a step applies.
    row = _row(
        "registry/plugins.py::_discover",
        [_c("entry_points", "library", 1.0, ("open", "library", 0.204))],
        [{"name": "load_plugins", "share": 1.0, "symbol": "registry/plugins.py::load_plugins"}],
        workload="app status",
        klass="entry_point",
    )
    assert next_step(row, "python") is None
    # Its leaves are file stats: still the metadata scan, never a filesystem step.
    stat_leaf = _row(
        "registry/plugins.py::_discover",
        [_c("entry_points", "library", 0.9, ("stat", "native", 0.75))],
        klass="entry_point",
    )
    assert next_step(stat_leaf, "python") is None


# One grouped read (F-NS-3).


def _counts_row(inside: list[dict[str, Any]]) -> dict[str, Any]:
    return _row(
        "articles/models.go::BatchGetFavoriteCounts",
        inside,
        [
            {
                "name": "ArticlesSerializer.Response",
                "share": 1.0,
                "symbol": "articles/serializers.go::ArticlesSerializer::Response",
            }
        ],
    )


GORM_FIND = _c("gorm.io/gorm.(*DB).Find", "library", 0.9956, ("runtime.cgocall", "native", 0.9356))
GORM_GROUP = _c(
    "gorm.io/gorm.(*DB).Group",
    "library",
    0.0022,
    ("internal/runtime/maps.(*Map).getWithoutKeySmallFastStr", "library", 0.0022),
)


def test_one_grouped_read_is_silent() -> None:
    # A count per article for a whole page in one GROUP BY: already one query.
    where = _c("gorm.io/gorm.(*DB).Where", "library", 0.0022)
    assert next_step(_counts_row([GORM_FIND, GORM_GROUP, where]), "go") is None
    py = _row(
        "app.py::counts",
        [_c("Session.execute", "library", 0.9), _c("GenerativeSelect.group_by", "library", 0.01)],
    )
    assert next_step(py, "python") is None
    in_list = _row(
        "app.py::by_ids",
        [_c("Session.execute", "library", 0.9), _c("ColumnOperators.in_", "library", 0.004)],
    )
    assert next_step(in_list, "python") is None


def test_a_read_without_a_grouping_builder_keeps_its_step() -> None:
    # The run did not sample a builder: nothing says the query is grouped.
    assert next_step(_counts_row([GORM_FIND]), "go").rule == "db_read"
    # A Group from a package that is not a database driver says nothing.
    other = _c("example.com/stats.(*Agg).Group", "library", 0.01)
    assert next_step(_counts_row([GORM_FIND, other]), "go").rule == "db_read"
    # A dataframe group_by, or the repo's own group_by, is no query builder.
    for name, kind in (("DataFrame.group_by", "library"), ("report.group_by", "repo")):
        row = _row("report.py::rows", [_c("Session.execute", "library", 0.9), _c(name, kind, 0.03)])
        assert next_step(row, "python").rule == "db_read", name


def test_a_grouped_read_beside_another_query_or_in_a_loop_keeps_its_step() -> None:
    count = _c("gorm.io/gorm.(*DB).Count", "library", 0.2, ("runtime.cgocall", "native", 0.2))
    find = _c("gorm.io/gorm.(*DB).Find", "library", 0.78, ("runtime.cgocall", "native", 0.7))
    step = next_step(_counts_row([find, count, GORM_GROUP]), "go")
    assert step.rule == "db_read" and "2 ORM or driver calls" in step.why
    # A second read, however small its share, is a second query.
    tiny = _c("gorm.io/gorm.(*DB).Count", "library", 0.005)
    assert next_step(_counts_row([GORM_FIND, GORM_GROUP, tiny]), "go").rule == "db_read"
    # A loop in the function itself repeats the grouped read.
    own = static_finding(
        "articles/models.go",
        "BatchGetFavoriteCounts",
        "io_in_loop",
        "a database call runs once per loop iteration",
        {"boundary_kind": "db", "sink_call": "Find"},
    )
    assert next_step(_counts_row([GORM_FIND, GORM_GROUP]), "go", [own]).rule == "db_read"
    # A loop in the function over some other I/O does not repeat the read.
    http = {**own, "boundary": "network", "sink": "http.Get"}
    assert next_step(_counts_row([GORM_FIND, GORM_GROUP]), "go", [http]) is None
    loop = static_finding(
        "articles/serializers.go",
        "Response",
        "io_in_loop",
        "a database call ...",
        {
            "boundary_kind": "db",
            "path": [
                "articles/serializers.go::ArticlesSerializer::Response",
                "articles/models.go::BatchGetFavoriteCounts",
            ],
        },
    )
    step = next_step(_counts_row([GORM_FIND, GORM_GROUP]), "go", [loop])
    assert step.rule == "db_read" and "N+1" in step.next


def test_only_leading_workloads_get_a_step() -> None:
    inside = [_c("statSync", "library", 0.99)]
    for klass in ("unit_test", "benchmark", None):
        assert next_step(_row("a.js::f", inside, klass=klass), "javascript") is None
    assert next_step(_row("a.js::f", [], klass="load_test"), "javascript") is None


def test_verify_command_per_workload_class() -> None:
    assert verify_command("repowise status", "entry_point") == (
        "repowise perf measure --save before -- repowise status before the change, then --against before after it"
    )
    assert '--workload "api load" --class load_test' in verify_command("api load", "load_test")
    assert (
        verify_command("checkout", "production") == "compare this row in the next checkout window"
    )
    assert verify_command("pytest", "unit_test") is None
    assert verify_command(None, "entry_point") is None


def test_as_dict_drops_empty_fields() -> None:
    step = next_step(
        _row("lib/view.js::tryStat", [_c("statSync", "library", 0.99)], workload=""), "javascript"
    )
    assert set(step.as_dict()) == {"rule", "why", "next", "callee", "share"}


@pytest.mark.parametrize(
    ("name", "leaf"),
    [
        ("walk", None),  # ast.walk
        ("exists", None),  # Redis.exists
        ("Session.get", None),  # an HTTP session
        ("glob", ("fnmatch", "library", 0.5)),
    ],
)
def test_names_other_libraries_spell_too_are_not_filesystem_or_database(
    name: str, leaf: tuple[str, str, float] | None
) -> None:
    assert (
        next_step(_row("a.py::f", [_c(name, "library", 0.8, leaf)], klass="entry_point"), "python")
        is None
    )


def test_an_ambiguous_walk_counts_when_its_stacks_end_in_the_filesystem() -> None:
    row = _row(
        "a.py::f", [_c("walk", "library", 0.8, ("scandir", "native", 0.6))], klass="entry_point"
    )
    assert next_step(row, "python").rule == "filesystem"


def test_only_a_loop_finding_makes_a_database_read_an_n_plus_one() -> None:
    drivers = [{"name": "hold", "share": 1.0, "symbol": "a.py::hold"}]
    lock = static_finding(
        "a.py",
        "hold",
        "blocking_io_under_lock",
        "io under a lock",
        {"boundary_kind": "db", "path": ["a.py::hold", "a.py::read"]},
    )
    row = _row("a.py::read", [_c("Session.execute", "library", 0.9)], drivers, klass="entry_point")
    assert "N+1" not in next_step(row, "python", [lock]).next
    assert "N+1" in next_step(row, "python", [{**lock, "kind": "io_in_loop"}]).next


def test_a_lock_wait_stays_silent_beside_a_subprocess_finding() -> None:
    sub = static_finding(
        "a.py",
        "f",
        "hot_path_sync_io",
        "a blocking subprocess spawn",
        {"boundary_kind": "subprocess"},
    )
    row = _row("a.py::f", [_c("RLock.acquire", "wait", 0.9)], klass="entry_point")
    assert next_step(row, "python", [sub]) is None


def test_only_a_transaction_named_function_reads_its_sql_as_a_commit() -> None:
    drivers = [{"name": "each", "share": 1.0, "symbol": "a.py::each"}]
    loop = static_finding(
        "a.py",
        "run",
        "io_in_loop",
        "a database call per item",
        {"boundary_kind": "db", "path": ["a.py::run", "a.py::each"]},
    )
    read = _row(
        "a.py::get_transaction",
        [_c("Session.execute", "library", 0.9)],
        drivers,
        klass="entry_point",
    )
    assert next_step(read, "python", [loop]).rule == "db_read"
    commit = _row(
        "a.py::atomic", [_c("Session.execute", "library", 0.9)], drivers, klass="entry_point"
    )
    assert next_step(commit, "python", [loop]).rule == "db_write_loop"


def test_a_loop_in_a_driver_around_a_direct_call_is_an_n_plus_one() -> None:
    drivers = [{"name": "each", "share": 1.0, "symbol": "a.py::each"}]
    direct = static_finding(
        "a.py",
        "each",
        "io_in_loop",
        "a database call runs once per loop iteration",
        {"boundary_kind": "db", "sink_call": "self.db.load_row", "role_owner": "a.py::each"},
    )
    row = _row(
        "a.py::load_row", [_c("Session.execute", "library", 0.9)], drivers, klass="entry_point"
    )
    assert "loop in each (N+1)" in next_step(row, "python", [direct]).next


# Django ORM: the shapes a Django site's page load writes (a template tag that
# lists menu pages, a model method a template calls), trimmed to what the rule reads.
DJANGO_ITER = _c(
    "QuerySet.__iter__",
    "library",
    0.938,
    ("DatabaseErrorWrapper.__call__.<locals>.inner", "library", 0.395),
)


def test_django_queryset_evaluation_is_database_work() -> None:
    row = _row(
        "app/templatetags/navigation_tags.py::top_menu",
        [DJANGO_ITER, _c("MP_Node.get_children", "library", 0.049)],
        [{"name": "InclusionNode.render", "share": 0.954}],
    )
    step = next_step(row, "python")
    assert step is not None and step.rule == "db_read"
    assert "94% QuerySet.__iter__" in step.why
    assert "InclusionNode.render 95%" in step.next and "cache the result" in step.next
    assert "N+1" not in step.next  # no loop in the row says per item
    for name in ("QuerySet.__len__", "QuerySet.__bool__", "QuerySet.iterator"):
        assert next_step(_row("a.py::f", [_c(name, "library", 0.9)]), "python").rule == "db_read"


@pytest.mark.parametrize(
    "inside",
    [
        # One row per render (a footer lookup): the request's own work.
        [_c("QuerySet.first", "library", 0.946, ("SQLiteCursorWrapper.execute", "library", 0.635))],
        [_c("QuerySet.get", "library", 0.9), _c("QuerySet.count", "library", 0.05)],
        # Building a queryset runs nothing.
        [_c("QuerySet.filter", "library", 0.6), _c("QuerySet.all", "library", 0.3)],
        # A paginator counting rows, with the cursor only as its leaf.
        [_c("Paginator.page", "library", 0.889, ("SQLiteCursorWrapper.execute", "library", 0.556))],
        # Raw SQL through Django can be a write: nothing in the row says which.
        [_c("RawQuerySet.__iter__", "library", 0.9)],
        [
            _c(
                "CursorWrapper.execute",
                "library",
                0.9,
                ("SQLiteCursorWrapper.execute", "library", 0.8),
            )
        ],
    ],
)
def test_django_single_row_lookups_builders_and_raw_sql_stay_silent(
    inside: list[dict[str, Any]],
) -> None:
    row = _row("app/views.py::page", inside, [{"name": "InclusionNode.render", "share": 1.0}])
    assert next_step(row, "python") is None


def test_a_django_queryset_in_a_loop_on_the_driver_chain_says_batch() -> None:
    drivers = [{"name": "listing", "share": 1.0, "symbol": "app/models.py::Index::listing"}]
    loop = static_finding(
        "app/models.py",
        "listing",
        "io_in_loop",
        "a database call per item",
        {
            "boundary_kind": "db",
            "path": ["app/models.py::Index::listing", "app/models.py::Post::tags"],
        },
    )
    row = _row("app/models.py::Post::tags", [DJANGO_ITER], drivers)
    assert "loop in Index.listing (N+1)" in next_step(row, "python", [loop]).next


# --- a regular expression run per call ---------------------------------------

_TIMESTAMP_FULL = (
    r"RegExp: ^([0-9][0-9][0-9][0-9])-([0-9][0-9]?)-([0-9][0-9]?)(?:[Tt]|[ \t]+)"
    r"([0-9][0-9]?):([0-9][0-9]):([0-9][0-9])(?:\.([0-9]*))?$"
)


def _regex_row(symbol: str, callee: str, share: float = 0.9, **kw: Any) -> dict[str, Any]:
    kind = kw.pop("kind", "library")
    leaf = kw.pop("leaf", None)
    return _row(symbol, [_c(callee, kind, share, leaf)], klass="entry_point")


def test_a_number_writer_spending_its_time_in_a_matcher_tests_a_cheap_condition_first() -> None:
    # A JSON writer that validates every number's string form against a pattern (JFR).
    row = _row(
        "src/main/java/stream/JsonWriter.java::JsonWriter::value#1(Number)",
        [
            _c("java.util.regex.Matcher.matches()", "library", 0.6683),
            _c(
                "stream.JsonWriter.JsonWriter.writeDeferredName",
                "repo",
                0.2284,
                ("java.lang.AbstractStringBuilder.ensureCapacityInternal(int)", "library", 0.17),
            ),
        ],
        klass="entry_point",
    )
    step = next_step(row, "java")
    assert step is not None and step.rule == "regex_match"
    assert (
        step.why == "67% of it is regular expression matching (67% java.util.regex.Matcher.matches)"
    )
    assert step.next.startswith("settle the common inputs without the regex: a cheap check first")
    assert "startsWith or indexOf" in step.next
    assert "hand-written scan that accepts exactly the same strings" in step.next


def test_two_patterns_per_call_add_up_and_print_short() -> None:
    # A YAML scalar resolver trying two date patterns on every plain scalar (V8).
    row = _row(
        "lib/type/timestamp.js::resolveYamlTimestamp",
        [
            _c("(own code)", "own", 0.4421),
            _c(_TIMESTAMP_FULL, "library", 0.3688),
            _c("RegExp: ^([0-9][0-9][0-9][0-9])-([0-9][0-9])-([0-9][0-9])$", "native", 0.189),
        ],
        klass="entry_point",
    )
    step = next_step(row, "javascript")
    assert step is not None and step.rule == "regex_match" and step.share == 0.5578
    assert "37% RegExp /^([0-9][0-9][0-9][0-9])-([0-9][0-9]?)-([.../" in step.why
    assert "19% RegExp /^([0-9][0-9][0-9][0-9])-([0-9][0-9])-([0.../" in step.why
    assert "startsWith or includes" in step.next and "lastIndex" in step.next


@pytest.mark.parametrize(
    ("language", "callee", "kind", "methods"),
    [
        ("go", "regexp.(*Regexp).MatchString", "library", "strings.HasPrefix"),
        ("go", "regexp.(*Regexp).ReplaceAllString (inline)", "library", "strings.HasPrefix"),
        ("java", "java/util/regex/Matcher.find", "library", "indexOf"),
        ("java", "java/util/regex/Matcher.matches()Z_[j]", "library", "indexOf"),
        ("kotlin", "kotlin.text.Regex.containsMatchIn(CharSequence)", "library", "indexOf"),
        ("python", "Pattern.search", "native", "str.startswith"),
        ("python", "<method 'fullmatch' of 're.Pattern' objects>", "native", "str.startswith"),
        # A pattern source ending in a word another family holds (``end``).
        ("typescript", r"RegExp: ^\/api\/end", "native", "startsWith"),
    ],
)
def test_regex_match_per_language(language: str, callee: str, kind: str, methods: str) -> None:
    step = next_step(_regex_row("src/util.x::is_valid_id", callee, kind=kind), language)
    assert step is not None and step.rule == "regex_match"
    assert methods in step.next


@pytest.mark.parametrize(
    ("language", "callee", "leaf", "build"),
    [
        ("java", "java.lang.String.matches(String)", None, "a static final Pattern"),
        # The compile is the engine's own leaf: still the pattern built per call.
        (
            "java",
            "java/lang/String.replaceAll",
            ("java.util.regex.Pattern.compile(String)", "library", 0.7),
            "a static final Pattern",
        ),
        (
            "go",
            "regexp.MatchString",
            ("regexp.compile", "library", 0.7),
            "a package-level regexp.MustCompile for a literal pattern",
        ),
    ],
)
def test_a_call_that_compiles_its_pattern_each_time_says_build_it_once(
    language: str, callee: str, leaf: tuple[str, str, float] | None, build: str
) -> None:
    row = _regex_row("src/util.x::is_valid_id", callee, leaf=leaf)
    step = next_step(row, language)
    assert step is not None and step.rule == "regex_match"
    assert "whose pattern is compiled on each call" in step.why
    assert "when the pattern is a constant" in step.next and build in step.next


@pytest.mark.parametrize(
    ("symbol", "language", "callee"),
    [
        ("src/lexer.js::next", "javascript", "RegExp: ^[a-z]+"),
        ("src/expr.lexer.ts::next", "typescript", "RegExp: ^[a-z]+"),
        ("src/util.java::Lexer::next", "java", "java.util.regex.Matcher.lookingAt()"),
        ("src/read.go::readToken", "go", "regexp.(*Regexp).FindStringIndex"),
        ("app/views.py::parse_header", "python", "Pattern.match"),
        (
            "src/Input.java::Input::parse#1(java.lang.String)",
            "java",
            "java.util.regex.Matcher.matches()",
        ),
        ("src/http.router.ts::accept", "typescript", "RegExp: ^\\/users"),
    ],
)
def test_a_function_whose_job_is_matching_stays_silent(
    symbol: str, language: str, callee: str
) -> None:
    kind = "native" if language == "python" else "library"
    assert next_step(_regex_row(symbol, callee, kind=kind), language) is None
    # The same call in a function whose name says nothing about matching fires.
    neutral = next_step(_regex_row("src/util.x::is_valid_id", callee, kind=kind), language)
    assert neutral is not None and neutral.rule == "regex_match"


@pytest.mark.parametrize(
    "symbol",
    [
        "src/ComplexValue.java::ComplexValue::write",  # ``lex`` inside a word
        "src/Out.java::Out::value#1(Token)",  # a parameter type
    ],
)
def test_matching_words_count_only_as_whole_words_of_the_name(symbol: str) -> None:
    step = next_step(_regex_row(symbol, "java.util.regex.Matcher.matches()"), "java")
    assert step is not None and step.rule == "regex_match"


def test_regex_match_fires_at_half_and_not_below() -> None:
    def row(share: float) -> dict[str, Any]:
        return _row(
            "lib/scalar.js::captureSegment",
            [_c("(own code)", "own", 1 - share), _c(r"RegExp: [\x00-\x08]", "native", share)],
            klass="entry_point",
        )

    assert next_step(row(0.5), "javascript").rule == "regex_match"
    # A segment checker whose own scan leads.
    assert next_step(row(0.4356), "javascript") is None


@pytest.mark.parametrize(
    ("language", "callee", "kind", "leaf"),
    [
        # Bare names other libraries spell too.
        ("python", "match", "library", None),
        ("javascript", "test", "native", None),
        # Not the standard library's matcher.
        ("java", "com.vendor.Matcher.matches(Object)", "library", None),
        ("go", "example.com/custom/regexp.(*Regexp).MatchString", "library", None),
        ("python", "Pattern.match", "library", None),
        # Calls that run no match in the call.
        ("go", "regexp.(*Regexp).SubexpIndex", "library", None),
        ("go", "regexp.(*Regexp).UnmarshalText", "library", None),
        ("java", "java.util.regex.Matcher.results()", "library", None),
        ("kotlin", "kotlin.text.Regex.findAll(CharSequence, int)", "library", None),
        # A literal-string replace in JavaScript.
        ("javascript", "String.replaceAll", "native", None),
        # A replacement callback whose time is a file read.
        (
            "go",
            "regexp.(*Regexp).ReplaceAllStringFunc",
            "library",
            ("os.Stat", "library", 0.75),
        ),
        ("python", "Pattern.sub", "native", ("stat", "native", 0.8)),
    ],
)
def test_regex_match_stays_silent(
    language: str, callee: str, kind: str, leaf: tuple[str, str, float] | None
) -> None:
    step = next_step(_regex_row("src/util.x::is_valid_id", callee, kind=kind, leaf=leaf), language)
    assert step is None or step.rule != "regex_match"


def test_a_callback_leaf_keeps_its_own_family() -> None:
    row = _regex_row(
        "a.go::rewrite",
        "regexp.(*Regexp).ReplaceAllStringFunc",
        leaf=("os.Stat", "library", 0.75),
    )
    assert next_step(row, "go").rule == "filesystem"


def test_a_v8_pattern_is_never_read_as_a_call_name() -> None:
    step = next_step(_regex_row("a.js::f", r"RegExp: ^foo\/regexp.MatchString"), "javascript")
    assert step is not None and step.rule == "regex_match"
    assert "compiled on each call" not in step.why


def test_a_v8_pattern_ending_in_the_function_name_is_no_thin_wrapper() -> None:
    step = next_step(_regex_row("api.js::end", r"RegExp: ^\/api\/end"), "javascript")
    assert step is not None and step.rule == "regex_match"


@pytest.mark.parametrize("leaf", ["parse", "_parse", "_compile"])
def test_a_python_replacement_callback_is_no_regex_time(leaf: str) -> None:
    row = _regex_row("a.py::render", "Pattern.sub", kind="native", leaf=(leaf, "library", 0.8))
    assert next_step(row, "python") is None


def test_a_small_compile_per_call_beside_a_built_matcher_reads_as_matching() -> None:
    row = _row(
        "a.go::check",
        [
            _c("regexp.(*Regexp).MatchString", "library", 0.55),
            _c("regexp.MatchString", "library", 0.01),
        ],
        klass="entry_point",
    )
    step = next_step(row, "go")
    assert step is not None and step.rule == "regex_match"
    assert step.why.startswith("56% of it is regular expression matching")
    assert "compiled on each call" not in step.why


def test_a_python_matcher_leaf_is_regex_time() -> None:
    row = _regex_row(
        "a.py::clean", "Pattern.sub", kind="native", leaf=("Pattern.match", "native", 0.7)
    )
    step = next_step(row, "python")
    assert step is not None and step.rule == "regex_match"
