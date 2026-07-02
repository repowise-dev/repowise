import * as vscode from "vscode";
import {
  getChurnComplexity,
  getHealthOverview,
  getHealthTrend,
  listHealthFiles,
} from "@repowise-dev/api-client/code-health";
import { getArchitectureView } from "@repowise-dev/api-client/c4";
import { getFileContent, getFileDetail } from "@repowise-dev/api-client/files";
import {
  getArchitectureCommunityGraph,
  getCommunities,
  getCommunityDetail,
  getCommunitySlice,
  getDeadCodeGraph,
  getExecutionFlows,
  getGraph,
  getGraphPath,
  getHotFilesGraph,
  getModuleGraph,
  searchNodes,
} from "@repowise-dev/api-client/graph";
import {
  getRefactoringPlan,
  getRefactoringTargets,
} from "@repowise-dev/api-client/refactoring";
import { listDecisions } from "@repowise-dev/api-client/decisions";
import { getPageById, listAllPages } from "@repowise-dev/api-client/pages";
import { getRiskRange } from "@repowise-dev/api-client/risk";
import { buildRefactoringPlanPrompt } from "@repowise-dev/ui/health/ai-prompt-builder";
import { CONFIG_SECTION } from "../constants";
import {
  SETTING_KEYS,
  type HomeSummary,
  type HostApi,
  type RiskRangeReport,
  type SettingKey,
  type SettingsValues,
  type SettingValue,
} from "../shared/webviewMessages";
import type { RepowiseContext } from "./context";
import { commitsMatch, getCurrentBranchName, resolveLiveHead } from "./gitApi";

/** Thrown by the dispatcher when a request arrives while disconnected. */
class NotReadyError extends Error {
  constructor() {
    super("The Repowise server is not connected.");
  }
}

/** Defaults mirrored from the package.json contribution, for a robust read. */
const SETTING_DEFAULTS: SettingsValues = {
  "diagnostics.enabled": true,
  "diagnostics.minSeverity": "high",
  "diagnostics.dimensions": ["defect", "maintainability", "performance"],
  "gutterHeat.enabled": true,
  "fileDecorations.enabled": true,
  "fileDecorations.maxScore": 4,
  "codeLens.enabled": true,
  "hover.enabled": true,
  "server.autoStart": "ask",
  "server.port": null,
  "cliPath": "",
  "risk.baseBranch": "",
};

const SEVERITIES = ["critical", "high", "medium", "low"];
const DIMENSIONS = ["defect", "maintainability", "performance"];

/**
 * Per-key validator for writes. The webview is untrusted input, so an out-of-
 * shape value is rejected before it reaches config.update rather than trusting
 * VS Code to coerce it. Returns the value to write, or throws.
 */
const SETTING_VALIDATORS: Record<SettingKey, (v: SettingValue) => SettingValue> = {
  "diagnostics.enabled": expectBoolean,
  "diagnostics.minSeverity": (v) => expectEnum(v, SEVERITIES),
  "diagnostics.dimensions": (v) => expectStringSubset(v, DIMENSIONS),
  "gutterHeat.enabled": expectBoolean,
  "fileDecorations.enabled": expectBoolean,
  "fileDecorations.maxScore": (v) => expectNumberInRange(v, 0, 10),
  "codeLens.enabled": expectBoolean,
  "hover.enabled": expectBoolean,
  "server.autoStart": (v) => expectEnum(v, ["ask", "always", "never"]),
  "server.port": expectNullablePort,
  "cliPath": expectString,
  "risk.baseBranch": expectString,
};

function expectBoolean(v: SettingValue): boolean {
  if (typeof v !== "boolean") throw new Error("Expected a boolean.");
  return v;
}
function expectString(v: SettingValue): string {
  if (typeof v !== "string") throw new Error("Expected a string.");
  return v;
}
function expectEnum(v: SettingValue, allowed: string[]): string {
  if (typeof v !== "string" || !allowed.includes(v)) throw new Error("Value not allowed.");
  return v;
}
function expectStringSubset(v: SettingValue, allowed: string[]): string[] {
  if (!Array.isArray(v) || v.some((x) => !allowed.includes(x))) {
    throw new Error("Value not allowed.");
  }
  // De-dupe and preserve the canonical order so the array stays stable.
  return allowed.filter((x) => v.includes(x));
}
function expectNumberInRange(v: SettingValue, min: number, max: number): number {
  if (typeof v !== "number" || !Number.isFinite(v) || v < min || v > max) {
    throw new Error(`Expected a number in [${min}, ${max}].`);
  }
  return v;
}
function expectNullablePort(v: SettingValue): number | null {
  if (v === null) return null;
  if (typeof v !== "number" || !Number.isInteger(v) || v < 1 || v > 65535) {
    throw new Error("Expected a port in [1, 65535] or null.");
  }
  return v;
}

