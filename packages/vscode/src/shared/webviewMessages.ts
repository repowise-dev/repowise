/**
 * Typed message contract between the extension host and the webviews.
 *
 * This module is imported by BOTH bundles (esbuild host bundle and the Vite
 * webview bundles), so it must stay free of 'vscode' imports and of any
 * runtime dependency beyond type-only imports. Webviews never fetch: every
 * data need is an RpcRequest the host serves from the shared api-client and
 * cache; everything else is a one-way notification.
 */

import type {
  ChurnComplexityResponse,
  HealthFilesQuery,
  HealthFilesResponse,
  HealthOverviewResponse,
  HealthTrendResponse,
} from "@repowise-dev/types/health";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import type {
  ArchitectureGraphResponse,
  CommunityDetailResponse,
  CommunitySliceResponse,
  CommunitySummaryItem,
  DeadCodeGraphResponse,
  DecisionRecordResponse,
  ExecutionFlowsResponse,
  GraphExportResponse,
  GraphPathResponse,
  HotFilesGraphResponse,
  ModuleGraphResponse,
  NodeSearchResult,
  PageResponse,
} from "@repowise-dev/api-client/types";
import type { RiskRangeResponse } from "@repowise-dev/api-client/risk";
import type { ReviewerSuggestion } from "@repowise-dev/api-client/types";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import type { ArchitectureView } from "@repowise-dev/ui/c4";
import type { RefactoringPlan, RefactoringTargets } from "@repowise-dev/ui/refactoring/types";
import type { AiPromptFlavor } from "@repowise-dev/ui/health/ai-prompt-builder";

/** Every editor-tab webview panel the extension can open. */
export type PanelViewId =
  | "health"
  | "architecture"
  | "graph"
  | "refactoring"
  | "decisions"
  | "docs"
  | "risk"
  | "settings";

/** Panels plus the sidebar Home view (a WebviewView, never a tab). */
export type WebviewViewId = PanelViewId | "home";

/** Per-view open parameters, carried in the init message. */
export interface ViewParams {
  /** selectPath focuses the dashboard on one file (from the status-bar score). */
  health: { selectPath?: string };
  architecture: { selectPath?: string };
  graph: { selectNode?: string };
  refactoring: { planId?: string; filePath?: string };
  decisions: Record<string, never>;
  docs: { pageId?: string; filePath?: string };
  risk: Record<string, never>;
  settings: Record<string, never>;
  home: Record<string, never>;
}

/**
 * The settings the Settings panel can read and write. Kept in lockstep with
 * the `repowise.*` keys contributed in package.json (the extension-side source
 * of truth); the panel offers a friendlier grouped surface over the same keys,
 * and the host validates every write against this allowlist.
 */
export const SETTING_KEYS = [
  "diagnostics.enabled",
  "diagnostics.minSeverity",
  "diagnostics.dimensions",
  "gutterHeat.enabled",
  "fileDecorations.enabled",
  "fileDecorations.maxScore",
  "codeLens.enabled",
  "hover.enabled",
  "hover.symbolDetail",
  "server.autoStart",
  "server.port",
  "cliPath",
  "risk.baseBranch",
  "changeIntel.cochangeNudge",
  "changeIntel.cochangeMinScore",
] as const;

export type SettingKey = (typeof SETTING_KEYS)[number];

/** The value shapes a setting can hold across the whole allowlist. */
export type SettingValue = boolean | number | string | string[] | null;

/** Current value of every allowlisted setting, keyed by its `repowise.*` tail. */
export type SettingsValues = Record<SettingKey, SettingValue>;

/**
 * Webview color scheme. "auto" follows the editor theme; a fixed value pins
 * every Repowise webview regardless of the editor. Persisted host-side so all
 * views (and future opens) agree.
 */
export type ThemePreference = "auto" | "light" | "dark";

/** Repo facts the webview needs for labels and cache-busting. */
export interface RepoInit {
  id: string;
  name: string;
  headCommit: string | null;
  defaultBranch: string | null;
}

/**
 * Data the host can serve. One method per need; the webview client is a
 * Proxy over this interface and the host dispatcher implements it, so the
 * two sides cannot drift.
 */
