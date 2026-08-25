"""Consumer contracts at *confirmed* HTTP-wrapper call sites.

The claim under test is that a client call is recognised because the callee
provably reaches an HTTP sink, not because its name contains "fetch" or "api".
Two consequences are what these tests pin:

* a wrapper the regex dialect cannot see — ``this.fetch<T>(path)``, whose type
  argument breaks ``fetch\\s*\\(`` — is extracted, with the right path;
* a function *named* like a wrapper that never calls a sink yields nothing.
  The second is the false positive the name-matching approach cannot avoid, and
  is the thing this layer is bought for.

Symbols come from the real :class:`ASTParser`, mapped onto the
:class:`IndexedSymbol` rows ingestion persists, rather than hand-built spans.
Wrapper confirmation depends on each symbol's ``start_line``/``end_line`` being
the extent ingestion actually recorded, so fabricating those here would test
this module against my own arithmetic instead of against the parse it consumes
in production.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.from_index import EXTRACTION_LAYER_KEY, LAYER_INDEX
from repowise.core.workspace.extractors.http.index_clients import extract_consumers
from repowise.core.workspace.extractors.http.js_clients import JsClientsDialect
from repowise.core.workspace.extractors.http.wrappers import confirm_wrappers
from repowise.core.workspace.repo_index import IndexedSymbol

from ._repo_index import make_repo_index


def _ingestion_symbols(path: str, source: str):
    """The symbols the real parser records for *source*."""
    info = FileInfo(
        path=path,
        abs_path=f"C:/fake/{path}",
        language="typescript",
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ASTParser().parse_file(info, source.encode()).symbols


def _symbols(path: str, source: str) -> list[IndexedSymbol]:
    """Those symbols as the rows ingestion persists, which is what this reads."""
    return [
        IndexedSymbol(
            symbol_id=sym.id,
            name=sym.name,
            qualified_name=sym.qualified_name,
            kind=sym.kind,
            signature=sym.signature,
            file_path=path,
            start_line=sym.start_line,
            end_line=sym.end_line,
            visibility=sym.visibility,
        )
        for sym in _ingestion_symbols(path, source)
    ]


def _run(source: str, path: str = "src/lib/api/client.ts"):
    """Extract via the index path; returns ``(contracts, unresolved, ctx)``."""
    symbols = _symbols(path, source)
    suffix = "." + path.rsplit(".", 1)[1]
    ctx = ScanContext("frontend", path, suffix, source, {})
    contracts, unresolved = extract_consumers(ctx, symbols)
    return contracts, unresolved, ctx


def _ids(contracts) -> set[str]:
    return {c.contract_id for c in contracts}


async def _index_for(repo, path: str, source: str):
    """A real per-repo index holding just *path*'s parsed symbols."""
    return await make_repo_index(
        repo, {path: _ingestion_symbols(path, source)}, alias="frontend"
    )


# The shape frontend/src/lib/api/client.ts really uses: a private `request`
# holding the sink, a generic `fetch<T>` delegating to it, and API methods
# calling `this.fetch<T>(...)`. Three hops from call site to sink.
CLIENT_TS = """\
const API_BASE = "https://api.example.com";

export class HostedApiClient {
  private async request(path: string, init?: RequestInit): Promise<Response> {
    const res = await fetch(`${API_BASE}${path}`, init);
    if (!res.ok) throw new Error("bad");
    return res;
  }

  private async fetch<T>(path: string, init?: RequestInit): Promise<T> {
    return (await this.request(path, init)).json();
  }

  getSnapshot(id: string) {
    return this.fetch<SnapshotResponse>(`/snapshots/${id}`);
  }

  listRepos() {
    return this.fetch<Repo[]>("/repos/mine");
  }

  patchFinding(id: string, status: string) {
    return this.fetch<Ok>(`/snapshots/${id}/dead-code`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
  }
}
"""


