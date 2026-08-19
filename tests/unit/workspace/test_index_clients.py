"""Consumer contracts at *confirmed* HTTP-wrapper call sites.

The claim under test is that a client call is recognised because the callee
provably reaches an HTTP sink, not because its name contains "fetch" or "api".
Two consequences are what these tests pin:

* a wrapper the regex dialect cannot see — ``this.fetch<T>(path)``, whose type
  argument breaks ``fetch\\s*\\(`` — is extracted, with the right path;
* a function *named* like a wrapper that never calls a sink yields nothing.
  The second is the false positive the name-matching approach cannot avoid, and
  is the thing this layer is bought for.

Symbols come from the real :class:`ASTParser` rather than hand-built
:class:`Symbol` objects. Wrapper confirmation depends on each symbol's
``start_line``/``end_line`` being the extent ingestion actually recorded, so
fabricating those here would test this module against my own arithmetic instead
of against the parse it consumes in production.
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


def _parse(path: str, source: str):
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
    return ASTParser().parse_file(info, source.encode())


def _run(source: str, path: str = "src/lib/api/client.ts"):
    """Extract via the index path; returns ``(contracts, unresolved, ctx)``."""
    parsed = _parse(path, source)
    suffix = "." + path.rsplit(".", 1)[1]
    ctx = ScanContext("frontend", path, suffix, source, {})
    contracts, unresolved = extract_consumers(ctx, parsed)
    return contracts, unresolved, ctx


def _ids(contracts) -> set[str]:
    return {c.contract_id for c in contracts}


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
        parsed = _parse("src/lib/cache.ts", DECOY_TS)
        assert confirm_wrappers(parsed, DECOY_TS, ".ts") == set()

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

    The parse cache is version-gated, so a repo indexed by an older repowise
    loads zero entries — measured on two of three repos in this workspace. The
    regex dialect is therefore not a legacy path but the only path for those
    repos, and it has to stay reachable.
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
            index=index,
            content_hashes={},
        )

    def test_without_an_index_the_regex_dialect_still_extracts(self, tmp_path):
        got = self._extract(tmp_path, index=None)
        assert "http::GET::/legacy/ping" in _ids(got)

    def test_an_index_that_cannot_read_a_file_does_not_delete_its_contracts(
        self, tmp_path
    ):
        """An index present but missing this file must not suppress the dialect.

        This is the rule that stops a bad or partial parse silently deleting
        contracts the regex can still see.
        """
        got = self._extract(tmp_path, index={"src/other.ts": _parse("src/other.ts", "")})
        assert "http::GET::/legacy/ping" in _ids(got)

    def test_a_language_with_no_sink_patterns_confirms_nothing(self):
        parsed = _parse("src/main.go", "package main\n")
        assert confirm_wrappers(parsed, "package main\n", ".go") == set()

    def test_one_line_wrapper_bodies_are_not_lost(self):
        """A concise wrapper puts its body on the declaration line."""
        source = (
            "export class C {\n"
            '  ping() { return fetch("/x/ping"); }\n'
            "  go() { return this.ping(); }\n"
            "}\n"
        )
        parsed = _parse("src/lib/one.ts", source)
        assert "ping" in confirm_wrappers(parsed, source, ".ts")


class TestHopBudget:
    """Confirmation is bounded, so it cannot become a whole-repo graph walk."""

    def test_budget_one_still_reaches_through_the_two_hop_chain(self):
        """call site -> fetch -> request -> sink: the target is 1 hop from the sink."""
        parsed = _parse("src/lib/api/client.ts", CLIENT_TS)
        assert "fetch" in confirm_wrappers(parsed, CLIENT_TS, ".ts", budget=1)

    def test_budget_zero_confirms_only_the_symbol_holding_the_sink(self):
        parsed = _parse("src/lib/api/client.ts", CLIENT_TS)
        assert confirm_wrappers(parsed, CLIENT_TS, ".ts", budget=0) == {"request"}

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
        parsed = _parse("src/lib/deep.ts", source)
        confirmed = confirm_wrappers(parsed, source, ".ts", budget=2)
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
        parsed = _parse("src/lib/r.ts", source)
        assert confirm_wrappers(parsed, source, ".ts") == set()


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

    def test_an_axios_call_survives_beside_a_confirmed_wrapper(self, tmp_path):
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
        parsed = _parse("src/lib/mix.ts", source)
        got = HttpExtractor().extract(
            tmp_path,
            "frontend",
            None,
            files=[("src/lib/mix.ts", ".ts", source)],
            index={"src/lib/mix.ts": parsed},
            content_hashes={"src/lib/mix.ts": parsed.content_hash},
        )
        ids = _ids(got)
        assert "http::GET::/wrapped" in ids  # from the index pass
        assert "http::GET::/legacy" in ids  # only the regex dialect sees this

    def test_duplicates_are_not_emitted_twice(self, tmp_path):
        from repowise.core.workspace.extractors.http import HttpExtractor

        source = (
            "export async function load() {\n"
            "  return fetch('/dup');\n"
            "}\n"
        )
        parsed = _parse("src/lib/dup.ts", source)
        got = HttpExtractor().extract(
            tmp_path,
            "frontend",
            None,
            files=[("src/lib/dup.ts", ".ts", source)],
            index={"src/lib/dup.ts": parsed},
            content_hashes={"src/lib/dup.ts": parsed.content_hash},
        )
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