/** Reads every allowlisted setting into a plain value map (defaults applied). */
function readSettings(): SettingsValues {
  const cfg = vscode.workspace.getConfiguration(CONFIG_SECTION);
  const out = {} as SettingsValues;
  for (const key of SETTING_KEYS) {
    out[key] = cfg.get<SettingValue>(key, SETTING_DEFAULTS[key]);
  }
  return out;
}

/**
 * Host-side implementation of the webview data contract. Every method runs an
 * api-client fetcher against the connected server, memoized in the shared
 * cache under the indexed head commit plus a refresh epoch the panel manager
 * bumps on every refresh broadcast (so a null or unchanged head commit can
 * never serve stale data after an index update). Branch risk is the
 * exception: it scores the working HEAD, which moves independently of the
 * index, so it always fetches fresh.
 *
 * Cache keys are namespaced `wv:` to stay clear of the tree/hover/lens keys.
 */
export function createHostApi(ctx: RepowiseContext, epoch: () => number): HostApi {
  function repoId(): string {
    const id = ctx.repoId;
    if (!id || ctx.getExtensionState() !== "ready") throw new NotReadyError();
    return id;
  }

  // Concurrent callers of the same key share one request instead of racing
  // (two panels opening together, or one view fanning out related slices).
  const inFlight = new Map<string, Promise<unknown>>();

  /** Serves from the tagged cache; concurrent misses share one request. */
  function cached<T>(key: string, load: (id: string) => Promise<T>): Promise<T> {
    const id = repoId();
    const tag = `${ctx.repo?.head_commit ?? ""}#${epoch()}`;
    const hit = ctx.cache.get<T>(id, `wv:${key}`, tag);
    if (hit !== undefined) return Promise.resolve(hit);
    const flightKey = `${id}|${key}|${tag}`;
    const pending = inFlight.get(flightKey);
    if (pending) return pending as Promise<T>;
    const request = load(id)
      .then((value) => {
        ctx.cache.set(id, `wv:${key}`, tag, value);
        return value;
      })
      .finally(() => {
        inFlight.delete(flightKey);
      });
    inFlight.set(flightKey, request);
    return request;
  }

  /**
   * Aggregates the sidebar Home payload. Sub-fetches go through the same
   * cached() keys the dashboards use (limits included), so opening a dashboard
   * after the sidebar costs nothing extra. Refactoring uses the lighter
   * medium-confidence query, not the panel's unfiltered multi-megabyte one.
   * allSettled: one failing endpoint degrades its section to null.
   */
  async function homeSummary(): Promise<HomeSummary> {
    const id = repoId();
    const [overview, trend, refactor, decisions, liveCommit, branch] =
      await Promise.allSettled([
        cached("health:overview:25", () => getHealthOverview(id, 25)),
        cached("health:trend:20", () => getHealthTrend(id, 20)),
        cached("home:refactor", () =>
          getRefactoringTargets(id, { minConfidence: "medium" }),
        ),
        cached("decisions:timeline", () =>
          listDecisions(id, { include_proposed: true, limit: 500 }),
        ),
        resolveLiveHead(ctx.workspace.repoRoot ?? ""),
        getCurrentBranchName(ctx.workspace.repoRoot ?? ""),
      ]);

    const summary = overview.status === "fulfilled" ? overview.value.summary : null;
    const trendVal = trend.status === "fulfilled" ? trend.value : null;
    const indexedCommit =
      (overview.status === "fulfilled" ? overview.value.meta?.head_commit : null) ??
      ctx.repo?.head_commit ??
      null;
    const live = liveCommit.status === "fulfilled" ? liveCommit.value : null;
    return {
      health: summary
        ? {
            hotspot: summary.hotspot_health ?? null,
            average: summary.average_health ?? null,
            fileCount: summary.file_count,
            openFindings: summary.open_findings,
            band: summary.band ?? null,
            hotspotDelta: trendVal?.summary?.hotspot_delta ?? null,
            history: (trendVal?.history ?? [])
              .slice()
              .reverse()
              .map((p) => p.hotspot_health),
          }
        : null,
      counts: {
        refactoringPlans:
          refactor.status === "fulfilled" ? refactor.value.plans.length : null,
        decisions: decisions.status === "fulfilled" ? decisions.value.length : null,
      },
      freshness: {
        indexedCommit,
        liveCommit: live,
        stale: Boolean(indexedCommit && live && !commitsMatch(indexedCommit, live)),
        branch: branch.status === "fulfilled" ? branch.value : null,
        lastIndexedAt:
          overview.status === "fulfilled"
            ? (overview.value.meta?.last_indexed_at ?? null)
            : null,
      },
    };
  }

  return {
    // Health dashboard
    healthOverview: (limit) => cached(`health:overview:${limit ?? ""}`, (id) => getHealthOverview(id, limit)),
    healthFiles: (query) =>
      cached(`health:files:${JSON.stringify(query ?? {})}`, (id) => listHealthFiles(id, query)),
    healthTrend: (limit) => cached(`health:trend:${limit ?? ""}`, (id) => getHealthTrend(id, limit)),
    churnComplexity: (limit) =>
      cached(`health:churn:${limit ?? ""}`, (id) => getChurnComplexity(id, limit != null ? { limit } : undefined)),

    // Architecture map
    architectureView: () => cached("arch:view", (id) => getArchitectureView(id)),
    // Working-tree content, not index content: never cached under head_commit.
    fileContent: (filePath) => getFileContent(repoId(), filePath),

    // Knowledge graph
    moduleGraph: () => cached("graph:modules", (id) => getModuleGraph(id)),
    fullGraph: (limit) => cached(`graph:full:${limit ?? ""}`, (id) => getGraph(id, limit)),
    architectureCommunityGraph: () =>
      cached("graph:architecture", (id) => getArchitectureCommunityGraph(id)),
    communities: () => cached("graph:communities", (id) => getCommunities(id)),
    communitySlice: (communityId) =>
      cached(`graph:slice:${communityId}`, (id) => getCommunitySlice(id, communityId)),
    communityDetail: (communityId) =>
      cached(`graph:community:${communityId}`, (id) => getCommunityDetail(id, communityId)),
    graphPath: (from, to) =>
      cached(`graph:path:${from}|${to}`, (id) => getGraphPath(id, from, to)),
    // Autocomplete over an unbounded query space: live, never cached.
    searchNodes: (query, limit) => searchNodes(repoId(), query, limit),
    deadCodeGraph: () => cached("graph:dead", (id) => getDeadCodeGraph(id)),
    hotFilesGraph: () => cached("graph:hot", (id) => getHotFilesGraph(id)),
    executionFlows: () => cached("graph:flows", (id) => getExecutionFlows(id)),

    // Refactoring
    refactoringTargets: (filePath) =>
      cached(`refactor:targets:${filePath ?? ""}`, (id) =>
        getRefactoringTargets(id, filePath ? { filePath } : {}),
      ),
    refactoringPlan: (suggestionId) =>
      cached(`refactor:plan:${suggestionId}`, (id) => getRefactoringPlan(id, suggestionId)),
    refactoringPrompt: async (suggestionId, flavor) => {
      const plan = await cached(`refactor:plan:${suggestionId}`, (id) =>
        getRefactoringPlan(id, suggestionId),
      );
      const repoName = ctx.repo?.name;
      return buildRefactoringPlanPrompt({ plan, flavor, ...(repoName ? { repoName } : {}) });
    },

    // Decisions
    decisionsList: () =>
      cached("decisions:timeline", (id) => listDecisions(id, { include_proposed: true, limit: 500 })),

    // Docs
    pagesList: () => cached("docs:pages", (id) => listAllPages(id)),
    pageById: (pageId) => cached(`docs:page:${pageId}`, () => getPageById(pageId)),
    fileDetail: (relPath) => cached(`docs:file:${relPath}`, (id) => getFileDetail(id, relPath)),

    // Branch risk
    riskRange: async (): Promise<RiskRangeReport> => {
      const id = repoId();
      const configured = vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<string>("risk.baseBranch", "")
        .trim();
      const base = configured || ctx.repo?.default_branch || "main";
      const result = await getRiskRange(id, { base, head: "HEAD" });
      const branch = await getCurrentBranchName(ctx.workspace.repoRoot ?? "");
      return { base, branch, result };
    },

    // Sidebar home
    homeSummary,

    // Settings panel
    getSettings: async () => readSettings(),
    updateSetting: async (key, value) => {
      if (!SETTING_KEYS.includes(key)) throw new Error(`Unknown setting: ${String(key)}`);
      const validated = SETTING_VALIDATORS[key](value);
      // Workspace scope when a folder is open (the common case, so the choice
      // travels with the repo); user scope otherwise so the write still lands.
      const target = vscode.workspace.workspaceFolders?.length
        ? vscode.ConfigurationTarget.Workspace
        : vscode.ConfigurationTarget.Global;
      await vscode.workspace.getConfiguration(CONFIG_SECTION).update(key, validated, target);
      return readSettings();
    },
  };
}
