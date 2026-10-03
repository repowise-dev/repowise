"""The overview's system map: structure picks it, code compiles it."""

from __future__ import annotations

import re

from repowise.core.generation.architecture_map import MAX_NODES, build_system_map
from repowise.core.generation.architecture_mermaid import (
    HEADING,
    apply_names,
    compile_mermaid,
    embed_system_map,
    split_names,
    system_map_section,
)


def _monorepo(extra_packages: int = 0):
    """A generic web app: a frontend and a CLI in front of an API over a core library."""
    files = {
        "apps/web/src/page.ts": "typescript",
        "apps/web/src/client.ts": "typescript",
        "apps/cli/src/tool/main.py": "python",
        "services/api/src/api/app.py": "python",
        "services/api/src/api/routes.py": "python",
        "libs/core/src/core/engine.py": "python",
        "libs/core/src/core/store.py": "python",
        "libs/core/src/core/model.py": "python",
        "libs/shared/src/index.ts": "typescript",
    }
    manifests = [
        "apps/web/package.json",
        "apps/cli/pyproject.toml",
        "services/api/pyproject.toml",
        "libs/core/pyproject.toml",
        "libs/shared/package.json",
    ]
    edges = [
        ("apps/web/src/page.ts", "libs/shared/src/index.ts", "imports"),
        ("apps/web/src/page.ts", "apps/web/src/client.ts", "imports"),
        ("apps/cli/src/tool/main.py", "libs/core/src/core/engine.py", "imports"),
        ("services/api/src/api/routes.py", "libs/core/src/core/engine.py", "imports"),
        ("services/api/src/api/routes.py", "libs/core/src/core/model.py", "calls"),
        ("libs/core/src/core/store.py", "external:dbdriver", "imports"),
    ]
    for i in range(extra_packages):
        files[f"libs/extra{i}/src/mod.py"] = "python"
        manifests.append(f"libs/extra{i}/pyproject.toml")
        edges.append((f"libs/extra{i}/src/mod.py", "libs/core/src/core/model.py", "imports"))
    return build_system_map(
        files,
        edges,
        repo_name="demo",
        manifests=manifests,
        package_names={"libs/core": "demo-core"},
        entry_points=["apps/cli/src/tool/main.py", "services/api/src/api/app.py"],
        route_files=["services/api/src/api/routes.py"],
        stores={"external:dbdriver": "dbdriver"},
        http_links=[("apps/web/src/client.ts", "services/api/src/api/routes.py")],
        pages=[("libs/core/src/core", {"libs/core/src/core/engine.py"}), ("apps/web", set())],
    )


_NODE_DEF = re.compile(r"^([A-Za-z_]\w*)[\[(]")
_EDGE = re.compile(r'^([A-Za-z_]\w*) (?:-->|==>)\|"[^"|]*"\| ([A-Za-z_]\w*)$')


def _compiler_problems(body: str) -> list[str]:
    """What a compiler bug would break: every edge, click and class names something defined."""
    lines = [line.strip() for line in body.split("\n")[1:] if line.strip()]
    nodes, classes, refs, used, depth = set(), set(), [], set(), 0
    problems = []
    for text in lines:
        if text.count('"') % 2:
            problems.append(f"unbalanced quote: {text}")
        used.update(re.findall(r":::(\w+)$", text))
        if text.startswith("subgraph "):
            depth += 1
        elif text == "end":
            depth -= 1
        elif text.startswith("classDef "):
            classes.add(text.split()[1])
        elif text.startswith("click "):
            refs.append(text.split()[1])
        elif text.startswith("direction "):
            continue
        elif m := _EDGE.match(text):
            refs += [m.group(1), m.group(2)]
        elif m := _NODE_DEF.match(text):
            nodes.add(m.group(1))
        else:
            problems.append(f"unrecognised line: {text}")
    if depth:
        problems.append("unclosed subgraph")
    problems += [f"undefined node: {r}" for r in refs if r not in nodes]
    problems += [f"undefined class: {c}" for c in sorted(used - classes)]
    return problems


def _tier_of(system_map):
    return {n.path or n.id: n.tier for n in system_map.nodes}


