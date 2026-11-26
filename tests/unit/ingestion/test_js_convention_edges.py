"""Files a JS framework loads by directory convention are reached, not dead.

The framework is read from the ``package.json`` that depends on it, and its
globs are relative to that manifest. End to end: traverse -> parse -> graph ->
framework edges -> dead code, the path a real index takes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.framework_facts import (
    REACT_EMAIL,
    convention_load_reasons,
    js_frameworks,
    parse_npm_manifest,
)


def _pkg(deps: dict[str, str], scripts: dict[str, str] | None = None, dev: bool = False) -> str:
    return json.dumps({"name": "x", "devDependencies" if dev else "dependencies": deps, "scripts": scripts or {}})


_NONE = "export default function Page() { return null; }\n"

_REPO = {
    # React Email: a preview default export beside the template the app imports.
    "mail/package.json": _pkg({"react-email": "6"}, {"dev": "email dev --port 3456"}),
    "mail/emails/auth/welcome.tsx": (
        "export function Welcome() { return null; }\n"
        "export default function WelcomePreview() { return Welcome(); }\n"
    ),
    "mail/src/index.ts": "export { Welcome } from '../emails/auth/welcome';\n",
    "web/package.json": _pkg({"next": "14"}),
    "web/pages/about.tsx": _NONE,
    "web/src/pages/blog/[slug].tsx": _NONE + "export async function getStaticProps() { return {}; }\n",
    "rmx/package.json": _pkg({"@remix-run/react": "2"}),
    "rmx/app/routes/_index.tsx": _NONE + "export function loader() { return null; }\n",
    "rmx/app/root.tsx": _NONE,
    "nux/package.json": _pkg({"nuxt": "3"}, dev=True),
    "nux/plugins/analytics.ts": _NONE,
    "nux/server/api/health.ts": _NONE,
    "nux/components/Card.ts": _NONE,  # auto-imported when used: not an entry
    "svk/package.json": _pkg({"@sveltejs/kit": "2"}, dev=True),
    "svk/src/hooks.server.ts": "export async function handle() { return null; }\n",
    "ast/package.json": _pkg({"astro": "4"}),
    "ast/src/middleware.ts": "export function onRequest() { return null; }\n",
    "exp/package.json": _pkg({"expo-router": "3"}),
    "exp/app/_layout.tsx": _NONE,
    "exp/app/(tabs)/settings.tsx": _NONE,
    # Controls: the same paths in a package that depends on none of them.
    "plain/package.json": _pkg({"react": "18"}),
    "plain/emails/x.tsx": _NONE,
    "plain/pages/y.tsx": _NONE,
}


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _analyze(repo: Path):
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    builder.add_framework_edges([])
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=repo
    ).analyze({"detect_unused_internals": True})
    findings = [f for f in report.findings if f.kind.value != "zombie_package"]
    return builder.graph(), findings


def test_convention_loaded_files_are_anchored_and_not_dead(tmp_path: Path) -> None:
    graph, findings = _analyze(_write(tmp_path, _REPO))
    anchored = {
        "framework:react-email": "mail/emails/auth/welcome.tsx",
        "framework:nextjs": "web/src/pages/blog/[slug].tsx",
        "framework:remix": "rmx/app/routes/_index.tsx",
        "framework:nuxt": "nux/server/api/health.ts",
        "framework:sveltekit": "svk/src/hooks.server.ts",
        "framework:astro": "ast/src/middleware.ts",
        "framework:expo-router": "exp/app/(tabs)/settings.tsx",
    }
    for anchor, path in anchored.items():
        assert graph.has_edge(anchor, path), (anchor, path)
    assert not graph.has_edge("framework:nuxt", "nux/components/Card.ts")
    flagged = {f.file_path for f in findings}
    # Only the auto-imported component nothing uses and the controls remain.
    assert flagged == {"nux/components/Card.ts", "plain/emails/x.tsx", "plain/pages/y.tsx"}
    # The auto-imported component is listed, but never as deletion-ready.
    card = [f for f in findings if f.file_path == "nux/components/Card.ts"]
    assert card and not any(f.safe_to_delete for f in card)


def test_the_preview_default_export_was_the_reported_shape(tmp_path: Path) -> None:
    """Without React Email declared, the preview reads as a deletion-ready export."""
    files = {k: v for k, v in _REPO.items() if k.startswith("mail/")}
    files["mail/package.json"] = _pkg({"react": "18"})
    _graph, findings = _analyze(_write(tmp_path, files))
    preview = [f for f in findings if f.symbol_name == "WelcomePreview"]
    assert preview and preview[0].confidence >= 0.9


def test_react_email_dir_flag_moves_the_emails_directory() -> None:
    manifest = parse_npm_manifest(
        {"dependencies": {"react-email": "6"}, "scripts": {"dev": "email dev --dir ./src/templates -p 3001"}},
        "packages/mail",
    )
    assert manifest is not None
    ((facts, pkg),) = js_frameworks(manifest)
    assert pkg == "react-email" and facts.anchor == REACT_EMAIL.anchor
    paths = ["packages/mail/src/templates/a.tsx", "packages/mail/emails/b.tsx"]
    assert facts.entry_files("packages/mail", paths) == ["packages/mail/src/templates/a.tsx"]


def test_a_bare_email_command_keeps_the_default_dir_beside_a_named_one() -> None:
    manifest = parse_npm_manifest(
        {
            "dependencies": {"react-email": "6"},
            "scripts": {"dev": "email dev", "export": "email export --dir out", "lint": "tsc"},
        }
    )
    assert manifest is not None
    ((facts, _pkg),) = js_frameworks(manifest)
    paths = ["emails/a.tsx", "out/b.tsx", "src/c.tsx"]
    assert facts.entry_files("", paths) == ["emails/a.tsx", "out/b.tsx"]


def test_a_dir_outside_the_package_anchors_nothing() -> None:
    manifest = parse_npm_manifest(
        {"dependencies": {"react-email": "6"}, "scripts": {"dev": "email dev --dir=../shared"}}
    )
    assert manifest is not None and js_frameworks(manifest) == []


def test_convention_reasons_name_the_framework_and_its_manifest(tmp_path: Path) -> None:
    repo = _write(tmp_path, _REPO)
    reasons = convention_load_reasons(
        repo, ["nux/components/Card.ts", "mail/emails/auth/welcome.tsx", "plain/pages/y.tsx"]
    )
    assert set(reasons) == {"nux/components/Card.ts", "mail/emails/auth/welcome.tsx"}
    assert "Nuxt" in reasons["nux/components/Card.ts"]
    assert "nux/package.json depends on nuxt" in reasons["nux/components/Card.ts"]


def test_an_unreadable_manifest_is_skipped(tmp_path: Path) -> None:
    repo = _write(tmp_path, {"package.json": "{ not json", "emails/a.tsx": _NONE})
    assert convention_load_reasons(repo, ["emails/a.tsx"]) == {}


def test_a_nuxt_4_app_s_convention_dirs_and_i18n_locales_are_loaded(tmp_path: Path) -> None:
    """The shape of a Nuxt 4 app with ``@nuxtjs/i18n`` and a ``langDir`` that climbs out of ``i18n/``."""
    repo = _write(
        tmp_path,
        {
            "frontend/package.json": _pkg({"nuxt": "4", "@nuxtjs/i18n": "10"}),
            "frontend/nuxt.config.ts": (
                "export default defineNuxtConfig({\n"
                "  i18n: { lazy: true, langDir: './../app/lang/locales', locales: [] },\n"
                "});\n"
            ),
        },
    )
    loaded = [
        "frontend/app/layouts/blank.vue",
        "frontend/app/plugins/theme.ts",
        "frontend/app/middleware/auth.ts",
        "frontend/server/api/health.ts",
        "frontend/server/routes/feed.ts",
        "frontend/server/middleware/log.ts",
        "frontend/app/lang/locales/de-DE.ts",
        "frontend/app/i18n.config.ts",
        "frontend/app/composables/use-user.ts",  # auto-imported: review, not entry
    ]
    reasons = convention_load_reasons(repo, [*loaded, "frontend/app/lib/api.ts"])
    assert set(reasons) == set(loaded)
    assert all("Nuxt" in r for r in reasons.values())


_VUE = "<template><slot /></template>\n"


def test_a_nuxt_src_dir_roots_every_convention_dir(tmp_path: Path) -> None:
    """``srcDir: 'src'`` moves layouts, plugins and middleware; ``server/`` stays at the root."""
    repo = _write(
        tmp_path,
        {
            "web/package.json": _pkg({"nuxt": "^3.12.0"}),
            "web/nuxt.config.ts": "export default defineNuxtConfig({ srcDir: 'src/' });\n",
            "web/src/layouts/default.vue": _VUE,
            "web/src/plugins/analytics.ts": _NONE,
            "web/src/middleware/auth.ts": _NONE,
            "web/src/pages/index.vue": _VUE,
            "web/src/app.vue": _VUE,
            "web/server/api/ping.ts": _NONE,
            # Not where this app keeps its code, so nothing loads it.
            "web/layouts/stray.vue": _VUE,
            "web/src/components/Card.vue": _VUE,
        },
    )
    graph, findings = _analyze(repo)
    for path in (
        "web/src/layouts/default.vue",
        "web/src/plugins/analytics.ts",
        "web/src/middleware/auth.ts",
        "web/src/pages/index.vue",
        "web/src/app.vue",
        "web/server/api/ping.ts",
    ):
        assert graph.has_edge("framework:nuxt", path), path
    assert not graph.has_edge("framework:nuxt", "web/layouts/stray.vue")
    flagged = {f.file_path for f in findings}
    assert "web/src/layouts/default.vue" not in flagged
    assert "web/layouts/stray.vue" in flagged
    reasons = convention_load_reasons(repo, ["web/src/components/Card.vue", "web/layouts/stray.vue"])
    assert set(reasons) == {"web/src/components/Card.vue"}


def test_a_default_nuxt_4_app_is_rooted_at_app(tmp_path: Path) -> None:
    """No ``srcDir``: Nuxt 4 reads ``app/``, told apart by the files the app holds."""
    repo = _write(
        tmp_path,
        {
            "package.json": _pkg({"nuxt": "^4.1.0"}),
            "nuxt.config.ts": "export default defineNuxtConfig({});\n",
            "app/app.vue": _VUE,
            "app/layouts/default.vue": _VUE,
            "app/plugins/theme.ts": _NONE,
            "plugins/legacy.ts": _NONE,
        },
    )
    graph, _findings = _analyze(repo)
    assert graph.has_edge("framework:nuxt", "app/layouts/default.vue")
    assert graph.has_edge("framework:nuxt", "app/plugins/theme.ts")
    assert not graph.has_edge("framework:nuxt", "plugins/legacy.ts")


def test_sveltekit_kit_files_move_the_convention_dirs(tmp_path: Path) -> None:
    config = (
        "export default {\n  kit: {\n    adapter: adapter(),\n"
        "    files: { routes: 'app/routes', hooks: { server: 'app/hooks.server' } },\n  },\n};\n"
    )
    repo = _write(
        tmp_path,
        {
            "package.json": _pkg({"@sveltejs/kit": "2"}, dev=True),
            "svelte.config.js": config,
            "app/routes/+page.ts": "export function load() { return {}; }\n",
            "app/hooks.server.ts": "export async function handle() { return null; }\n",
            "src/params/slug.ts": "export function match() { return true; }\n",
            "src/routes/+page.ts": "export function load() { return {}; }\n",
        },
    )
    graph, _findings = _analyze(repo)
    for path in ("app/routes/+page.ts", "app/hooks.server.ts", "src/params/slug.ts"):
        assert graph.has_edge("framework:sveltekit", path), path
    assert not graph.has_edge("framework:sveltekit", "src/routes/+page.ts")


def test_nuxt_locales_need_the_i18n_module(tmp_path: Path) -> None:
    repo = _write(tmp_path, {"package.json": _pkg({"nuxt": "4"})})
    assert convention_load_reasons(repo, ["i18n/locales/en.ts"]) == {}


@pytest.mark.parametrize(
    ("script", "paths", "expected"),
    [
        ("email dev --dir .", ["a.tsx", "src/b.tsx"], ["a.tsx", "src/b.tsx"]),
        ("email dev && tsc -d out", ["emails/a.tsx", "out/b.tsx"], ["emails/a.tsx"]),
        ("email dev --dir emails[v2]", ["emails[v2]/a.tsx", "emails2/a.tsx"], ["emails[v2]/a.tsx"]),
    ],
    ids=["package-root", "later-command-flag", "literal-brackets"],
)
def test_react_email_dir_edge_cases(script: str, paths: list[str], expected: list[str]) -> None:
    manifest = parse_npm_manifest({"dependencies": {"react-email": "6"}, "scripts": {"dev": script}})
    assert manifest is not None
    ((facts, _pkg),) = js_frameworks(manifest)
    assert facts.entry_files("", paths) == expected