class TestClassMethodWrapper:
    """``this.fetch<T>(path)`` — the shape that produced the 20% recall."""

    def test_generic_class_method_calls_are_extracted(self):
        contracts, _unresolved, _ctx = _run(CLIENT_TS)
        assert _ids(contracts) == {
            "http::GET::/snapshots/{param}",
            "http::GET::/repos/mine",
            "http::PATCH::/snapshots/{param}/dead-code",
        }

    def test_template_interpolation_normalises_to_param(self):
        contracts, _u, _c = _run(CLIENT_TS)
        got = next(c for c in contracts if c.contract_id.endswith("/snapshots/{param}"))
        assert got.meta["path"] == "/snapshots/{param}"
        assert got.role == "consumer"

    def test_method_option_is_read_from_the_call(self):
        contracts, _u, _c = _run(CLIENT_TS)
        patch = next(c for c in contracts if "dead-code" in c.contract_id)
        assert patch.meta["method"] == "PATCH"

    def test_provenance_is_the_index_layer(self):
        contracts, _u, _c = _run(CLIENT_TS)
        # Compared against the literal, not the constant the production code
        # sets: asserting `== LAYER_INDEX` would move with any rename and could
        # never fail.
        assert all(c.meta[EXTRACTION_LAYER_KEY] == "index" for c in contracts)
        assert LAYER_INDEX == "index"

    def test_the_regex_dialect_alone_cannot_see_these(self):
        """Fails before this change: the generic type argument breaks the regex.

        Locks the actual gap rather than the fix. ``fetch\\s*\\(`` cannot match
        ``fetch<SnapshotResponse>(``, so the three real endpoint calls above are
        invisible to the text dialect.
        """
        ctx = ScanContext("frontend", "src/lib/api/client.ts", ".ts", CLIENT_TS, {})
        old = JsClientsDialect().extract(ctx)
        assert _ids(old) & {
            "http::GET::/snapshots/{param}",
            "http::PATCH::/snapshots/{param}/dead-code",
        } == set()


PUBLIC_CLIENT_TS = """\
const API_BASE = "https://api.example.com";

async function publicFetch<T>(path: string, opts: { revalidate?: number } = {}) {
  const res = await fetch(`${API_BASE}${path}`, { next: opts });
  if (!res.ok) return null;
  return res.json() as T;
}

export function getOverview(shortId: string) {
  return publicFetch<OverviewResponse>(`/snapshots/${shortId}/overview`);
}

export function getCommits(shortId: string) {
  return publicFetch<Paginated<Commit>>(`/snapshots/${shortId}/commits`);
}
"""


class TestModuleLevelHelper:
    """``publicFetch<T>()`` — a module function, extracted 0 before this change."""

    def test_module_level_wrapper_yields_contracts(self):
        contracts, _u, _c = _run(PUBLIC_CLIENT_TS, "src/lib/api/public-client.ts")
        assert _ids(contracts) == {
            "http::GET::/snapshots/{param}/overview",
            "http::GET::/snapshots/{param}/commits",
        }

    def test_regex_dialect_yields_nothing_here(self):
        """Fails before this change: this file's yield was 0."""
        ctx = ScanContext(
            "frontend", "src/lib/api/public-client.ts", ".ts", PUBLIC_CLIENT_TS, {}
        )
        assert JsClientsDialect().extract(ctx) == []


# Named exactly like an HTTP wrapper, reaches no sink. `_HTTP_NAME_RE` matches
# both `fetchUser` and `apiGet`, so the name-guessing dialect emits contracts
# for the two paths below; confirmation must not.
DECOY_TS = """\
const cache = new Map<string, string>();

export function fetchUser(key: string) {
  return cache.get(key);
}

export function apiGet(key: string) {
  return cache.get(key);
}

export function loadThings() {
  const a = fetchUser("/users/me");
  const b = apiGet("/things/all");
  return [a, b];
}
"""


class TestNameLookalikesAreRejected:
    """The false positive the name-matching approach cannot avoid."""

    def test_wrapper_named_functions_without_a_sink_yield_nothing(self):
        contracts, unresolved, _c = _run(DECOY_TS, "src/lib/cache.ts")
        assert contracts == []
        assert unresolved == 0

    def test_they_are_not_confirmed_as_wrappers(self):
        symbols = _symbols("src/lib/cache.ts", DECOY_TS)
        assert confirm_wrappers(symbols, DECOY_TS, ".ts") == set()

    def test_the_regex_dialect_does_emit_them(self):
        """Documents the defect being removed, so the gain is not hypothetical."""
        ctx = ScanContext("frontend", "src/lib/cache.ts", ".ts", DECOY_TS, {})
        assert _ids(JsClientsDialect().extract(ctx)) == {
            "http::GET::/users/me",
            "http::GET::/things/all",
        }

    def test_a_method_merely_named_fetch_is_not_a_sink(self):
        """A `fetch` that calls nothing must not confirm itself by its signature.

        Its own declaration line reads ``fetch<T>(`` — the sink pattern — so
        without excluding the declaration this would confirm by name, which is
        the whole failure mode under repair.
        """
        source = (
            "export class Fake {\n"
            "  private async fetch<T>(path: string): Promise<T> {\n"
            "    return JSON.parse(path) as T;\n"
            "  }\n"
            "  getThing() {\n"
            '    return this.fetch<Thing>("/things/1");\n'
            "  }\n"
            "}\n"
        )
        contracts, unresolved, _c = _run(source, "src/lib/fake.ts")
        assert contracts == []
        assert unresolved == 0