class TestStructure:
    def test_tiers_follow_how_each_part_is_entered(self):
        tiers = _tier_of(_monorepo())
        assert tiers["services/api"] == "service"  # api entry point, HTTP route
        assert tiers["apps/web"] == "surface"  # HTTP caller, nothing imports it
        assert tiers["apps/cli"] == "surface"  # CLI entry point
        assert tiers["libs/core"] == "engine"
        assert tiers["n_store"] == "store"
        # One person for every human way in, one box per calling program.
        assert {"n_actor_user", "n_actor_api"} <= set(tiers)
        assert "n_actor_cli" not in tiers

    def test_runtime_and_store_edges_are_drawn(self):
        verbs = {(e.source, e.target): e.verb for e in _monorepo().edges}
        assert verbs[("n_apps_web", "n_services_api")] == "calls over HTTP"
        assert verbs[("n_libs_core", "n_store")] == "reads and writes"
        assert verbs[("n_actor_user", "n_apps_cli")] == "runs"
        assert verbs[("n_actor_user", "n_apps_web")] == "uses"
        assert verbs[("n_services_api", "n_libs_core")] == "calls"

    def test_node_cap_counts_omitted_parts(self):
        sm = _monorepo(extra_packages=12)
        assert len(sm.nodes) <= MAX_NODES
        assert sm.omitted > 0

    def test_heavy_edge_is_drawn_thick_and_the_rest_are_capped(self):
        files = {f"p{i}/m.py": "python" for i in range(8)}
        files.update({f"p0/x{k}.py": "python" for k in range(60)})
        manifests = [f"p{i}/pyproject.toml" for i in range(8)]
        edges = [
            (f"p{i}/m.py", f"p{j}/m.py", "imports")
            for i in range(8)
            for j in range(i + 1, 8)
        ]
        edges += [(f"p0/x{k}.py", "p1/m.py", "imports") for k in range(60)]
        sm = build_system_map(files, edges, repo_name="demo", manifests=manifests)
        assert [(e.source, e.target) for e in sm.edges if e.heavy] == [("n_p0", "n_p1")]
        dependency_edges = [e for e in sm.edges if not e.source.startswith("n_actor_")]
        assert len(dependency_edges) <= 10

    def test_single_package_is_opened_up(self):
        files = {f"src/pkg/{n}.py": "python" for n in ("app", "cli", "helpers", "models")}
        files["src/pkg/sub/a.py"] = "python"
        edges = [
            ("src/pkg/cli.py", "src/pkg/app.py", "imports"),
            ("src/pkg/app.py", "src/pkg/helpers.py", "imports"),
            ("src/pkg/app.py", "src/pkg/sub/a.py", "imports"),
            ("src/pkg/models.py", "src/pkg/helpers.py", "imports"),
        ]
        sm = build_system_map(
            files,
            edges,
            repo_name="pkg",
            manifests=["pyproject.toml"],
            entry_points=["src/pkg/cli.py"],
        )
        paths = {n.path for n in sm.nodes if n.path}
        assert {"src/pkg/sub", "src/pkg/app.py"} <= paths
        assert _tier_of(sm)["src/pkg/cli.py"] == "surface"

    def test_nothing_to_draw_returns_none(self):
        assert build_system_map({"a.py": "python"}, [], repo_name="x") is None

    def test_keyless_names_come_from_packages_and_paths(self):
        nodes = {n.path: n for n in _monorepo().nodes if n.path}
        assert nodes["libs/core"].name == "demo-core"
        assert nodes["apps/web"].name == "web"
        # The library only the web app imports is part of the web app's box.
        assert "libs/shared" not in nodes
        assert nodes["apps/web"].role == "3 TypeScript files"

    def test_a_route_file_is_flow_and_makes_its_box_the_server(self):
        """A route file defines no named symbol, yet it is the server, not a re-export."""
        files = {
            "app/server.js": "javascript",
            "app/routes/users.js": "javascript",
            "app/routes/posts.js": "javascript",
            "app/lib/db.js": "javascript",
            "app/lib/auth.js": "javascript",
            "app/cli/run.js": "javascript",
        }
        edges = [
            ("app/server.js", "app/routes/users.js", "imports"),
            ("app/server.js", "app/routes/posts.js", "imports"),
            ("app/routes/users.js", "app/lib/db.js", "imports"),
            ("app/routes/posts.js", "app/lib/auth.js", "imports"),
            ("app/cli/run.js", "app/lib/db.js", "imports"),
        ]
        sm = build_system_map(
            files,
            edges,
            repo_name="app",
            manifests=["app/package.json"],
            barrels={"app/routes/users.js", "app/routes/posts.js", "app/server.js"},
            route_files={"app/routes/users.js", "app/routes/posts.js"},
        )
        tiers = _tier_of(sm)
        assert tiers["app/routes/users.js"] == tiers["app/routes/posts.js"] == "service"
        drawn = {(e.source, e.target) for e in sm.edges}
        assert ("n_app_routes_users_js", "n_app_lib") in drawn

    def test_a_stray_route_does_not_make_a_large_library_the_server(self):
        files = {f"lib/m{i}.py": "python" for i in range(40)}
        files.update({f"{d}/main.py": "python" for d in ("cli", "a", "b", "c", "d")})
        edges = [("cli/main.py", f"lib/m{i}.py", "imports") for i in range(40)]
        edges += [(f"{d}/main.py", "lib/m0.py", "imports") for d in "abcd"]
        manifests = [f"{d}/pyproject.toml" for d in ("lib", "cli", "a", "b", "c", "d")]
        sm = build_system_map(
            files, edges, repo_name="demo", manifests=manifests, route_files={"lib/m7.py"}
        )
        assert _tier_of(sm)["lib"] == "engine"
        routes_only = build_system_map(
            files, edges, repo_name="demo", manifests=manifests,
            route_files={f"lib/m{i}.py" for i in range(10)},
        )
        assert _tier_of(routes_only)["lib"] == "service"

    def test_a_store_nobody_drawn_uses_takes_no_slot(self):
        files = {f"p{i}/m.py": "python" for i in range(12)}
        manifests = [f"p{i}/pyproject.toml" for i in range(12)]
        edges = [(f"p{i}/m.py", "p0/m.py", "imports") for i in range(1, 12)]
        files.update({f"p0/x{k}.py": "python" for k in range(5)})
        edges.append(("p9/m.py", "external:db", "imports"))
        sm = build_system_map(
            files, edges, repo_name="demo", manifests=manifests, stores={"external:db": "db"}
        )
        assert "n_store" not in {n.id for n in sm.nodes}
        assert len(sm.nodes) == MAX_NODES

    def test_libraries_several_ways_in_share_become_one_box(self):
        files = {
            "web/a.ts": "typescript",
            "desk/a.ts": "typescript",
            "ui/a.ts": "typescript",
            "kit/a.ts": "typescript",
            "cli/a.py": "python",
            "core/a.py": "python",
        }
        edges = [
            ("web/a.ts", "ui/a.ts", "imports"),
            ("desk/a.ts", "ui/a.ts", "imports"),
            ("ui/a.ts", "kit/a.ts", "imports"),
            ("cli/a.py", "core/a.py", "imports"),
            ("core/a.py", "external:db", "imports"),
        ]
        sm = build_system_map(
            files,
            edges,
            repo_name="demo",
            manifests=[f"{d}/package.json" for d in ("web", "desk", "ui", "kit", "cli", "core")],
            stores={"external:db": "db"},
        )
        shared = [n for n in sm.nodes if n.tier == "shared"]
        assert [(n.name, n.role) for n in shared] == [("Shared libraries", "2 TypeScript files")]
        assert _tier_of(sm)["core"] == "engine"

    def test_re_exports_are_not_flow(self):
        files = {f"src/pkg/{n}.py": "python" for n in ("__init__", "app", "cli", "a", "b", "c")}
        edges = [("src/pkg/__init__.py", f"src/pkg/{n}.py", "imports") for n in "abc"]
        edges += [
            ("src/pkg/__main__.py", "src/pkg/cli.py", "imports"),
            ("src/pkg/cli.py", "src/pkg/app.py", "imports"),
            ("src/pkg/app.py", "src/pkg/a.py", "imports"),
            ("src/pkg/a.py", "src/pkg/b.py", "imports"),
            ("src/pkg/b.py", "src/pkg/c.py", "imports"),
        ]
        files["src/pkg/__main__.py"] = "python"
        sm = build_system_map(
            files,
            edges,
            repo_name="pkg",
            manifests=["pyproject.toml"],
            entry_points=["src/pkg/__main__.py", "src/pkg/app.py"],
            barrels={"src/pkg/__init__.py", "src/pkg/__main__.py"},
        )
        paths = {n.path for n in sm.nodes if n.path}
        assert "src/pkg/__init__.py" not in paths
        assert not any(e.source == "n_src_pkg_init_py" for e in sm.edges)
        # The launcher hands its CLI role to the file it runs. app.py serves no
        # route and the CLI already calls it, so it is not a second way in.
        verbs = {(e.source, e.target): e.verb for e in sm.edges}
        assert verbs[("n_actor_user", "n_src_pkg_cli_py")] == "runs"
        assert _tier_of(sm)["src/pkg/app.py"] == "engine"
        assert len(sm.nodes) <= 7


