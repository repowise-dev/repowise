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
  CommunitySliceResponse,
  CommunitySummaryItem,
  DeadCodeGraphResponse,
  DecisionRecordResponse,
  ExecutionFlowsResponse,
  GraphExportResponse,
  HotFilesGraphResponse,
  ModuleGraphResponse,
  PageResponse,
} from "@repowise-dev/api-client/types";
import type { RiskRangeResponse } from "@repowise-dev/api-client/risk";
import type { ArchitectureView } from "@repowise-dev/ui/c4";
import type { RefactoringPlan, RefactoringTargets } from "@repowise-dev/ui/refactoring/types";
import type { AiPromptFlavor } from "@repowise-dev/ui/health/ai-prompt-builder";

/** Every webview panel the extension can open. */
export type WebviewViewId =
  | "health"
  | "architecture"
  | "graph"
  | "refactoring"
  | "decisions"
  | "docs"
  | "risk";

/** Per-view open parameters, carried in the init message. */
export interface ViewParams {
  health: Record<string, never>;
  architecture: { selectPath?: string };
  graph: { selectNode?: string };
  refactoring: { planId?: string; filePath?: string };
  decisions: Record<string, never>;
  docs: { pageId?: string; filePath?: string };
  risk: Record<string, never>;
}

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
  // Branch risk
  riskRange(): Promise<RiskRangeReport>;
}

/** Branch risk payload: the endpoint response plus the refs it compared. */
export interface RiskRangeReport {
  base: string;
  branch: string | null;
  result: RiskRangeResponse;
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

export type WebviewToHostMessage =
  | ReadyMessage
  | RpcRequestMessage
  | OpenFileMessage
  | CopyTextMessage
  | OpenExternalMessage;

export type HostToWebviewMessage = InitMessage | RefreshMessage | RpcResponseMessage;