export interface HostApi {
  // Health dashboard
  healthOverview(limit?: number): Promise<HealthOverviewResponse>;
  healthFiles(query?: HealthFilesQuery): Promise<HealthFilesResponse>;
  healthTrend(limit?: number): Promise<HealthTrendResponse>;
  churnComplexity(limit?: number): Promise<ChurnComplexityResponse>;
  // Architecture map
  architectureView(): Promise<ArchitectureView>;
  fileContent(filePath: string): Promise<string>;
  // Knowledge graph
  moduleGraph(): Promise<ModuleGraphResponse>;
  fullGraph(limit?: number): Promise<GraphExportResponse>;
  architectureCommunityGraph(): Promise<ArchitectureGraphResponse>;
  communities(): Promise<CommunitySummaryItem[]>;
  communitySlice(communityId: number): Promise<CommunitySliceResponse>;
  communityDetail(communityId: number): Promise<CommunityDetailResponse>;
  /** Shortest path between two graph nodes (path finder panel). */
  graphPath(from: string, to: string): Promise<GraphPathResponse>;
  /** Node-name autocomplete for the path finder inputs. */
  searchNodes(query: string, limit?: number): Promise<NodeSearchResult[]>;
  deadCodeGraph(): Promise<DeadCodeGraphResponse>;
  hotFilesGraph(): Promise<HotFilesGraphResponse>;
  executionFlows(): Promise<ExecutionFlowsResponse>;
  // Refactoring
  refactoringTargets(filePath?: string): Promise<RefactoringTargets>;
  refactoringPlan(suggestionId: string): Promise<RefactoringPlan>;
  /** Built host-side so the prompt matches the CodeLens copy path exactly. */
  refactoringPrompt(suggestionId: string, flavor: AiPromptFlavor): Promise<string>;
  // Decisions
  decisionsList(): Promise<DecisionRecordResponse[]>;
  // Docs
  pagesList(): Promise<PageResponse[]>;
  pageById(pageId: string): Promise<PageResponse>;
  fileDetail(relPath: string): Promise<FileDetailResponse>;
  // Change risk
  riskRange(): Promise<RiskRangeReport>;
  /** Impact of the current change set (uncommitted + unpushed). */
  changeImpact(): Promise<ChangeImpactReport>;
  // Sidebar home
  homeSummary(): Promise<HomeSummary>;
  // Settings panel
  /** Current value of every allowlisted setting. */
  getSettings(): Promise<SettingsValues>;
  /** Persist one setting, then echo the full fresh value map back. */
  updateSetting(key: SettingKey, value: SettingValue): Promise<SettingsValues>;
}

/**
 * The sidebar Home payload, aggregated host-side so one RPC serves the whole
 * view and only compact numbers cross the postMessage boundary. Sections are
 * independently nullable: one failing endpoint degrades its card, not the view.
 */
export interface HomeSummary {
  health: {
    /** Headline: NLOC-weighted health of the hotspot files. */
    hotspot: number | null;
    average: number | null;
    fileCount: number;
    openFindings: number;
    band: string | null;
    hotspotDelta: number | null;
    /** Hotspot-health history, oldest first, for the hero sparkline. */
    history: number[];
  } | null;
  counts: {
    refactoringPlans: number | null;
    decisions: number | null;
  };
  freshness: {
    /** Commit the index was built from (null when the server has none). */
    indexedCommit: string | null;
    /** Checked-out commit, or null when git cannot serve it. */
    liveCommit: string | null;
    /** True only when both commits are known and differ. */
    stale: boolean;
    branch: string | null;
    lastIndexedAt: string | null;
  };
}

/** Branch risk payload: the endpoint response plus the refs it compared. */
export interface RiskRangeReport {
  base: string;
  branch: string | null;
  result: RiskRangeResponse;
}

/**
 * Impact of the working change set, assembled host-side from the blast-radius
 * and reviewer-suggestion endpoints. Shared verbatim by the Change Risk panel
 * and the ambient co-change nudge so there is one analysis, not two.
 */
