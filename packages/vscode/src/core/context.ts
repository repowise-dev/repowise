import * as vscode from "vscode";
import { CONFIG_SECTION, REPO_DIR, STATE_CONTEXT_KEY, WORKSPACE_DIR } from "../constants";
import type { Logger } from "./log";
import type { RepowiseApi } from "./api";
import type { RepowiseCache } from "./cache";
import type { CliRunner } from "./cliRunner";
import {
  createFreshnessWatcher,
  type FreshnessEventKind,
  type FreshnessWatcher,
} from "./freshness";
import { detectWorkspace, type WorkspaceInfo } from "./workspace";
import type { RepoResponse } from "@repowise-dev/api-client/types";

/**
 * States the activity-bar welcome content gates on. `ready` shows no special
 * welcome. This is a coarse editor-facing state, distinct from the finer status
 * bar state.
 */
export type ExtensionState = "not-installed" | "no-index" | "server-down" | "ready";

/** Live view over the extension's settings; reads fresh on every call. */
export interface RepowiseConfig {
  /** Explicit server port override, or null to discover from the lockfile. */
  serverPort(): number | null;
  autoStart(): "ask" | "always" | "never";
  /** Configured CLI path, or "" to fall back to `repowise` on PATH. */
  cliPath(): string;
}

function createConfig(): RepowiseConfig {
  const read = () => vscode.workspace.getConfiguration(CONFIG_SECTION);
  return {
    serverPort: () => read().get<number | null>("server.port", null),
    autoStart: () => read().get<"ask" | "always" | "never">("server.autoStart", "ask"),
    cliPath: () => read().get<string>("cliPath", ""),
  };
}

/**
 * The subscription surface features receive from `events()`. Deliberately not
 * the watcher itself: the watcher is replaced on workspace rescans, while this
 * surface stays valid for the life of the context.
 */
export interface FreshnessEvents {
  readonly onDidChange: vscode.Event<FreshnessEventKind>;
}

/**
 * Optional presentation detail for a status-bar state. Only the `connected`
 * state uses it today, to show the live server version and url in the tooltip.
 */
export interface StatusBarDetail {
  version?: string;
  url?: string;
}

/** Callback a feature (the status bar) registers to receive state updates. */
export type StatusBarState =
  | "no-index"
  | "server-down"
  | "connecting"
  | "connected"
  | "version-low"
  | "untrusted";

/**
 * The single object passed to every feature's register function. It owns the
 * shared collaborators (log, config, api, cache, CLI) and the mutable runtime
 * state (workspace, freshness watchers, extension/status-bar state), so feature
 * modules stay free of global wiring.
 */
export class RepowiseContext {
  readonly config: RepowiseConfig = createConfig();

  private extensionState: ExtensionState = "no-index";
  private currentRepo: RepoResponse | null = null;
  private readonly stateEmitter = new vscode.EventEmitter<ExtensionState>();
  /**
   * Fires after the extension state settles into a new value (deduplicated).
   * Data features key off `ready`: it guarantees a base URL and, when the
   * server knows this repo, a repo id.
   */
  readonly onDidChangeExtensionState = this.stateEmitter.event;
  private freshnessWatcher: FreshnessWatcher | null = null;
  private statusBarSink: (state: StatusBarState, detail?: StatusBarDetail) => void =
    () => {};
  private workspaceInfo: WorkspaceInfo;

  constructor(
    readonly log: Logger,
    /** Workspace-scoped persisted state (`ExtensionContext.workspaceState`). */
    readonly state: vscode.Memento,
    readonly api: RepowiseApi,
    readonly cache: RepowiseCache,
    readonly cli: CliRunner,
    /** Push feature disposables here; all are disposed on deactivate. */
    readonly subscriptions: vscode.Disposable[],
  ) {
    this.workspaceInfo = detectWorkspace();
  }

  /** Current detected workspace (primary repo root + candidates). */
  get workspace(): WorkspaceInfo {
    return this.workspaceInfo;
  }

  /** Re-scan workspace folders, e.g. after a folder is added or removed. */
  rescanWorkspace(): WorkspaceInfo {
    // A changed root invalidates the freshness watcher bound to the old root.
    this.disposeFreshness();
    this.workspaceInfo = detectWorkspace();
    return this.workspaceInfo;
  }