class TestCompile:
    def test_compiles_to_a_valid_flowchart(self):
        body = compile_mermaid(_monorepo())
        assert body.startswith("flowchart TB")
        assert _compiler_problems(body) == []
        assert 'subgraph tier_service["Server"]' in body
        assert "classDef engine" in body and "#" not in body  # no hardcoded colors

    def test_click_goes_to_the_module_page(self):
        body = compile_mermaid(_monorepo())
        assert 'click n_libs_core "?page=module_page%3Alibs%2Fcore%2Fsrc%2Fcore"' in body
        assert 'click n_apps_web "?page=module_page%3Aapps%2Fweb"' in body

    def test_model_text_is_escaped(self):
        sm = apply_names(
            _monorepo(),
            {
                "nodes": [
                    {"id": "n_libs_core", "label": 'Core "engine" <b>|x|:::y', "role": "a;b#c"}
                ],
                "edges": [{"from": "n_services_api", "to": "n_libs_core", "verb": "calls (x)"}],
            },
        )
        body = compile_mermaid(sm)
        assert _compiler_problems(body) == []
        assert "<b>Core engine bxy</b><br/><small>abc</small>" in body
        assert 'n_services_api ==>|"calls (x)"| n_libs_core' in body or (
            'n_services_api -->|"calls (x)"| n_libs_core' in body
        )

    def test_caption_cannot_break_the_page(self):
        sm = apply_names(_monorepo(), {"caption": "# Parts ``` of <b>it</b>."})
        section = system_map_section(sm, "demo")
        assert section.count("```") == 2
        assert section.split("\n\n")[1].startswith("Parts of bit/b.")


