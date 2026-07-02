import * as vscode from "vscode";
import { CONFIG_SECTION, REPO_DIR, STATE_CONTEXT_KEY, WORKSPACE_DIR } from "../constants";
import type { Logger } from "./log";
import type { RepowiseApi } from "./api";
import type { RepowiseCache } from "./cache";
import type { CliRunner } from "./cliRunner";
import { createFreshnessWatcher, type FreshnessWatcher } from "./freshness";
import { detectWorkspace, type WorkspaceInfo } from "./workspace";

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
   * Freshness watchers for the primary repo, created on first use. Returns null
   * when no repo root is known. Never call this during activate() to keep cold
   * start free of filesystem watching.
   */
  events(): FreshnessWatcher | null {
    if (!this.workspaceInfo.repoRoot) return null;
    if (!this.freshnessWatcher) {
      this.freshnessWatcher = createFreshnessWatcher(
        this.workspaceInfo.repoRoot,
        this.workspaceInfo.workspaceMode ? WORKSPACE_DIR : REPO_DIR,
      );
    }
    return this.freshnessWatcher;
  }

  private disposeFreshness(): void {
    this.freshnessWatcher?.dispose();
    this.freshnessWatcher = null;
  }

  /** Updates the welcome-view context key. Cheap; safe to call often. */
  setExtensionState(next: ExtensionState): void {
    this.extensionState = next;
    void vscode.commands.executeCommand("setContext", STATE_CONTEXT_KEY, next);
  }

  getExtensionState(): ExtensionState {
    return this.extensionState;
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
  }
}
