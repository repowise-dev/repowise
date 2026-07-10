import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError } from "./client";
import {
  createHostedProvider,
  mapHostedDeadCodeFinding,
  mapHostedPage,
  mapHostedRepo,
  mapHostedSearchResult,
} from "./hosted";

// ---------------------------------------------------------------------------
// Shared wire fixtures (shape per the hosted API's /repos/mine and snapshot
// routes; trimmed to the fields the adapter reads)
// ---------------------------------------------------------------------------

const READY_REPO = {
  repo: {
    id: "repo-1",
    canonical_url: "github.com/acme/widgets",
    owner: "acme",
    name: "widgets",
    default_branch: "main",
    latest_known_head_sha: "aaa111",
    is_public: true,
    description: "Widget factory",
    primary_language: "Python",
    stars: 42,
    last_checked_at: "2026-07-01T00:00:00Z",
  },
  active_snapshot: {
    id: "snap-1",
    short_id: "abcdef123456",
    status: "ready",
    head_sha: "bbb222",
    completed_at: "2026-06-30T00:00:00Z",
  },
  added_at: "2026-06-01T00:00:00Z",
  team_name: null,
};

const UNINDEXED_REPO = {
  repo: {
    id: "repo-2",
    canonical_url: "github.com/acme/empty",
    owner: "acme",
    name: "empty",
    default_branch: null,
    latest_known_head_sha: null,
    is_public: false,
  },
  active_snapshot: null,
  team_name: "Platform",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A fetch stub that routes by URL substring and records every call. */
function makeFetch(routes: Array<[string, unknown]>) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const impl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    calls.push({ url: u, init: init ?? {} });
    for (const [needle, body] of routes) {
      if (u.includes(needle)) {
        return typeof body === "string" ? new Response(body, { status: 200 }) : jsonResponse(body);
      }
    }
    return jsonResponse({ detail: `no route for ${u}` }, 404);
  });
  return { impl: impl as unknown as typeof fetch, calls };
}

function provider(routes: Array<[string, unknown]>, token: string | null = "tok-1") {
  const { impl, calls } = makeFetch(routes);
  return {
    p: createHostedProvider({ baseUrl: "https://api.example.dev", token, fetch: impl }),
    calls,
  };
}

const REPOS_ROUTE: [string, unknown] = ["/repos/mine", [READY_REPO, UNINDEXED_REPO]];

// ---------------------------------------------------------------------------
// Mapping
// ---------------------------------------------------------------------------

describe("mapHostedRepo", () => {
  it("presents a hosted repo in the local RepoResponse shape", () => {
    const mapped = mapHostedRepo(READY_REPO);
    expect(mapped).toMatchObject({
      id: "repo-1",
      name: "widgets",
      url: "https://github.com/acme/widgets",
      local_path: "",
      default_branch: "main",
      head_commit: "bbb222",
      settings: {},
      hosted: true,
      owner: "acme",
      snapshot_id: "snap-1",
      snapshot_short_id: "abcdef123456",
      snapshot_status: "ready",
      last_indexed_at: "2026-06-30T00:00:00Z",
    });
  });

  it("degrades cleanly for a repo with no snapshot", () => {
    const mapped = mapHostedRepo(UNINDEXED_REPO);
    expect(mapped.head_commit).toBeNull();
    expect(mapped.snapshot_id).toBeNull();
    expect(mapped.snapshot_status).toBeNull();
    expect(mapped.default_branch).toBe("main");
    expect(mapped.team_name).toBe("Platform");
  });
});

describe("mapHostedPage", () => {
  it("keeps content fields and defaults the local-DB bookkeeping", () => {
    const page = mapHostedPage(
      {
        page_id: "file_page:src/a.py",
        id: "file_page:src/a.py",
        title: "a.py",
        content: "# a",
        page_type: "file_page",
        target_path: "src/a.py",
        metadata: { onboarding_slot: "start" },
      },
      "repo-1",
    );
    expect(page.id).toBe("file_page:src/a.py");
    expect(page.repository_id).toBe("repo-1");
    expect(page.version).toBe(1);
    expect(page.freshness_status).toBe("fresh");
    expect(page.human_notes).toBeNull();
    expect(page.metadata).toEqual({ onboarding_slot: "start" });
  });

  it("falls back to page_id when the id alias is absent (old snapshots)", () => {
    expect(mapHostedPage({ page_id: "p1" }, "r").id).toBe("p1");
  });
});