UNRESOLVED_TS = """\
const API_BASE = "https://api.example.com";

export class Client {
  private async request(path: string): Promise<Response> {
    return fetch(`${API_BASE}${path}`);
  }

  private async fetch<T>(path: string): Promise<T> {
    return (await this.request(path)).json();
  }

  known() {
    return this.fetch<Ok>("/repos/mine");
  }

  bridge(path: string) {
    return this.fetch<Ok>(path);
  }

  built(id: string) {
    return this.fetch<Ok>(buildPath(id));
  }
}
"""


class TestUnresolvedPathsAreCounted:
    """A path that cannot be resolved is counted, never guessed and never dropped."""

    def test_dynamic_paths_are_counted_not_extracted(self):
        contracts, unresolved, _c = _run(UNRESOLVED_TS, "src/lib/api/dyn.ts")
        assert _ids(contracts) == {"http::GET::/repos/mine"}
        assert unresolved == 2

    def test_no_contract_is_invented_for_them(self):
        contracts, _u, _c = _run(UNRESOLVED_TS, "src/lib/api/dyn.ts")
        # Nothing named after the variable, and no bare-param path.
        assert not any("path" in c.contract_id for c in contracts)
        assert not any(c.meta["path"] in ("/{param}", "/") for c in contracts)

    def test_calls_to_api_methods_are_not_counted_as_unresolved(self):
        """``client.getSnapshot(id)`` is a call to an endpoint method, not a path.

        Its first argument was never a URL, so counting it would inflate the
        unresolved figure with calls that were never lost.
        """
        source = UNRESOLVED_TS + (
            "\nexport function page(c: Client) {\n  return c.known();\n}\n"
        )
        _contracts, unresolved, _c = _run(source, "src/lib/api/dyn.ts")
        assert unresolved == 2


class TestFallbackRemainsReachable:
    """A repo with no usable index keeps the regex path.

    A repo that has never been indexed has no ``wiki.db`` to read, so the regex
    dialect is not a legacy path but the only path for it, and it has to stay
    reachable.
    """

    # Plain `fetch("/x")` — the one shape the text dialect handles well.
    PLAIN = 'export async function go() {\n  return fetch("/legacy/ping");\n}\n'

    def _extract(self, tmp_path, *, index):
        from repowise.core.workspace.extractors.http import HttpExtractor

        return HttpExtractor().extract(
            tmp_path,
            "frontend",
            None,
            files=[("src/legacy.ts", ".ts", self.PLAIN)],
            repo_index=index,
        )

    def test_without_an_index_the_regex_dialect_still_extracts(self, tmp_path):
        got = self._extract(tmp_path, index=None)
        assert "http::GET::/legacy/ping" in _ids(got)

    async def test_an_index_missing_this_file_does_not_delete_its_contracts(
        self, tmp_path
    ):
        """An index present but holding no rows for this file must not suppress
        the dialect. This is the rule that stops a partial index silently
        deleting contracts the regex can still see.
        """
        index = await make_repo_index(
            tmp_path, {"src/other.ts": []}, alias="frontend"
        )
        try:
            got = self._extract(tmp_path, index=index)
        finally:
            await index.close()
        assert "http::GET::/legacy/ping" in _ids(got)

    def test_a_language_with_no_sink_patterns_confirms_nothing(self):
        symbols = _symbols("src/main.go", "package main\n")
        assert confirm_wrappers(symbols, "package main\n", ".go") == set()

    def test_one_line_wrapper_bodies_are_not_lost(self):
        """A concise wrapper puts its body on the declaration line."""
        source = (
            "export class C {\n"
            '  ping() { return fetch("/x/ping"); }\n'
            "  go() { return this.ping(); }\n"
            "}\n"
        )
        symbols = _symbols("src/lib/one.ts", source)
        assert "ping" in confirm_wrappers(symbols, source, ".ts")