class TestNaming:
    def test_split_names_strips_the_trailing_block(self):
        reply = '## Project Summary\n\nText.\n\n```json\n{"caption": "C.", "nodes": []}\n```\n'
        prose, names = split_names(reply)
        assert "```json" not in prose
        assert names == {"caption": "C.", "nodes": []}

    def test_split_names_keeps_prose_written_after_the_block(self):
        reply = '## A\n\nText.\n\n```json\n{"nodes": []}\n```\n\n## B\n\nMore.\n'
        prose, names = split_names(reply)
        assert "```json" not in prose and "## B\n\nMore." in prose
        assert names == {"nodes": []}

    def test_split_names_accepts_other_fence_shapes(self):
        for fence in ("```JSON", "```", "  ```json"):
            reply = f'## A\n\nText.\n\n{fence}\n  {{"nodes": []}}\n```\n'
            prose, names = split_names(reply)
            assert names == {"nodes": []} and "nodes" not in prose

    def test_split_names_leaves_other_json_alone(self):
        reply = '## A\n\nConfig:\n\n```json\n{"port": 8080}\n```\n'
        assert split_names(reply) == (reply, None)

    def test_split_names_drops_a_block_cut_off_mid_reply(self):
        reply = '## A\n\nText.\n\n```json\n{"caption": "Work flows", "nodes": [{"id"'
        prose, names = split_names(reply)
        assert names is None and prose == "## A\n\nText.\n"

    def test_unknown_ids_and_pairs_are_ignored(self):
        sm = _monorepo()
        named = apply_names(
            sm,
            {
                "caption": "Work flows from the ways in to the core. Extra. Dropped.",
                "nodes": [
                    {"id": "n_libs_core", "label": "Core engine", "role": "Does the work"},
                    {"id": "n_made_up", "label": "Ghost"},
                ],
                "edges": [
                    {"from": "n_services_api", "to": "n_libs_core", "verb": "runs"},
                    {"from": "n_libs_core", "to": "n_apps_web", "verb": "invented"},
                ],
            },
        )
        assert [n.id for n in named.nodes] == [n.id for n in sm.nodes]
        assert [(e.source, e.target) for e in named.edges] == [
            (e.source, e.target) for e in sm.edges
        ]
        verbs = {(e.source, e.target): e.verb for e in named.edges}
        assert verbs[("n_services_api", "n_libs_core")] == "runs"
        assert named.caption == "Work flows from the ways in to the core. Extra."


class TestEmbed:
    def test_lands_under_the_first_section_and_is_idempotent(self):
        page = "# T\n\n## Project Summary\n\nWhat.\n\n## Architecture\n\nHow.\n"
        section = system_map_section(_monorepo(), "demo")
        once = embed_system_map(page, section)
        assert once.index("## Project Summary") < once.index(HEADING) < once.index("## Architecture")
        assert embed_system_map(once, section) == once

    def test_replaces_the_legacy_end_of_page_map(self):
        page = (
            "## Project Summary\n\nWhat.\n\n## Architecture map\n\n"
            "```mermaid\nflowchart LR\nA-->B\n```\n"
        )
        out = embed_system_map(page, system_map_section(_monorepo(), "demo"))
        assert "## Architecture map" not in out
        assert out.count("```mermaid") == 1

    def test_caption_names_what_was_cut(self):
        section = system_map_section(_monorepo(extra_packages=12), "demo")
        assert "more parts are not shown" in section

    def test_no_map_removes_a_stale_one(self):
        page = embed_system_map("## A\n\nx\n\n## B\n", system_map_section(_monorepo(), "demo"))
        assert HEADING not in embed_system_map(page, None)
