/**
 * Hosted-platform provider: the same typed read surface as the per-domain
 * modules in this package, addressed at the hosted repowise API instead of a
 * local server.
 *
 * The hosted API serves the same artifact JSON the local pipeline produces,
 * but reads are snapshot-addressed (`/snapshots/{id}/...`) where the local
 * server is repo-addressed (`/api/repos/{id}/...`). The provider bridges
 * that: every method takes the same arguments as its local counterpart and
 * resolves the repo's latest ready snapshot internally (resolved once per
 * repo and cached; `refresh()` clears the cache after a reindex).
 *
 * Unlike `configureApiClient`, which sets module-global state, this is an
 * instance factory: a host app can hold a hosted provider per account next
 * to the local singleton client and route per repo.
 */

import type { ArchitectureView, C4L1, C4L2, C4L3 } from "@repowise-dev/ui/c4";
import type { CouplingGraphResponse } from "@repowise-dev/types/coupling";
import type { FileDetailResponse, FilesIndexResponse } from "@repowise-dev/types/files";
import type {
  ChurnComplexityResponse,
  HealthCoverageResponse,
  HealthFileBreakdownResponse,
  HealthFilesQuery,
  HealthFilesResponse,
  HealthFinding,
  HealthOverviewResponse,
  HealthTrendResponse,
  RefactoringQuery,
  RefactoringTargetsResponse,
} from "@repowise-dev/types/health";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import type { SymbolDetailResponse } from "@repowise-dev/types/symbols";
import { ApiClientError } from "./client";
import type { ModuleHealthSortKey } from "./modules";
import type {
  ArchitectureGraphResponse,
  CommunitySliceResponse,
  DeadCodeFindingResponse,
  DecisionRecordResponse,
  ExecutionFlowsResponse,
  GitSummaryResponse,
  HotspotResponse,
  ModuleGraphResponse,
  ModuleHealthDetail,
  ModuleHealthSummary,
  Paginated,
  PageResponse,
  RepoResponse,
  SearchResultResponse,
} from "./types";

// ---------------------------------------------------------------------------
// Config + hosted-only response types
// ---------------------------------------------------------------------------

export interface HostedProviderConfig {
  /** Absolute origin of the hosted API, e.g. "https://api.repowise.dev". */
  baseUrl: string;
  /**
   * Bearer token: an OAuth access token or an rw_live_ API key. May be a
   * resolver called fresh on every request (same convention as
   * `configureApiClient`). Reads on public repos work without one.
   */
  token?: string | null | (() => string | null);
  fetch?: typeof fetch;
}

export type HostedSnapshotState = "queued" | "indexing" | "ready" | "failed";