  /**
   * Stable pipe from whichever filesystem watcher is current. A workspace
   * rescan replaces the watcher, but subscriptions made through `events()`
   * attach here and survive the swap; only the pipe below is rebound.
   */
  private readonly freshnessEmitter = new vscode.EventEmitter<FreshnessEventKind>();
  private freshnessPipe: vscode.Disposable | null = null;
  private readonly freshnessFacade: FreshnessEvents = {
    onDidChange: this.freshnessEmitter.event,
  };

  /**
   * Freshness events for the primary repo; the underlying watcher is created
   * on first use. Returns null when no repo root is known. Never call this
   * during activate() to keep cold start free of filesystem watching.
   */
  events(): FreshnessEvents | null {
    if (!this.workspaceInfo.repoRoot) return null;
    if (!this.freshnessWatcher) {
      this.freshnessWatcher = createFreshnessWatcher(
        this.workspaceInfo.repoRoot,
        this.workspaceInfo.workspaceMode ? WORKSPACE_DIR : REPO_DIR,
      );
      this.freshnessPipe = this.freshnessWatcher.onDidChange((kind) =>
        this.freshnessEmitter.fire(kind),
      );
    }
    return this.freshnessFacade;
  }

  private disposeFreshness(): void {
    this.freshnessPipe?.dispose();
    this.freshnessPipe = null;
    this.freshnessWatcher?.dispose();
    this.freshnessWatcher = null;
  }

  /** Updates the welcome-view context key. Cheap; safe to call often. */
  setExtensionState(next: ExtensionState): void {
    const changed = this.extensionState !== next;
    this.extensionState = next;
    // The resolved repo is only meaningful while connected; leaving `ready`
    // for any reason (server stop, version floor, workspace change)
    // invalidates it.
    if (next !== "ready") this.currentRepo = null;
    void vscode.commands.executeCommand("setContext", STATE_CONTEXT_KEY, next);
    if (changed) this.stateEmitter.fire(next);
  }

  getExtensionState(): ExtensionState {
    return this.extensionState;
  }

  /**
   * The server's record of the primary repo, resolved by the server manager
   * on connect (null while disconnected or when the server does not index
   * this workspace). Set before the state flips to `ready`, so a `ready`
   * listener can read it synchronously. Carries the indexed `head_commit`
   * (the cache/staleness tag) and `default_branch` (the risk base).
   */
  get repo(): RepoResponse | null {
    return this.currentRepo;
  }

  get repoId(): string | null {
    return this.currentRepo?.id ?? null;
  }

  setRepo(repo: RepoResponse | null): void {
    this.currentRepo = repo;
  }

  private repoRefresh: Promise<RepoResponse | null> | null = null;

  /**
   * Re-resolves the repo record from the server so `head_commit` reflects a
   * just-finished index update. Single-flight: concurrent callers (several
   * features reacting to the same freshness event) share one request. Keeps
   * the last known record when the server is unreachable mid-refresh.
   */
  refreshRepo(): Promise<RepoResponse | null> {
    if (this.repoRefresh) return this.repoRefresh;
    const root = this.workspaceInfo.repoRoot;
    if (!root || this.extensionState !== "ready") {
      return Promise.resolve(this.currentRepo);
    }
    this.repoRefresh = this.api
      .resolveRepo(root)
      .then((repo) => {
        if (repo) this.setRepo(repo);
        return this.currentRepo;
      })
      .finally(() => {
        this.repoRefresh = null;
      });
    return this.repoRefresh;
  }

  /** Routes a state to the status bar feature, if it has registered a sink. */
  setStatusBarState(next: StatusBarState, detail?: StatusBarDetail): void {
    this.statusBarSink(next, detail);
  }

  /** Called by the status bar feature to receive state updates. */
  bindStatusBar(sink: (state: StatusBarState, detail?: StatusBarDetail) => void): void {
    this.statusBarSink = sink;
  }

  dispose(): void {
    this.disposeFreshness();
    this.freshnessEmitter.dispose();
    this.stateEmitter.dispose();
  }
}
