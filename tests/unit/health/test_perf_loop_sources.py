"""What a perf loop walks, the magnitude that proves, and whether its calls can be one batch.

Through the real walker, as the other loop-fact tests do. Each positive rule is
paired with the shape that must stay ``unknown`` or ``query``.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.perf import loop_lexicon
from repowise.core.analysis.health.perf.actionability import actionability, assess_fix
from repowise.core.analysis.health.refactoring.performance_fix import fix_steps


def _loop(path: str, language: str, src: str):
    hits = [h for h in walk_file(path, language, src.encode()).perf_hits if h.kind == "io_in_loop"]
    assert hits, "no io_in_loop hit"
    return hits[0].loop


def _py(src: str):
    return _loop("f.py", "python", src)


def _ts(src: str):
    return _loop("f.ts", "typescript", src)


_REGISTERED_REPOS = """
from sqlalchemy import select
async def check(session):
    result = await session.execute(select(Repository))
    repos = result.scalars().all()
    for repo in repos:
        await session.execute(select(Page).where(Page.repository_id == repo.id))
"""
# The same loop with a neutral variable name, so only the model can say "registry".
_REGISTERED_ROWS = _REGISTERED_REPOS.replace("repos = ", "rows = ").replace("in repos", "in rows")


# -- registry and configuration tables ------------------------------------------


def test_a_loop_over_the_registered_repos_table_still_grows():
    # Registered repos grow with use: the table's name says what is walked, not how many.
    facts = _py(_REGISTERED_ROWS)
    assert facts.iterates == "config_table"
    assert facts.magnitude == "grows_with_data"


def test_a_loop_over_another_table_is_a_query_that_grows():
    src = _REGISTERED_REPOS.replace("Repository", "Order").replace("repos", "orders")
    facts = _py(src)
    assert facts.iterates == "query"
    assert facts.magnitude == "grows_with_data"


def test_a_workspace_config_repos_list_is_a_config_table():
    facts = _py(
        "from sqlalchemy import select\n"
        "async def sync(session, ws_config):\n"
        "    for entry in ws_config.repos:\n"
        "        await session.execute(select(Repo).where(Repo.path == entry.path))\n"
    )
    assert (facts.iterates, facts.magnitude) == ("config_table", "unknown")


def test_a_control_flow_graph_named_cfg_is_not_configuration():
    assert not loop_lexicon.is_config_name("cfg.blocks")
    assert loop_lexicon.is_config_name("models_cfg")
    assert not loop_lexicon.is_config_name("_REGISTRY_RE.finditer", callee=True)
    # A function returning repos may be scanning a disk for them.
    assert not loop_lexicon.is_config_name("discover_repos", callee=True)


# -- one key, a rare state ----------------------------------------------------------


def test_rows_of_one_primary_key_are_bounded():
    facts = _py(
        "def f(conn, task_id):\n"
        "    rows = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchall()\n"
        "    for row in rows:\n"
        "        conn.execute('UPDATE runs SET x = 1 WHERE task = ?', (row[0],)).fetchone()\n"
    )
    assert (facts.iterates, facts.magnitude) == ("caller_key", "bounded")


@pytest.mark.parametrize(
    "where",
    ["parent_id = ?", "id = m.session_id", "id = ? UNION SELECT id FROM t"],
)
def test_a_foreign_key_or_a_correlated_id_is_not_one_key(where):
    facts = _py(
        "def f(conn, task_id):\n"
        f"    rows = conn.execute('SELECT * FROM tasks WHERE {where}', (task_id,)).fetchall()\n"
        "    for row in rows:\n"
        "        conn.execute('UPDATE runs SET x = 1 WHERE task = ?', (row[0],)).fetchone()\n"
    )
    assert facts.iterates != "caller_key"
    assert facts.magnitude == "grows_with_data"


def test_a_filter_for_expired_rows_is_a_rare_subset_that_still_grows():
    facts = _py(
        "def f(conn, now):\n"
        "    stale = conn.execute(\n"
        "        'SELECT id FROM tasks WHERE status = ? AND claim_expires < ?', ('running', now)\n"
        "    ).fetchall()\n"
        "    for row in stale:\n"
        "        conn.execute('UPDATE tasks SET claim = NULL WHERE id = ?', (row[0],)).fetchone()\n"
    )
    assert (facts.iterates, facts.magnitude) == ("rare_subset", "grows_with_data")


# -- parameters, constants, listings --------------------------------------------------


def test_a_parameter_is_named_and_stays_unknown():
    facts = _py(
        "from sqlalchemy import select\n"
        "async def f(session, items):\n"
        "    for item in items:\n"
        "        await session.execute(select(M).where(M.id == item))\n"
    )
    assert (facts.iterates, facts.magnitude) == ("parameter", "unknown")


def test_a_chunked_walk_names_the_collection_it_chunks():
    facts = _py(
        "from sqlalchemy import select\n"
        "_CHUNK = 500\n"
        "async def f(session, ids):\n"
        "    for i in range(0, len(ids), _CHUNK):\n"
        "        await session.execute(select(M).where(M.id.in_(ids[i:i + _CHUNK])))\n"
    )
    assert facts.iterates == "parameter"


def test_a_class_constant_bound_to_a_literal_is_a_constant():
    src = (
        "class Store:\n"
        "    _TABLES = ('a', 'b')\n"
        "    def wipe(self, conn):\n"
        "        for t in self._TABLES:\n"
        "            conn.execute('DELETE FROM x WHERE t = ?', (t,)).fetchone()\n"
    )
    assert (_py(src).iterates, _py(src).magnitude) == ("constant", "bounded")
    # Bound to anything but a literal, the spelling proves nothing.
    computed = src.replace("('a', 'b')", "load_tables()")
    assert _py(computed) is None or _py(computed).magnitude == "unknown"


def test_a_class_constant_listing_computed_entries_is_no_constant():
    src = (
        "class Store:\n"
        "    _ROOTS = [BASE / 'a', repo.path]\n"
        "    def wipe(self, conn):\n"
        "        for t in self._ROOTS:\n"
        "            conn.execute('DELETE FROM x WHERE t = ?', (t,)).fetchone()\n"
    )
    assert _py(src) is None or _py(src).iterates != "constant"


def test_a_fixed_width_slice_keeps_what_the_loop_walks():
    facts = _py(
        "from sqlalchemy import select\n"
        "async def f(session, ids):\n"
        "    for key in ids[:5]:\n"
        "        await session.execute(select(M).where(M.id == key))\n"
    )
    assert (facts.iterates, facts.magnitude) == ("parameter", "bounded")


def test_ts_a_fixed_width_slice_keeps_what_the_loop_walks():
    facts = _ts(
        "async function f(ids: string[]) {\n"
        "  for (const id of ids.slice(0, 5)) {\n"
        "    await prisma.page.findMany({ where: { id } });\n"
        "  }\n"
        "}\n"
    )
    assert (facts.iterates, facts.magnitude) == ("parameter", "bounded")


def test_a_recursive_listing_of_the_repository_root_grows():
    facts = _py(
        "import os\n"
        "from sqlalchemy import select\n"
        "async def f(session, repo_root):\n"
        "    for root_, dirs, files in os.walk(repo_root):\n"
        "        await session.execute(select(root_))\n"
    )
    assert (facts.iterates, facts.magnitude) == ("listing", "grows_with_data")


def test_a_flat_listing_is_a_listing_of_unknown_size():
    facts = _py(
        "from sqlalchemy import select\n"
        "async def f(session, repo_root):\n"
        "    for p in repo_root.iterdir():\n"
        "        await session.execute(select(p))\n"
    )
    assert (facts.iterates, facts.magnitude) == ("listing", "unknown")


# -- not batchable ---------------------------------------------------------------------


def test_a_loop_that_returns_the_first_hit_is_not_batchable():
    facts = _py(
        "from sqlalchemy import select\n"
        "async def find(session, keys):\n"
        "    for key in keys:\n"
        "        row = await session.get(Model, key)\n"
        "        if row is not None:\n"
        "            return row\n"
        "    return None\n"
    )
    assert facts.not_batchable == "first_match"


def test_a_fallback_that_returns_the_first_call_that_works_is_not_batchable():
    facts = _py(
        "import requests\n"
        "def fetch(urls):\n"
        "    for url in urls:\n"
        "        try:\n"
        "            return requests.get(url)\n"
        "        except OSError:\n"
        "            continue\n"
    )
    assert facts.not_batchable == "first_match"


def test_an_exit_on_a_local_derived_from_the_result_is_no_first_match():
    # ``ok`` may test anything about the row; only a test of the result itself
    # says the call answered.
    facts = _py(
        "from sqlalchemy import select\n"
        "async def find(session, keys):\n"
        "    for key in keys:\n"
        "        row = await session.get(Model, key)\n"
        "        ok = check(row)\n"
        "        if ok:\n"
        "            break\n"
    )
    assert facts.not_batchable is None


def test_an_exit_under_an_outer_test_of_the_result_is_no_first_match():
    facts = _py(
        "from sqlalchemy import select\n"
        "async def find(session, keys):\n"
        "    for key in keys:\n"
        "        row = await session.get(Model, key)\n"
        "        if row is not None:\n"
        "            if key.last:\n"
        "                break\n"
    )
    assert facts.not_batchable is None


def test_a_cap_on_what_the_loop_collected_is_no_first_match():
    facts = _py(
        "import requests\n"
        "def fetch(urls, out):\n"
        "    for url in urls:\n"
        "        out.append(requests.get(url))\n"
        "        if len(out) >= 5:\n"
        "            break\n"
    )
    assert facts.not_batchable is None


def test_an_exit_on_the_element_is_no_first_match():
    facts = _py(
        "import requests\n"
        "def fetch(urls):\n"
        "    for url in urls:\n"
        "        if url.done:\n"
        "            break\n"
        "        requests.get(url)\n"
    )
    assert facts.not_batchable is None


def test_a_cursor_fed_by_the_previous_page_is_a_turn_loop():
    facts = _py(
        "import requests\n"
        "def pages(url):\n"
        "    cursor = None\n"
        "    while True:\n"
        "        page = requests.get(url, params={'cursor': cursor}).json()\n"
        "        cursor = page.get('next')\n"
    )
    assert facts.not_batchable == "turn_loop"


def test_a_conversation_appended_to_each_turn_is_a_turn_loop():
    facts = _py(
        "import requests\n"
        "def chat(url, messages, turns):\n"
        "    for _ in turns:\n"
        "        reply = requests.post(url, json=messages)\n"
        "        messages.append(reply)\n"
    )
    assert facts.not_batchable == "turn_loop"


def test_grouping_results_by_the_element_is_no_turn_loop():
    facts = _py(
        "import requests\n"
        "def f(rows, by_file):\n"
        "    for row in rows:\n"
        "        entry = requests.get(row.url)\n"
        "        by_file.setdefault(row.path, []).append(entry)\n"
    )
    assert facts.not_batchable is None


def test_independent_calls_are_not_marked():
    facts = _py(
        "import requests\n"
        "def total(urls):\n"
        "    n = 0\n"
        "    for url in urls:\n"
        "        r = requests.get(url)\n"
        "        n += len(r.content)\n"
        "    return n\n"
    )
    assert facts.not_batchable is None


# -- TypeScript --------------------------------------------------------------------------


def test_ts_a_counted_loop_to_a_literal_or_a_module_constant_is_bounded():
    for bound, header in (("5", ""), ("MAX_TRIES", "const MAX_TRIES = 5;\n")):
        facts = _ts(
            header + "async function f(u: string) {\n"
            f"  for (let i = 0; i < {bound}; i++) {{\n"
            "    await fetch(u);\n"
            "  }\n"
            "}\n"
        )
        assert (facts.iterates, facts.magnitude) == ("constant", "bounded")


def test_ts_a_counted_loop_over_a_read_grows():
    facts = _ts(
        "async function f() {\n"
        "  const rows = await prisma.order.findMany();\n"
        "  for (let i = 0; i < rows.length; i++) {\n"
        "    await prisma.item.findMany({ where: { orderId: rows[i].id } });\n"
        "  }\n"
        "}\n"
    )
    assert (facts.iterates, facts.magnitude) == ("query", "grows_with_data")


def test_ts_a_registry_table_read_still_grows():
    facts = _ts(
        "async function f() {\n"
        "  const rows = await prisma.repository.findMany();\n"
        "  for (const r of rows) {\n"
        "    await prisma.page.findMany({ where: { repoId: r.id } });\n"
        "  }\n"
        "}\n"
    )
    assert (facts.iterates, facts.magnitude) == ("config_table", "grows_with_data")


def test_ts_walking_the_repository_tree_grows():
    facts = _ts(
        "import fs from 'fs';\n"
        "function f(repoRoot: string) {\n"
        "  for (const file of walkFiles(path.join(repoRoot, 'src'))) {\n"
        "    fs.readFileSync(file);\n"
        "  }\n"
        "}\n"
    )
    assert (facts.iterates, facts.magnitude) == ("listing", "grows_with_data")


def test_ts_first_match_and_parameter():
    facts = _ts(
        "async function f(urls: string[]) {\n"
        "  for (const u of urls) {\n"
        "    const res = await fetch(u);\n"
        "    if (res.ok) return res;\n"
        "  }\n"
        "}\n"
    )
    assert facts.iterates == "parameter"
    assert facts.not_batchable == "first_match"


# -- what readers see -------------------------------------------------------------------


def _db_details(**extra):
    return [{"loop_magnitude": "grows_with_data", **extra}]


def test_a_not_batchable_loop_gets_no_batch_advice():
    assessment = assess_fix(
        "io_in_loop", ("io_in_loop",), "db", _db_details(loop_not_batchable="first_match"),
        cross_function=False,
    )
    assert assessment.fix is None and assessment.refusal == "loop_not_batchable"
    # A fact about the code, not a missing proof: nothing to investigate.
    assert actionability(assessment, "high").state == "expected"


def test_one_batchable_member_keeps_the_strategy():
    details = [*_db_details(loop_not_batchable="first_match"), *_db_details()]
    assessment = assess_fix("io_in_loop", ("io_in_loop",), "db", details, cross_function=False)
    assert assessment.fix is not None


def test_a_plan_step_says_what_the_loop_walks():
    (step,) = fix_steps(
        "batch_or_prefetch_io",
        "advisory",
        None,
        None,
        [{"file_path": "a.py", "function_name": "f", "line_start": 3, "call": "session.get",
          "loop_iterates": "query"}],
    )
    assert step["action"].endswith("(the loop walks rows a query returns)")
    assert step["loop_iterates"] == "query"
    (plain,) = fix_steps(
        "batch_or_prefetch_io", "advisory", None, None,
        [{"file_path": "a.py", "function_name": "f", "line_start": 3, "loop_iterates": "unknown"}],
    )
    assert "loop walks" not in plain["action"]