/** Snapshot row served by GET /snapshots/{id}; the reindex polling target. */
export interface HostedSnapshotStatus {
  id: string;
  short_id: string;
  repo_id: string;
  head_sha: string | null;
  status: HostedSnapshotState;
  error_message: string | null;
  file_count: number | null;
  line_count: number | null;
  ref: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface HostedReindexResponse {
  snapshot_id: string;
  short_id: string;
  status: HostedSnapshotState;
  /** True when the head commit was already indexed and no new run started. */
  cached: boolean;
}

/**
 * A hosted repo presented in the local `RepoResponse` shape (so repo-list
 * components render it unchanged) plus hosted-only context. `local_path` is
 * always "" and `hosted` is the discriminant.
 */
export interface HostedRepoResponse extends RepoResponse {
  hosted: true;
  owner: string;
  is_public: boolean;
  description: string | null;
  primary_language: string | null;
  stars: number | null;
  /** Team that shares this repo with the account, if any. */
  team_name: string | null;
  snapshot_id: string | null;
  snapshot_short_id: string | null;
  snapshot_status: HostedSnapshotState | null;
  last_indexed_at: string | null;
}

/** Docs payload with the hosted generation-progress fields. */
export interface HostedDocsStatus {
  available: boolean;
  docs_status: string | null;
  pages_ready: number;
  pages_total: number | null;
}

// Wire shapes of the hosted endpoints the provider maps (not re-exported).

interface WireSnapshot {
  id?: string;
  short_id?: string;
  status?: string;
  head_sha?: string | null;
  completed_at?: string | null;
}

interface WireUserRepo {
  repo: {
    id: string;
    canonical_url: string;
    owner: string;
    name: string;
    default_branch: string | null;
    latest_known_head_sha: string | null;
    is_public: boolean;
    description?: string | null;
    primary_language?: string | null;
    stars?: number | null;
    last_checked_at?: string | null;
  };
  active_snapshot: WireSnapshot | null;
  added_at?: string | null;
  team_name?: string | null;
}

interface WireDocs extends HostedDocsStatus {
  pages: Array<Record<string, unknown>>;
}

interface WireDecisions {
  total_found: number;
  decisions: Array<Record<string, unknown>>;
  by_source: Record<string, number>;
}

interface WireDeadCode {
  total_findings: number;
  deletable_lines: number;
  findings: Array<Record<string, unknown>>;
}

interface WireSearch {
  results: Array<Record<string, unknown>>;
}

interface WireFindings {
  items: HealthFinding[];
  total: number;
}

interface WireTrend {
  points: Array<{
    taken_at: string;
    hotspot_health: number;
    average_health: number;
    worst_performer_path?: string | null;
    worst_performer_score?: number | null;
  }>;
  declining: boolean;
  predicted_decline: boolean;
}

const SEVERITY_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

interface WireHealthOverview {
  kpis: Record<string, unknown> & {
    file_count?: number;
    average_health?: number;
    worst_performer_path?: string | null;
    worst_performer_score?: number | null;
  };
  files: HealthOverviewResponse["files"];
  /** Top finding rows (the hosted counterpart of the local `top_findings`). */
  biomarkers: HealthFinding[];
  modules?: HealthOverviewResponse["modules"];
  defect_accuracy?: HealthOverviewResponse["defect_accuracy"];
  meta?: HealthOverviewResponse["meta"];
}

interface WireFilesIndex {
  files: FilesIndexResponse["files"];
  language_counts: Record<string, number>;
  totals: { files?: number; loc?: number };
}

// ---------------------------------------------------------------------------
// Mapping helpers (exported for tests)
// ---------------------------------------------------------------------------

export function mapHostedRepo(item: WireUserRepo): HostedRepoResponse {
  const { repo, active_snapshot: snap } = item;
  return {
    id: repo.id,
    name: repo.name,
    url: `https://${repo.canonical_url}`,
    local_path: "",
    default_branch: repo.default_branch ?? "main",
    head_commit: snap?.head_sha ?? repo.latest_known_head_sha ?? null,
    settings: {},
    created_at: item.added_at ?? "",
    updated_at: repo.last_checked_at ?? item.added_at ?? "",
    hosted: true,
    owner: repo.owner,
    is_public: repo.is_public,
    description: repo.description ?? null,
    primary_language: repo.primary_language ?? null,
    stars: repo.stars ?? null,
    team_name: item.team_name ?? null,
    snapshot_id: snap?.id ?? null,
    snapshot_short_id: snap?.short_id ?? null,
    snapshot_status: (snap?.status as HostedSnapshotState | undefined) ?? null,
    last_indexed_at: snap?.completed_at ?? null,
  };
}

/**
 * A pages.json entry into the local `PageResponse` shape. The artifact
 * carries the content fields; the local-DB bookkeeping fields (token counts,
 * versioning) do not exist on hosted and get neutral defaults.
 */
export function mapHostedPage(raw: Record<string, unknown>, repoId: string): PageResponse {
  const str = (k: string): string => (typeof raw[k] === "string" ? (raw[k] as string) : "");
  const num = (k: string, fallback = 0): number =>
    typeof raw[k] === "number" ? (raw[k] as number) : fallback;
  return {
    id: str("id") || str("page_id"),
    repository_id: repoId,
    page_type: str("page_type"),
    title: str("title"),
    content: str("content"),
    target_path: str("target_path"),
    source_hash: str("source_hash"),
    model_name: str("model_name"),
    provider_name: str("provider_name"),
    input_tokens: num("input_tokens"),
    output_tokens: num("output_tokens"),
    cached_tokens: num("cached_tokens"),
    generation_level: num("generation_level"),
    version: num("version", 1),
    confidence: num("confidence", 1),
    freshness_status: str("freshness_status") || "fresh",
    metadata: (raw.metadata as Record<string, unknown> | undefined) ?? {},
    human_notes: null,
    created_at: str("created_at"),
    updated_at: str("updated_at") || str("created_at"),
  };
}

/** A dead_code.json finding into the local finding shape (artifact rows lack
 * the local-DB triage fields, which default to an open, note-less finding). */
export function mapHostedDeadCodeFinding(raw: Record<string, unknown>): DeadCodeFindingResponse {
  const partial = raw as Partial<DeadCodeFindingResponse>;
  return {
    id:
      partial.id ??
      `${partial.file_path ?? ""}:${partial.symbol_name ?? ""}:${partial.start_line ?? 0}`,
    kind: partial.kind ?? "",
    file_path: partial.file_path ?? "",
    symbol_name: partial.symbol_name ?? null,
    symbol_kind: partial.symbol_kind ?? null,
    confidence: partial.confidence ?? 0,
    reason: partial.reason ?? "",
    lines: partial.lines ?? 0,
    start_line: partial.start_line ?? null,
    end_line: partial.end_line ?? null,
    safe_to_delete: partial.safe_to_delete ?? false,
    risk_factors: partial.risk_factors ?? [],
    evidence: partial.evidence ?? [],
    primary_owner: partial.primary_owner ?? null,
    status: partial.status ?? "open",
    note: partial.note ?? null,
  };
}

export function mapHostedSearchResult(raw: Record<string, unknown>): SearchResultResponse {
  const partial = raw as Partial<SearchResultResponse>;
  return {
    page_id: partial.page_id ?? "",
    title: partial.title ?? "",
    page_type: partial.page_type ?? "",
    target_path: partial.target_path ?? "",
    score: partial.score ?? 0,
    snippet: partial.snippet ?? "",
    search_type: partial.search_type ?? "keyword",
  };
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

type QueryParams = Record<string, string | number | boolean | undefined>;

export interface HostedProvider {
  /** Personal + team repos of the signed-in account, in RepoResponse shape. */
  listRepos(): Promise<HostedRepoResponse[]>;
  /** Drops the cached repo list and snapshot ids (e.g. after a reindex). */
  refresh(): void;

  // Overview / stats / health (identical shapes, re-addressed)
  getOverviewSummary(repoId: string): Promise<OverviewSummaryResponse>;
  getStatsHighlights(repoId: string): Promise<StatsHighlights>;
  getHealthOverview(repoId: string, limit?: number): Promise<HealthOverviewResponse>;
  listHealthFindings(
    repoId: string,
    opts?: {
      biomarker_type?: string;
      file_path?: string;
      min_severity?: string;
      dimension?: string;
    },
  ): Promise<HealthFinding[]>;
  listHealthFiles(repoId: string, opts?: HealthFilesQuery): Promise<HealthFilesResponse>;
  getHealthFileBreakdown(repoId: string, filePath: string): Promise<HealthFileBreakdownResponse>;
  getHealthTrend(repoId: string, limit?: number): Promise<HealthTrendResponse>;
  getHealthCoverage(
    repoId: string,
    opts?: { file_path?: string; limit?: number },
  ): Promise<HealthCoverageResponse>;
  getRefactoringTargets(
    repoId: string,
    opts?: RefactoringQuery,
  ): Promise<RefactoringTargetsResponse>;
  getChurnComplexity(repoId: string, opts?: { limit?: number }): Promise<ChurnComplexityResponse>;

  // Modules
  listModuleHealth(
    repoId: string,
    options?: { sort?: ModuleHealthSortKey; limit?: number; offset?: number },
  ): Promise<Paginated<ModuleHealthSummary>>;
  getModuleHealth(repoId: string, modulePath: string): Promise<ModuleHealthDetail>;

  // Architecture
  getC4L1(repoId: string): Promise<C4L1>;
  getC4L2(repoId: string): Promise<C4L2>;
  getC4L3(repoId: string, containerId: string): Promise<C4L3>;
  getC4Mermaid(repoId: string, level: 1 | 2 | 3, containerId?: string | null): Promise<string>;
  getArchitectureView(repoId: string, includeSymbols?: boolean): Promise<ArchitectureView>;
  getArchitecture(repoId: string, minMembers?: number): Promise<ArchitectureGraphResponse>;
  getCommunitySlice(repoId: string, communityId: number): Promise<CommunitySliceResponse>;
  getModuleGraph(repoId: string): Promise<ModuleGraphResponse>;
  getExecutionFlows(
    repoId: string,
    params?: { top_n?: number; max_depth?: number; entry_point?: string },
  ): Promise<ExecutionFlowsResponse>;
  getCoupling(repoId: string, opts?: { limit?: number }): Promise<CouplingGraphResponse>;

  // Files / symbols
  getFilesIndex(repoId: string): Promise<FilesIndexResponse>;
  getFileDetail(repoId: string, filePath: string): Promise<FileDetailResponse>;
  getSymbolDetail(repoId: string, symbolId: string): Promise<SymbolDetailResponse>;

  // Docs
  getDocsStatus(repoId: string): Promise<HostedDocsStatus>;
  listPages(
    repoId: string,
    opts?: { page_type?: string; limit?: number; offset?: number },
  ): Promise<PageResponse[]>;
  listAllPages(repoId: string, opts?: { page_type?: string }): Promise<PageResponse[]>;
  getPageById(pageId: string, repoId?: string): Promise<PageResponse>;

  // Decisions / risk / dead code
  listDecisions(repoId: string, opts?: { limit?: number }): Promise<DecisionRecordResponse[]>;
  listDeadCode(
    repoId: string,
    opts?: {
      kind?: string;
      min_confidence?: number;
      status?: string;
      safe_only?: boolean;
      limit?: number;
    },
  ): Promise<DeadCodeFindingResponse[]>;
  getHotspotsPage(
    repoId: string,
    options?: { limit?: number; offset?: number },
  ): Promise<Paginated<HotspotResponse>>;
  getHotspots(repoId: string, limit?: number): Promise<HotspotResponse[]>;
  getCoChanges(
    repoId: string,
    filePath: string,
    minCount?: number,
  ): Promise<{
    file_path: string;
    co_change_partners: Array<{ file_path: string; co_change_count: number }>;
    total?: number;
  }>;
  getGitSummary(repoId: string, topOwnersLimit?: number): Promise<GitSummaryResponse>;

  // Search
  search(
    query: string,
    opts?: { search_type?: "semantic" | "fulltext"; limit?: number; repo_id?: string },
  ): Promise<SearchResultResponse[]>;

  // Indexing lifecycle
  triggerReindex(
    repoId: string,
    opts?: { generate_docs?: boolean; docs_file_percentage?: number },
  ): Promise<HostedReindexResponse>;
  getSnapshotStatus(snapshotId: string): Promise<HostedSnapshotStatus>;
}

export function createHostedProvider(config: HostedProviderConfig): HostedProvider {
  const baseUrl = config.baseUrl.replace(/\/+$/, "");

  function resolveToken(): string | null {
    const { token } = config;
    if (typeof token === "function") return token();
    return token ?? null;
  }

  function buildUrl(path: string, params?: QueryParams): string {
    const url = new URL(`${baseUrl}${path}`);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
      }
    }
    return url.toString();
  }