class TestHopBudget:
    """Confirmation is bounded, so it cannot become a whole-repo graph walk."""

    def test_budget_one_still_reaches_through_the_two_hop_chain(self):
        """call site -> fetch -> request -> sink: the target is 1 hop from the sink."""
        symbols = _symbols("src/lib/api/client.ts", CLIENT_TS)
        assert "fetch" in confirm_wrappers(symbols, CLIENT_TS, ".ts", budget=1)

    def test_budget_zero_confirms_only_the_symbol_holding_the_sink(self):
        symbols = _symbols("src/lib/api/client.ts", CLIENT_TS)
        assert confirm_wrappers(symbols, CLIENT_TS, ".ts", budget=0) == {"request"}

    def test_a_chain_longer_than_the_budget_is_not_confirmed(self):
        source = (
            "export class Deep {\n"
            "  a(p: string) { return fetch(p); }\n"
            "  b(p: string) { return this.a(p); }\n"
            "  c(p: string) { return this.b(p); }\n"
            "  d(p: string) { return this.c(p); }\n"
            "  e() { return this.d('/x/y'); }\n"
            "}\n"
        )
        symbols = _symbols("src/lib/deep.ts", source)
        confirmed = confirm_wrappers(symbols, source, ".ts", budget=2)
        assert "a" in confirmed and "b" in confirmed and "c" in confirmed
        assert "d" not in confirmed


class TestCycleSafety:
    def test_mutual_recursion_terminates(self):
        source = (
            "export class R {\n"
            "  a(p: string) { return this.b(p); }\n"
            "  b(p: string) { return this.a(p); }\n"
            "}\n"
        )
        symbols = _symbols("src/lib/r.ts", source)
        assert confirm_wrappers(symbols, source, ".ts") == set()


class TestSseEndpointsLinkToTheirConsumers:
    """The Phase 1 SSE endpoints reach a provider once their callers are seen.

    Note on where these consumers live: the three call sites named in the plan
    (``app/s/[shortId]/chat/page.tsx``, ``app/w/[id]/chat/page.tsx``,
    ``components/ask/ask-modal.tsx``) call *client methods* — ``postChatMessage``,
    ``postSnapshotAnswer``. The ``fetch`` carrying the URL is inside
    ``client.ts``, so that is the file the consumer contract is attributed to.
    Verified on the live index: all three endpoints below are extracted from
    ``src/lib/api/client.ts``.
    """

    # A streaming endpoint bypasses `this.fetch` and calls the global `fetch`
    # directly, because it needs the raw Response rather than parsed JSON.
    SSE_CLIENT = """\
const API_BASE = "https://api.example.com";

export class HostedApiClient {
  async postChatMessage(snapshotId: string, body: ChatBody) {
    return fetch(`${API_BASE}/snapshots/${snapshotId}/chat`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async postSnapshotAnswer(snapshotId: string, body: AskBody) {
    return fetch(`${API_BASE}/snapshots/${snapshotId}/answer`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
}
"""

    def test_sse_consumers_are_extracted(self):
        contracts, _u, _c = _run(self.SSE_CLIENT, "src/lib/api/client.ts")
        assert _ids(contracts) == {
            "http::POST::/snapshots/{param}/chat",
            "http::POST::/snapshots/{param}/answer",
        }

    def test_they_link_to_the_backend_providers(self):
        """End to end: FastAPI route declarations link to the frontend calls.

        Providers are built through :func:`build_provider_contract` from the raw
        decorator paths, so the join runs against whatever normalisation the
        product actually applies rather than an id hand-written to match.
        """
        from repowise.core.workspace.contracts import match_contracts
        from repowise.core.workspace.extractors.http.dialect import build_provider_contract

        consumers, _u, _c = _run(self.SSE_CLIENT, "src/lib/api/client.ts")
        backend_ctx = ScanContext("backend", "app/routers/chat.py", ".py", "", {})
        providers = [
            build_provider_contract(
                backend_ctx, method="POST", path_raw=raw, framework="fastapi"
            )
            for raw in (
                "/snapshots/{snapshot_id}/chat",
                "/snapshots/{snapshot_id}/answer",
            )
        ]
        links = match_contracts([*providers, *consumers])
        linked = {lk.contract_id for lk in links}
        assert linked == {
            "http::POST::/snapshots/{param}/chat",
            "http::POST::/snapshots/{param}/answer",
        }
        assert all(lk.consumer_repo == "frontend" for lk in links)
        assert all(lk.provider_repo == "backend" for lk in links)