describe("mapHostedDeadCodeFinding", () => {
  it("synthesises an id and triage defaults for artifact rows", () => {
    const f = mapHostedDeadCodeFinding({
      kind: "unused_export",
      file_path: "src/a.py",
      symbol_name: "helper",
      confidence: 0.9,
      start_line: 10,
    });
    expect(f.id).toBe("src/a.py:helper:10");
    expect(f.status).toBe("open");
    expect(f.note).toBeNull();
    expect(f.risk_factors).toEqual([]);
  });
});

describe("mapHostedSearchResult", () => {
  it("fills page_id for results that lack one", () => {
    const r = mapHostedSearchResult({ title: "t", target_path: "src/a.py", score: 0.5 });
    expect(r.page_id).toBe("");
    expect(r.search_type).toBe("keyword");
  });
});

// ---------------------------------------------------------------------------
// Snapshot resolution
// ---------------------------------------------------------------------------

describe("snapshot resolution", () => {
  it("resolves via /repos/mine once and caches per repo", async () => {
    const { p, calls } = provider([
      REPOS_ROUTE,
      ["/health/overview", { kpis: { file_count: 1, average_health: 8 }, files: [], biomarkers: [] }],
    ]);
    await p.getHealthOverview("repo-1");
    await p.getHealthTrend("repo-1").catch(() => undefined);
    const repoListCalls = calls.filter((c) => c.url.includes("/repos/mine"));
    expect(repoListCalls).toHaveLength(1);
    expect(calls[1]!.url).toContain("/snapshots/snap-1/health/overview");
  });

  it("throws 404 for a repo that is not on the account", async () => {
    const { p } = provider([REPOS_ROUTE]);
    await expect(p.getHealthOverview("nope")).rejects.toMatchObject({ status: 404 });
  });

  it("throws 409 for a repo with no ready snapshot", async () => {
    const { p } = provider([REPOS_ROUTE]);
    await expect(p.getHealthOverview("repo-2")).rejects.toMatchObject({ status: 409 });
  });

  it("refresh() drops the cache so the next call re-resolves", async () => {
    const { p, calls } = provider([REPOS_ROUTE, ["/stats/highlights", {}]]);
    await p.getStatsHighlights("repo-1");
    p.refresh();
    await p.getStatsHighlights("repo-1");
    expect(calls.filter((c) => c.url.includes("/repos/mine"))).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// Request wiring
// ---------------------------------------------------------------------------

describe("request wiring", () => {
  it("attaches the bearer token from a live resolver", async () => {
    const { impl, calls } = makeFetch([REPOS_ROUTE]);
    let token: string | null = "first";
    const p = createHostedProvider({
      baseUrl: "https://api.example.dev",
      token: () => token,
      fetch: impl,
    });
    await p.listRepos();
    token = "second";
    p.refresh();
    await p.listRepos();
    const auth = (i: number) => new Headers(calls[i]!.init.headers).get("Authorization");
    expect(auth(0)).toBe("Bearer first");
    expect(auth(1)).toBe("Bearer second");
  });

  it("stays anonymous without a token (public repo reads)", async () => {
    const { impl, calls } = makeFetch([REPOS_ROUTE]);
    const p = createHostedProvider({ baseUrl: "https://api.example.dev", token: null, fetch: impl });
    await p.listRepos();
    expect(new Headers(calls[0]!.init.headers).get("Authorization")).toBeNull();
  });

  it("passes query params through on re-addressed routes", async () => {
    const { p, calls } = provider([REPOS_ROUTE, ["/health/files", []]]);
    const res = await p.listHealthFiles("repo-1", { limit: 10, sort: "score", search: "app" });
    const url = calls.at(-1)!.url;
    expect(url).toContain("/snapshots/snap-1/health/files");
    expect(url).toContain("sort=score");
    expect(url).toContain("q=app");
    expect(res).toMatchObject({ total: 0, offset: 0, limit: 10, files: [] });
  });

  it("surfaces API errors as ApiClientError with the detail", async () => {
    const { impl } = makeFetch([REPOS_ROUTE]);
    const p = createHostedProvider({ baseUrl: "https://api.example.dev", fetch: impl });
    await p.listRepos();
    // No route registered for health/overview: stub returns a 404 body.
    await expect(p.getHealthOverview("repo-1")).rejects.toBeInstanceOf(ApiClientError);
  });
});

// ---------------------------------------------------------------------------
// Docs
// ---------------------------------------------------------------------------

const DOCS_BODY = {
  available: true,
  docs_status: "ready",
  pages_ready: 2,
  pages_total: 2,
  pages: [
    { id: "overview", page_id: "overview", page_type: "overview", title: "Overview" },
    { id: "file_page:a", page_id: "file_page:a", page_type: "file_page", title: "a" },
  ],
};

describe("docs", () => {
  it("maps and caches the docs list per snapshot", async () => {
    const { p, calls } = provider([REPOS_ROUTE, ["/docs", DOCS_BODY]]);
    const all = await p.listAllPages("repo-1");
    await p.listAllPages("repo-1");
    expect(all).toHaveLength(2);
    expect(all[0]!.repository_id).toBe("repo-1");
    expect(calls.filter((c) => c.url.includes("/docs"))).toHaveLength(1);
  });

  it("listPages filters by page_type and slices", async () => {
    const { p } = provider([REPOS_ROUTE, ["/docs", DOCS_BODY]]);
    const filtered = await p.listPages("repo-1", { page_type: "file_page" });
    expect(filtered.map((pg) => pg.id)).toEqual(["file_page:a"]);
    const sliced = await p.listPages("repo-1", { limit: 1 });
    expect(sliced).toHaveLength(1);
  });

  it("getPageById finds a page and 404s on a miss", async () => {
    const { p } = provider([REPOS_ROUTE, ["/docs", DOCS_BODY]]);
    const page = await p.getPageById("overview", "repo-1");
    expect(page.title).toBe("Overview");
    await expect(p.getPageById("missing", "repo-1")).rejects.toMatchObject({ status: 404 });
  });

  it("getDocsStatus exposes the generation-progress fields", async () => {
    const { p } = provider([REPOS_ROUTE, ["/docs", DOCS_BODY]]);
    expect(await p.getDocsStatus("repo-1")).toEqual({
      available: true,
      docs_status: "ready",
      pages_ready: 2,
      pages_total: 2,
    });
  });
});

// ---------------------------------------------------------------------------
// Envelope unwrapping
// ---------------------------------------------------------------------------

describe("envelope unwrapping", () => {
  it("listDecisions unwraps the hosted envelope", async () => {
    const { p } = provider([
      REPOS_ROUTE,
      ["/decisions", { total_found: 1, decisions: [{ title: "Use X" }], by_source: {} }],
    ]);
    const decisions = await p.listDecisions("repo-1");
    expect(decisions).toEqual([{ title: "Use X" }]);
  });

  it("listDeadCode unwraps and filters client-side", async () => {
    const { p } = provider([
      REPOS_ROUTE,
      [
        "/dead-code",
        {
          total_findings: 3,
          deletable_lines: 30,
          findings: [
            { kind: "unused_export", file_path: "a.py", confidence: 0.9, safe_to_delete: true },
            { kind: "unused_export", file_path: "b.py", confidence: 0.4, safe_to_delete: false },
            { kind: "unreachable", file_path: "c.py", confidence: 0.95, safe_to_delete: true },
          ],
        },
      ],
    ]);
    const safe = await p.listDeadCode("repo-1", { kind: "unused_export", min_confidence: 0.5 });
    expect(safe.map((f) => f.file_path)).toEqual(["a.py"]);
  });

  it("search posts to the snapshot route and maps results", async () => {
    const { p, calls } = provider([
      REPOS_ROUTE,
      ["/search", { results: [{ title: "hit", target_path: "a.py", score: 1, search_type: "semantic" }] }],
    ]);
    const results = await p.search("widgets", { repo_id: "repo-1", limit: 5 });
    expect(results[0]).toMatchObject({ title: "hit", page_id: "", search_type: "semantic" });
    const call = calls.at(-1)!;
    expect(call.url).toContain("/snapshots/snap-1/search");
    expect(JSON.parse(String(call.init.body))).toEqual({ query: "widgets", limit: 5 });
  });

  it("search without repo_id is rejected", async () => {
    const { p } = provider([REPOS_ROUTE]);
    await expect(p.search("q")).rejects.toMatchObject({ status: 400 });
  });
});

// ---------------------------------------------------------------------------
// Indexing lifecycle
// ---------------------------------------------------------------------------

describe("indexing lifecycle", () => {
  it("triggerReindex posts to the repo route with the docs flag", async () => {
    const { p, calls } = provider([
      ["/repos/repo-1/reindex", { snapshot_id: "snap-2", short_id: "s2", status: "queued", cached: false }],
    ]);
    const res = await p.triggerReindex("repo-1", { generate_docs: true });
    expect(res.status).toBe("queued");
    const call = calls.at(-1)!;
    expect(call.init.method).toBe("POST");
    expect(JSON.parse(String(call.init.body))).toEqual({ generate_docs: true });
  });

  it("getSnapshotStatus polls the snapshot row", async () => {
    const { p, calls } = provider([["/snapshots/snap-2", { id: "snap-2", status: "indexing" }]]);
    const status = await p.getSnapshotStatus("snap-2");
    expect(status.status).toBe("indexing");
    expect(calls[0]!.url).toContain("/snapshots/snap-2");
  });
});