  function headers(): Headers {
    const h = new Headers({ "Content-Type": "application/json" });
    const token = resolveToken();
    if (token) h.set("Authorization", `Bearer ${token}`);
    return h;
  }

  async function handle<T>(res: Response): Promise<T> {
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
        else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
      } catch {
        // response body is not JSON
      }
      throw new ApiClientError(res.status, detail);
    }
    return res.json() as Promise<T>;
  }

  async function get<T>(path: string, params?: QueryParams): Promise<T> {
    const res = await (config.fetch ?? fetch)(buildUrl(path, params), {
      method: "GET",
      headers: headers(),
    });
    return handle<T>(res);
  }

  async function getText(path: string, params?: QueryParams): Promise<string> {
    const res = await (config.fetch ?? fetch)(buildUrl(path, params), {
      method: "GET",
      headers: headers(),
    });
    if (!res.ok) throw new ApiClientError(res.status, res.statusText);
    return res.text();
  }

  async function post<T>(path: string, body?: unknown): Promise<T> {
    const res = await (config.fetch ?? fetch)(buildUrl(path), {
      method: "POST",
      headers: headers(),
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    return handle<T>(res);
  }

  // Repo handle -> snapshot id, resolved from the repo list and cached for
  // the provider's lifetime (a snapshot's content is immutable; a reindex
  // mints a new id, surfaced via refresh()).
  let repos: HostedRepoResponse[] | null = null;
  let reposInFlight: Promise<HostedRepoResponse[]> | null = null;
  const snapshotByRepo = new Map<string, string | null>();
  const pagesBySnapshot = new Map<string, PageResponse[]>();

  function listRepos(): Promise<HostedRepoResponse[]> {
    // Single-flight: concurrent calls (e.g. parallel dashboard requests all
    // resolving their snapshot) share one /repos/mine round trip.
    if (reposInFlight) return reposInFlight;
    reposInFlight = (async () => {
      try {
        const items = await get<WireUserRepo[]>("/repos/mine");
        repos = items.map(mapHostedRepo);
        for (const r of repos) {
          snapshotByRepo.set(r.id, r.snapshot_status === "ready" ? r.snapshot_id : null);
        }
        return repos;
      } finally {
        reposInFlight = null;
      }
    })();
    return reposInFlight;
  }

  async function snapshot(repoId: string): Promise<string> {
    if (!snapshotByRepo.has(repoId)) await listRepos();
    if (!snapshotByRepo.has(repoId)) {
      throw new ApiClientError(404, `Repository ${repoId} is not on your hosted account`);
    }
    const sid = snapshotByRepo.get(repoId);
    if (!sid) {
      throw new ApiClientError(409, "This repository has no ready hosted snapshot yet");
    }
    return sid;
  }

  async function snapGet<T>(repoId: string, suffix: string, params?: QueryParams): Promise<T> {
    return get<T>(`/snapshots/${await snapshot(repoId)}${suffix}`, params);
  }

  async function getDocs(repoId: string): Promise<WireDocs> {
    return snapGet<WireDocs>(repoId, "/docs");
  }

  async function loadPages(repoId: string): Promise<PageResponse[]> {
    const sid = await snapshot(repoId);
    const cached = pagesBySnapshot.get(sid);
    if (cached) return cached;
    const docs = await getDocs(repoId);
    const mapped = (docs.pages ?? []).map((p) => mapHostedPage(p, repoId));
    pagesBySnapshot.set(sid, mapped);
    return mapped;
  }

  return {
    listRepos,
    refresh(): void {
      repos = null;
      snapshotByRepo.clear();
      pagesBySnapshot.clear();
    },

    getOverviewSummary: (repoId) => snapGet(repoId, "/overview-summary"),
    getStatsHighlights: (repoId) => snapGet(repoId, "/stats/highlights"),
    async getHealthOverview(repoId, limit = 25): Promise<HealthOverviewResponse> {
      // Hosted splits what the local route serves in one payload: the KPI
      // block is named differently, the top finding rows live under
      // `biomarkers`, the distribution has its own route, and the open
      // finding count only exists as the findings-list total. Compose them.
      const [overview, distribution, findingsPage] = await Promise.all([
        snapGet<WireHealthOverview>(repoId, "/health/overview", { limit }),
        snapGet<HealthOverviewResponse["distribution"]>(repoId, "/health/distribution").catch(
          () => null,
        ),
        snapGet<WireFindings>(repoId, "/health/findings", { limit: 1 }).catch(() => null),
      ]);
      return {
        summary: {
          ...overview.kpis,
          file_count: overview.kpis.file_count ?? 0,
          average_health: overview.kpis.average_health ?? 0,
          worst_performer_path: overview.kpis.worst_performer_path ?? null,
          worst_performer_score: overview.kpis.worst_performer_score ?? null,
          open_findings: findingsPage?.total ?? overview.biomarkers?.length ?? 0,
        },
        distribution: distribution ?? null,
        defect_accuracy: overview.defect_accuracy ?? null,
        files: overview.files ?? [],
        top_findings: overview.biomarkers ?? [],
        modules: overview.modules ?? [],
        ...(overview.meta ? { meta: overview.meta } : {}),
      };
    },
    async listHealthFindings(repoId, opts): Promise<HealthFinding[]> {
      // Hosted paginates findings and filters by exact severity; the local
      // route returns a bare list with a minimum-severity filter. Fetch the
      // (server-filtered) page and apply the threshold client-side.
      const res = await snapGet<WireFindings>(repoId, "/health/findings", {
        biomarker: opts?.biomarker_type,
        file_path: opts?.file_path,
        dimension: opts?.dimension,
        limit: 500,
      });
      let items = res.items ?? [];
      if (opts?.min_severity !== undefined && opts.min_severity in SEVERITY_ORDER) {
        const floor = SEVERITY_ORDER[opts.min_severity] as number;
        items = items.filter((f) => (SEVERITY_ORDER[f.severity] ?? 0) >= floor);
      }
      return items;
    },
    async listHealthFiles(repoId, opts): Promise<HealthFilesResponse> {
      // Hosted returns a bare (server-sliced) list with its own param names;
      // the local route returns a windowed envelope. Fetch a wide window and
      // apply the local windowing semantics client-side. Ceiling: repos with
      // more than 500 scored files see the worst 500 (server sort: score).
      const filter = opts?.only_hotspots
        ? "hotspots"
        : opts?.only_untested
          ? "untested"
          : opts?.only_failing
            ? "failing"
            : undefined;
      const rows = await snapGet<HealthFilesResponse["files"]>(repoId, "/health/files", {
        limit: 500,
        sort: opts?.sort,
        q: opts?.search,
        filter,
      });
      let files = rows ?? [];
      if (opts?.module) files = files.filter((f) => f.file_path.startsWith(opts.module as string));
      if (opts?.order === "desc") files = [...files].reverse();
      const offset = opts?.offset ?? 0;
      const limit = opts?.limit ?? 50;
      return { total: files.length, offset, limit, files: files.slice(offset, offset + limit) };
    },
    getHealthFileBreakdown: (repoId, filePath) =>
      snapGet(repoId, "/health/files/breakdown", { file_path: filePath }),
    async getHealthTrend(repoId, limit = 20): Promise<HealthTrendResponse> {
      // Hosted serves the raw trend points; the local route pre-computes the
      // summary block. Derive it from the last two points; alerts and
      // per-file deltas are not exposed on hosted and stay empty.
      const res = await snapGet<WireTrend>(repoId, "/health/trend");
      const history = (res.points ?? []).slice(-limit).map((p) => ({
        taken_at: p.taken_at ?? null,
        hotspot_health: p.hotspot_health,
        average_health: p.average_health,
        worst_performer_path: p.worst_performer_path ?? null,
        worst_performer_score: p.worst_performer_score ?? null,
      }));
      const current = history.at(-1);
      const previous = history.at(-2);
      return {
        history,
        summary: {
          current_hotspot_health: current?.hotspot_health ?? 0,
          current_average_health: current?.average_health ?? 0,
          previous_hotspot_health: previous?.hotspot_health ?? null,
          previous_average_health: previous?.average_health ?? null,
          hotspot_delta:
            current && previous ? current.hotspot_health - previous.hotspot_health : null,
          average_delta:
            current && previous ? current.average_health - previous.average_health : null,
        },
        alerts: [],
        file_deltas: [],
        snapshot_count: history.length,
      };
    },
    getHealthCoverage: (repoId, opts) => snapGet(repoId, "/health/coverage", opts),
    getRefactoringTargets: (repoId, opts) =>
      snapGet(repoId, "/health/refactoring-targets", opts as QueryParams),
    getChurnComplexity: (repoId, opts) => snapGet(repoId, "/health/churn-complexity", opts),

    listModuleHealth: (repoId, options = {}) => snapGet(repoId, "/modules/health", options),
    getModuleHealth: (repoId, modulePath) =>
      snapGet(repoId, `/modules/health/${encodeURIComponent(modulePath)}`),

    getC4L1: (repoId) => snapGet(repoId, "/c4/l1"),
    getC4L2: (repoId) => snapGet(repoId, "/c4/l2"),
    getC4L3: (repoId, containerId) => snapGet(repoId, "/c4/l3", { container_id: containerId }),
    getC4Mermaid: async (repoId, level, containerId) => {
      const params: QueryParams = { level };
      if (level === 3 && containerId) params.container_id = containerId;
      return getText(`/snapshots/${await snapshot(repoId)}/c4/mermaid`, params);
    },
    getArchitectureView: (repoId, includeSymbols = false) =>
      snapGet(repoId, "/architecture-view", { include_symbols: includeSymbols }),
    getArchitecture: (repoId) => snapGet(repoId, "/architecture"),
    getCommunitySlice: (repoId, communityId) =>
      snapGet(repoId, `/communities/${communityId}/slice`),
    getModuleGraph: (repoId) => snapGet(repoId, "/module-graph"),
    getExecutionFlows: (repoId, params) => snapGet(repoId, "/execution-flows", params),
    getCoupling: (repoId, opts) => snapGet(repoId, "/coupling", opts),

    async getFilesIndex(repoId): Promise<FilesIndexResponse> {
      // Same rows, different envelope: hosted totals/language_counts vs the
      // local total/languages.
      const res = await snapGet<WireFilesIndex>(repoId, "/files");
      return {
        files: res.files ?? [],
        total: res.totals?.files ?? (res.files ?? []).length,
        languages: Object.entries(res.language_counts ?? {}).map(([language, count]) => ({
          language,
          count,
        })),
      };
    },
    getFileDetail: (repoId, filePath) => snapGet(repoId, "/files/detail", { path: filePath }),
    getSymbolDetail: (repoId, symbolId) =>
      snapGet(repoId, "/symbols/detail", { symbol_id: symbolId }),

    async getDocsStatus(repoId): Promise<HostedDocsStatus> {
      const docs = await getDocs(repoId);
      return {
        available: docs.available,
        docs_status: docs.docs_status ?? null,
        pages_ready: docs.pages_ready ?? 0,
        pages_total: docs.pages_total ?? null,
      };
    },
    async listPages(repoId, opts): Promise<PageResponse[]> {
      let pages = await loadPages(repoId);
      if (opts?.page_type) pages = pages.filter((p) => p.page_type === opts.page_type);
      const offset = opts?.offset ?? 0;
      if (offset || opts?.limit !== undefined) {
        pages = pages.slice(offset, opts?.limit !== undefined ? offset + opts.limit : undefined);
      }
      return pages;
    },
    async listAllPages(repoId, opts): Promise<PageResponse[]> {
      const pages = await loadPages(repoId);
      return opts?.page_type ? pages.filter((p) => p.page_type === opts.page_type) : pages;
    },
    async getPageById(pageId, repoId): Promise<PageResponse> {
      const pools = repoId
        ? [await loadPages(repoId)]
        : Array.from(pagesBySnapshot.values());
      for (const pool of pools) {
        const hit = pool.find((p) => p.id === pageId);
        if (hit) return hit;
      }
      throw new ApiClientError(404, `Page not found: ${pageId}`);
    },

    async listDecisions(repoId, opts): Promise<DecisionRecordResponse[]> {
      const res = await snapGet<WireDecisions>(repoId, "/decisions", opts);
      return (res.decisions ?? []) as unknown as DecisionRecordResponse[];
    },
    async listDeadCode(repoId, opts): Promise<DeadCodeFindingResponse[]> {
      // Hosted paginates but does not filter; filters apply client-side over
      // the full report so semantics match the local server.
      const res = await snapGet<WireDeadCode>(repoId, "/dead-code", { limit: 500 });
      let findings = (res.findings ?? []).map(mapHostedDeadCodeFinding);
      if (opts?.kind) findings = findings.filter((f) => f.kind === opts.kind);
      if (opts?.min_confidence !== undefined) {
        findings = findings.filter((f) => f.confidence >= (opts.min_confidence as number));
      }
      if (opts?.status) findings = findings.filter((f) => f.status === opts.status);
      if (opts?.safe_only) findings = findings.filter((f) => f.safe_to_delete);
      return opts?.limit !== undefined ? findings.slice(0, opts.limit) : findings;
    },
    getHotspotsPage: (repoId, options = {}) => {
      const { limit = 50, offset = 0 } = options;
      return snapGet(repoId, "/hotspots", { limit, offset });
    },
    async getHotspots(repoId, limit = 50): Promise<HotspotResponse[]> {
      const page = await snapGet<Paginated<HotspotResponse>>(repoId, "/hotspots", { limit });
      return page.items;
    },
    getCoChanges: (repoId, filePath, minCount = 3) =>
      snapGet(repoId, "/co-changes", { file_path: filePath, min_count: minCount }),
    getGitSummary: (repoId, topOwnersLimit) =>
      snapGet(
        repoId,
        "/git-summary",
        topOwnersLimit ? { top_owners_limit: topOwnersLimit } : undefined,
      ),

    async search(query, opts): Promise<SearchResultResponse[]> {
      if (!opts?.repo_id) {
        throw new ApiClientError(400, "Hosted search requires a repo_id");
      }
      const sid = await snapshot(opts.repo_id);
      const res = await post<WireSearch>(`/snapshots/${sid}/search`, {
        query,
        limit: opts.limit ?? 10,
      });
      return (res.results ?? []).map(mapHostedSearchResult);
    },

    triggerReindex: (repoId, opts) =>
      post(`/repos/${repoId}/reindex`, {
        generate_docs: opts?.generate_docs ?? false,
        ...(opts?.docs_file_percentage !== undefined
          ? { docs_file_percentage: opts.docs_file_percentage }
          : {}),
      }),
    getSnapshotStatus: (snapshotId) => get(`/snapshots/${snapshotId}`),
  };
}