class TestLexicalNoiseIsNotEvidence:
    """Comments and string bodies are not code, and must not confirm anything.

    Every case here was found by review of the first implementation, which
    searched raw text. Each one made the layer emit a contract for something
    that is not a call, which is the same name-guessing failure arriving by a
    different route.
    """

    def test_a_sink_mentioned_in_a_comment_does_not_confirm_a_wrapper(self):
        source = (
            "const cache = new Map<string, string>();\n"
            "export function niceWrapper(path: string) {\n"
            "  // TODO: used to fetch(path) directly, now cached\n"
            "  return cache.get(path);\n"
            "}\n"
            'export function go() { return niceWrapper("/users/me"); }\n'
        )
        contracts, unresolved, _c = _run(source, "src/lib/cache.ts")
        assert contracts == []
        assert unresolved == 0

    def test_a_sink_named_in_a_string_does_not_confirm_a_wrapper(self):
        source = (
            "export function describeIt(path: string) {\n"
            '  return "call fetch(" + path + ") to load";\n'
            "}\n"
            'export function go() { return describeIt("/users/me"); }\n'
        )
        contracts, _u, _c = _run(source, "src/lib/desc.ts")
        assert contracts == []

    def test_a_commented_out_call_is_not_a_contract(self):
        source = (
            "export class C {\n"
            "  private async fetch<T>(p: string): Promise<T> {\n"
            "    return (await fetch(p)).json();\n"
            "  }\n"
            "  live() { return this.fetch<A>('/live'); }\n"
            "  // dead() { return this.fetch<A>('/dead'); }\n"
            "}\n"
        )
        contracts, _u, _c = _run(source, "src/lib/c.ts")
        assert _ids(contracts) == {"http::GET::/live"}

    def test_an_apostrophe_in_a_comment_does_not_swallow_the_call(self):
        """A stray quote used to desynchronise the argument scanner."""
        source = (
            "export class C {\n"
            "  private async fetch<T>(p: string): Promise<T> {\n"
            "    return (await fetch(p)).json();\n"
            "  }\n"
            "  getIt(id: string) {\n"
            "    return this.fetch<Snap>(\n"
            "      // don't cache this\n"
            "      `/snapshots/${id}`\n"
            "    );\n"
            "  }\n"
            "}\n"
        )
        contracts, unresolved, _c = _run(source, "src/lib/c.ts")
        assert _ids(contracts) == {"http::GET::/snapshots/{param}"}
        assert unresolved == 0

    def test_nested_template_literals_parse(self):
        source = (
            "export class C {\n"
            "  private async fetch<T>(p: string): Promise<T> {\n"
            "    return (await fetch(p)).json();\n"
            "  }\n"
            "  q(id: string, f: string) {\n"
            "    return this.fetch<A>(`/a/${f ? `${id}` : `none`}/b`);\n"
            "  }\n"
            "  after() { return this.fetch<A>('/plain'); }\n"
            "}\n"
        )
        contracts, _u, _c = _run(source, "src/lib/c.ts")
        # The nested-template call normalises to {param}; the crucial part is
        # that the call *after* it still parses, which a desynced scan loses.
        assert "http::GET::/plain" in _ids(contracts)

    def test_a_regex_literal_containing_a_paren_does_not_desync(self):
        source = (
            "export class C {\n"
            "  private async fetch<T>(p: string): Promise<T> {\n"
            "    return (await fetch(p)).json();\n"
            "  }\n"
            "  a(s: string) { return this.fetch<A>('/x/' + s.replace(/\\)/g, '')); }\n"
            "  b() { return this.fetch<A>('/after'); }\n"
            "}\n"
        )
        contracts, _u, _c = _run(source, "src/lib/c.ts")
        assert "http::GET::/after" in _ids(contracts)


class TestNothingFoundIsSilentlyDropped:
    def test_an_unparseable_argument_list_is_counted_not_dropped(self):
        """A scanner failure must show up in the number, not hide in it."""
        from repowise.core.workspace.extractors.http import index_clients

        source = (
            "export class C {\n"
            "  private async fetch<T>(p: string): Promise<T> {\n"
            "    return (await fetch(p)).json();\n"
            "  }\n"
            "  ok() { return this.fetch<A>('/ok'); }\n"
            "  weird(p: string) { return this.fetch<A>(p); }\n"
            "}\n"
        )
        # Force every paren scan to fail, the way a malformed file would.
        original = index_clients._match_paren
        index_clients._match_paren = lambda _c, _i: -1
        try:
            contracts, unresolved, _c = _run(source, "src/lib/c.ts")
        finally:
            index_clients._match_paren = original
        assert contracts == []
        # Both call sites failed to parse; neither vanished.
        assert unresolved >= 2


