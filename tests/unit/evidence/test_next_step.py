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
        (
            "plugins.py::_discover",
            "python",
            [_c("entry_points", "library", 1.0, ("open", "library", 0.2))],
            "package_metadata",
        ),
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


def test_django_cursor_wrappers_read_and_write_like_any_driver() -> None:
    for name in ("CursorWrapper.execute", "CursorDebugWrapper.fetchall"):
        assert next_step(_row("a.py::f", [_c(name, "library", 0.9)]), "python").rule == "db_read"
    # A batch write per request needs its own: silent.
    assert (
        next_step(_row("a.py::f", [_c("CursorWrapper.executemany", "library", 0.9)]), "python")
        is None
    )


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
    ],
)
def test_django_single_row_lookups_builders_and_cursor_leaves_stay_silent(
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