export interface ChangeImpactReport {
  /** Repo-relative paths analyzed, sorted (the change-set signature source). */
  changed: string[];
  /** How many of the changed paths are staged / unstaged (working tree). */
  stagedCount: number;
  workingCount: number;
  /**
   * "branch" includes committed-but-unpushed changes (base..HEAD); "working"
   * is the uncommitted tree only. The panel asks for branch scope; the nudge
   * asks for working scope so it speaks to the file being edited right now.
   */
  scope: "branch" | "working";
  /** Blast analysis, or null when git is unavailable or nothing changed. */
  blast: BlastRadiusResponse | null;
  /** Reviewer suggestions for the changed set (names + reasons). */
  reviewers: ReviewerSuggestion[];
  /** True when git could not be read at all (distinct from a clean tree). */
  gitUnavailable: boolean;
}

export type HostApiMethod = keyof HostApi;

// ---------------------------------------------------------------------------
// Envelopes
// ---------------------------------------------------------------------------

/** Webview -> host: invoke a HostApi method. */
export interface RpcRequestMessage {
  kind: "rpc-request";
  id: number;
  method: HostApiMethod;
  args: unknown[];
}

/** Host -> webview: RPC outcome. */
export interface RpcResponseMessage {
  kind: "rpc-response";
  id: number;
  ok: boolean;
  /** Present when ok. */
  result?: unknown;
  /** Present when not ok; already user-presentable. */
  error?: string;
}

/** Webview -> host: bootstrapped and listening; host replies with init. */
export interface ReadyMessage {
  kind: "ready";
}

/** Host -> webview: everything the view needs to render. */
export interface InitMessage<V extends WebviewViewId = WebviewViewId> {
  kind: "init";
  view: V;
  repo: RepoInit;
  params: ViewParams[V];
  theme: ThemePreference;
}

/**
 * Host -> webview: the index moved under the panel (repowise update finished
 * or HEAD changed). Views refetch what they show; repo.headCommit is fresh.
 */
export interface RefreshMessage {
  kind: "refresh";
  repo: RepoInit;
}

/** Webview -> host: open a repo file in an editor column. */
export interface OpenFileMessage {
  kind: "open-file";
  /** Repo-relative path. */
  path: string;
  /** 1-based line to reveal. */
  line?: number;
}

/** Webview -> host: put text on the clipboard and confirm via toast. */
export interface CopyTextMessage {
  kind: "copy-text";
  text: string;
  /** Confirmation toast; defaults to a generic one. */
  toast?: string;
}

/** Webview -> host: open an external URL in the default browser. */
export interface OpenExternalMessage {
  kind: "open-external";
  url: string;
}

/** Webview -> host: open (or reveal) an editor-tab panel. Sent by Home. */
export interface OpenViewMessage {
  kind: "open-view";
  view: PanelViewId;
  params?: ViewParams[PanelViewId];
}

/** Webview -> host: reveal the sidebar Home view. Sent by a panel's chrome. */
export interface FocusHomeMessage {
  kind: "focus-home";
}

/** Webview -> host: open the native Settings editor filtered to the extension. */
export interface OpenNativeSettingsMessage {
  kind: "open-native-settings";
}

/** Webview -> host: run an incremental index update. Sent by Home. */
export interface UpdateIndexMessage {
  kind: "update-index";
}

/** Webview -> host: persist a theme preference. Sent by Home's switcher. */
export interface SetThemeMessage {
  kind: "set-theme";
  theme: ThemePreference;
}

/** Host -> webview: the theme preference changed; every open view applies it. */
export interface ThemeChangedMessage {
  kind: "theme-changed";
  theme: ThemePreference;
}

/**
 * Host -> webview: the requested index update finished (either way). The view
 * refetches its summary; a successful update also arrives as a refresh.
 */
export interface UpdateDoneMessage {
  kind: "update-done";
}

export type WebviewToHostMessage =
  | ReadyMessage
  | RpcRequestMessage
  | OpenFileMessage
  | CopyTextMessage
  | OpenExternalMessage
  | OpenViewMessage
  | FocusHomeMessage
  | OpenNativeSettingsMessage
  | UpdateIndexMessage
  | SetThemeMessage;

export type HostToWebviewMessage =
  | InitMessage
  | RefreshMessage
  | RpcResponseMessage
  | UpdateDoneMessage
  | ThemeChangedMessage;