class TestSupersedeCannotSubtract:
    """The index pass removes its own duplicates, never another shape."""

    async def test_an_axios_call_survives_beside_a_confirmed_wrapper(self, tmp_path):
        from repowise.core.workspace.extractors.http import HttpExtractor

        source = (
            "export class C {\n"
            "  private async fetch<T>(p: string): Promise<T> {\n"
            "    return (await fetch(p)).json();\n"
            "  }\n"
            "  wrapped() { return this.fetch<A>('/wrapped'); }\n"
            "}\n"
            "export function legacy() { return axios.get('/legacy'); }\n"
        )
        index = await _index_for(tmp_path, "src/lib/mix.ts", source)
        try:
            got = HttpExtractor().extract(
                tmp_path,
                "frontend",
                None,
                files=[("src/lib/mix.ts", ".ts", source)],
                repo_index=index,
            )
        finally:
            await index.close()
        ids = _ids(got)
        assert "http::GET::/wrapped" in ids  # from the index pass
        assert "http::GET::/legacy" in ids  # only the regex dialect sees this

    async def test_duplicates_are_not_emitted_twice(self, tmp_path):
        from repowise.core.workspace.extractors.http import HttpExtractor

        source = (
            "export async function load() {\n"
            "  return fetch('/dup');\n"
            "}\n"
        )
        index = await _index_for(tmp_path, "src/lib/dup.ts", source)
        try:
            got = HttpExtractor().extract(
                tmp_path,
                "frontend",
                None,
                files=[("src/lib/dup.ts", ".ts", source)],
                repo_index=index,
            )
        finally:
            await index.close()
        dup = [c for c in got if c.contract_id == "http::GET::/dup"]
        assert len(dup) == 1


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ('"/a/b"', "/a/b"),
        ("`/a/${id}`", "/a/${id}"),
        ("path", None),
        ('"/a" + id', None),
        ("buildPath(id)", None),
    ],
)
def test_literal_url_recognition(args: str, expected: str | None):
    from repowise.core.workspace.extractors.http.index_clients import _literal_url

    assert _literal_url(args) == expected


# ---------------------------------------------------------------------------
# Python: the client-instance receiver
# ---------------------------------------------------------------------------


def _py_symbols(path: str, source: str) -> list[IndexedSymbol]:
    """Python symbols as the rows ingestion persists them."""
    info = FileInfo(
        path=path,
        abs_path=f"C:/fake/{path}",
        language="python",
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return [
        IndexedSymbol(
            symbol_id=sym.id,
            name=sym.name,
            qualified_name=sym.qualified_name,
            kind=sym.kind,
            signature=sym.signature,
            file_path=path,
            start_line=sym.start_line,
            end_line=sym.end_line,
            visibility=sym.visibility,
        )
        for sym in ASTParser().parse_file(info, source.encode()).symbols
    ]


def _run_py(source: str, path: str = "app/services/caller.py"):
    ctx = ScanContext("backend", path, ".py", source, {})
    return extract_consumers(ctx, _py_symbols(path, source))


# The shape backend/app/services/frontend_cache.py really uses: a module
# constant for the path, an f-string joining it to a configured base, and the
# call through the variable the async context manager binds.
PURGE_PY = """\
import httpx

_PURGE_PATH = "/api/revalidate/snapshot"


async def _post_purge(short_id: str) -> bool:
    url = f"{settings.app_base_url.rstrip('/')}{_PURGE_PATH}"
    async with httpx.AsyncClient(follow_redirects=True) as http:
        resp = await http.post(url, json={"short_id": short_id})
    return resp.status_code // 100 == 2
"""


class TestBoundClientReceiver:
    """``http.post(url)`` on an ``httpx.AsyncClient`` — the shape with no wrapper."""

    def test_the_call_becomes_a_contract(self):
        contracts, unresolved = _run_py(PURGE_PY)
        assert _ids(contracts) == {"http::POST::/api/revalidate/snapshot"}
        assert unresolved == 0

    def test_the_contract_names_the_library_and_strips_the_base(self):
        (contract,) = _run_py(PURGE_PY)[0]
        assert contract.meta["client"] == "httpx"
        assert contract.meta["base_stripped"] is True
        assert contract.meta["base_token"] == "app_base_url"
        assert contract.meta[EXTRACTION_LAYER_KEY] == LAYER_INDEX

    def test_the_verb_names_the_method(self):
        source = (
            "import httpx\n"
            "async def f():\n"
            "    async with httpx.AsyncClient() as c:\n"
            "        await c.patch('/things/1')\n"
        )
        assert _ids(_run_py(source)[0]) == {"http::PATCH::/things/1"}

    def test_request_is_not_read_as_a_path(self):
        """``client.request(method, url)`` puts the verb first, so it is skipped."""
        source = (
            "import httpx\n"
            "async def f():\n"
            "    async with httpx.AsyncClient() as c:\n"
            "        await c.request('GET', '/things')\n"
        )
        contracts, unresolved = _run_py(source)
        assert contracts == []
        assert unresolved == 0


class TestBindingIsRequired:
    """A receiver is a client because the file constructs one, never by name."""

    def test_a_dict_named_client_yields_nothing(self):
        source = (
            "def register(client: dict) -> dict:\n"
            "    return {\n"
            "        'name': client.get('/client_name'),\n"
            "        'uris': client.get('/redirect_uris'),\n"
            "    }\n"
        )
        assert _run_py(source) == ([], 0)

    def test_a_binding_does_not_escape_its_function(self):
        """Measured on content_engine_chain.py: two objects, one name, one file."""
        source = (
            "import httpx\n"
            "async def notify():\n"
            "    async with httpx.AsyncClient() as client:\n"
            "        await client.post('/internal/notify')\n"
            "\n"
            "async def read(store):\n"
            "    client = await store.get_supabase()\n"
            "    return client.get('/rows/all')\n"
        )
        contracts, _ = _run_py(source)
        assert _ids(contracts) == {"http::POST::/internal/notify"}

    def test_an_attribute_binding_reaches_the_other_methods(self):
        source = (
            "import httpx\n"
            "class Osv:\n"
            "    def __init__(self):\n"
            "        self._client = httpx.Client(base_url='https://osv.dev')\n"
            "\n"
            "    def query(self):\n"
            "        return self._client.post('/v1/query')\n"
        )
        assert _ids(_run_py(source)[0]) == {"http::POST::/v1/query"}


class TestPathsAreReadNeverGuessed:
    def test_a_call_shaped_string_is_not_a_call(self):
        """The masked scan is what keeps prose out; ``content`` still has the bytes."""
        source = (
            "import httpx\n"
            "async def f(log):\n"
            "    c = httpx.AsyncClient()\n"
            "    log.info(\"call c.get('/api/leak') to see\")\n"
            "    await c.get('/api/real')\n"
        )
        assert _ids(_run_py(source)[0]) == {"http::GET::/api/real"}

    def test_an_example_in_a_docstring_is_not_an_assignment(self):
        source = (
            "import httpx\n"
            "async def f(build):\n"
            '    """Example:\n'
            "\n"
            '        url = "/docs/example"\n'
            '    """\n'
            "    url = build()\n"
            "    c = httpx.AsyncClient()\n"
            "    await c.get(url)\n"
        )
        contracts, unresolved = _run_py(source)
        assert contracts == []
        assert unresolved == 1

    def test_a_docstring_quote_does_not_desynchronise_the_rest_of_the_file(self):
        """A ``"`` inside a docstring used to close it early and mask real code."""
        source = (
            "import httpx\n"
            "async def f():\n"
            '    """He said "hi" there."""\n'
            "    async with httpx.AsyncClient() as c:\n"
            "        await c.get('/after/the/docstring')\n"
        )
        assert _ids(_run_py(source)[0]) == {"http::GET::/after/the/docstring"}

    def test_a_name_assigned_twice_is_not_folded(self):
        source = (
            "import httpx\n"
            "async def f(flag):\n"
            "    url = '/one'\n"
            "    url = '/two'\n"
            "    c = httpx.AsyncClient()\n"
            "    await c.get(url)\n"
        )
        contracts, unresolved = _run_py(source)
        assert contracts == []
        assert unresolved == 1

    def test_an_unresolvable_call_is_counted_not_dropped(self):
        source = (
            "import httpx\n"
            "async def f(build):\n"
            "    c = httpx.AsyncClient()\n"
            "    await c.get(build('x'))\n"
        )
        assert _run_py(source) == ([], 1)

    def test_a_literal_brace_is_refused_rather_than_corrupted(self):
        """``{{`` survives normalization as a dangling ``}``, so it is not claimed."""
        source = (
            "import httpx\n"
            "async def f(v):\n"
            "    c = httpx.AsyncClient()\n"
            '    await c.get(f"/q/{{literal}}/{v}")\n'
        )
        assert _run_py(source) == ([], 1)


class TestPythonLeavesTheJsPassAlone:
    def test_a_python_file_with_no_client_yields_nothing(self):
        source = "import os\ndef f():\n    return os.environ.get('/not/a/url')\n"
        assert _run_py(source) == ([], 0)


class TestTrailingQueryExpressionIsNotAPathSegment:
    """An interpolated query string must not become a segment of the path.

    The ``?`` lives inside the expression, so :func:`normalize_http_path` never
    sees it and the call used to key on ``/snapshots/{param}/graph{param}``,
    which no provider declares.
    """

    QUERY_SUFFIX_TS = """\
const API_BASE = "https://api.example.com";

export class HostedApiClient {
  private async request(path: string, init?: RequestInit): Promise<Response> {
    const res = await fetch(`${API_BASE}${path}`, init);
    if (!res.ok) throw new Error("bad");
    return res;
  }

  private async fetch<T>(path: string, init?: RequestInit): Promise<T> {
    return (await this.request(path, init)).json();
  }

  getGraph(id: string, limit?: number) {
    const q = limit != null ? `?limit=${limit}` : "";
    return this.fetch<GraphResponse>(`/snapshots/${id}/graph${q}`);
  }

  getFile(id: string, path: string) {
    return this.fetch<FileResponse>(`/repos/${id}/files/${path}`);
  }
}
"""

    def test_the_suffix_is_dropped_from_the_key(self):
        contracts, _u, _c = _run(self.QUERY_SUFFIX_TS)
        assert "http::GET::/snapshots/{param}/graph" in _ids(contracts)

    def test_a_final_path_parameter_is_kept(self):
        """The discriminator is the ``/`` separator, not the interpolation."""
        contracts, _u, _c = _run(self.QUERY_SUFFIX_TS)
        assert "http::GET::/repos/{param}/files/{param}" in _ids(contracts)

    def test_it_now_reaches_the_provider_that_exists(self):
        """End to end against a provider built by the product's own normalizer."""
        from repowise.core.workspace.contracts import match_contracts
        from repowise.core.workspace.extractors.http.dialect import build_provider_contract

        consumers, _u, _c = _run(self.QUERY_SUFFIX_TS)
        backend_ctx = ScanContext("backend", "app/routers/graph.py", ".py", "", {})
        provider = build_provider_contract(
            backend_ctx,
            method="GET",
            path_raw="/snapshots/{snapshot_id}/graph",
            framework="fastapi",
        )
        links = match_contracts([provider, *consumers])
        assert {lk.contract_id for lk in links} == {"http::GET::/snapshots/{param}/graph"}

    def test_the_shared_normalizer_was_not_widened(self):
        """Providers share ``normalize_http_path``; the fix stays consumer-side."""
        from repowise.core.workspace.extractors.http.paths import normalize_http_path

        assert (
            normalize_http_path("/snapshots/{id}/graph${q}")
            == "/snapshots/{param}/graph{param}"
        )


class TestTheCalleeNameCarriesTheVerb:
    """``apiPost(...)`` is a POST. Defaulting it to GET matched no provider."""

    # The shape packages/api-client/src/providers.ts really uses: verb-named
    # wrappers imported from a sibling module, called with no `method:` option.
    PROVIDERS_TS = """\
import { apiPost, apiDelete } from "./client";

export async function addProviderKey(providerId: string, apiKey: string) {
  await apiPost(`/api/providers/${providerId}/key`, { api_key: apiKey });
}

export async function removeProviderKey(providerId: string, repoId?: string) {
  const qs = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : "";
  await apiDelete(`/api/providers/${providerId}/key${qs}`);
}
"""

    def test_the_verb_wrappers_record_their_own_methods(self):
        ctx = ScanContext(
            "repowise",
            "packages/api-client/src/providers.ts",
            ".ts",
            self.PROVIDERS_TS,
            {},
        )
        assert _ids(JsClientsDialect().extract(ctx)) == {
            "http::POST::/api/providers/{param}/key",
            "http::DELETE::/api/providers/{param}/key",
        }

    @pytest.mark.parametrize(
        ("callee", "expected"),
        [
            ("apiPost", "POST"),
            ("apiDelete", "DELETE"),
            # A leading verb wins outright, so the noun `Post` cannot steal it.
            ("getPostById", "GET"),
        ],
    )
    def test_the_verb_is_read_from_the_name(self, callee, expected):
        from repowise.core.workspace.extractors.http.dialect import method_from_callee

        assert method_from_callee(callee) == expected

    def test_a_name_with_no_verb_falls_back(self):
        from repowise.core.workspace.extractors.http.dialect import method_from_callee

        assert method_from_callee("fetchJSON") == "GET"
