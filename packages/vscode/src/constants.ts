/**
 * Shared constants. Kept dependency-free so any module can import it without
 * pulling in 'vscode' or the API client.
 */

/** Marketplace / manifest extension id (publisher.name). */
export const EXTENSION_ID = "repowise-dev.repowise";

/** Settings section that groups every extension setting. */
export const CONFIG_SECTION = "repowise";

/**
 * Lowest server version this build understands. Older servers may serve a
 * store shape this extension cannot read, so the status bar flags them.
 */
export const MIN_SERVER_VERSION = "0.25.0";

/** Command ids contributed by the extension, mirrored from package.json. */
export const Commands = {
  startServer: "repowise.startServer",
  stopServer: "repowise.stopServer",
  showLog: "repowise.showLog",
  configureMcp: "repowise.configureMcp",
  runInit: "repowise.runInit",
  checkSetup: "repowise.checkSetup",
  checkBranchRisk: "repowise.checkBranchRisk",
  updateIndex: "repowise.updateIndex",
  showFileHealth: "repowise.showFileHealth",
  openDocs: "repowise.openDocs",
  showHealthDashboard: "repowise.showHealthDashboard",
  showArchitecture: "repowise.showArchitecture",
  showKnowledgeGraph: "repowise.showKnowledgeGraph",
  showDecisionTimeline: "repowise.showDecisionTimeline",
} as const;

/**
 * Command ids registered at runtime but not surfaced in the palette (they
 * take arguments from a CodeLens or tree item). Not contributed in
 * package.json on purpose.
 */
export const InternalCommands = {
  copyRefactoringPrompt: "repowise.copyRefactoringPrompt",
  openRefactoringPlan: "repowise.openRefactoringPlan",
} as const;

/** Sidebar view ids, mirrored from package.json `contributes.views`. */
export const Views = {
  /** Welcome/onboarding tree shown until the server is ready. */
  home: "repowise.home",
  /** The Home webview view (hero score, freshness, dashboard launcher). */
  homeDashboard: "repowise.homeView",
  /** Consolidated findings tree: health, hotspots and ownership, dead code. */
  findings: "repowise.findingsView",
  refactoring: "repowise.refactoringView",
} as const;

/** Context key the welcome views gate on. */
export const STATE_CONTEXT_KEY = "repowise.state";

/** Directory names that mark a repo (or workspace) as Repowise-enabled. */
export const REPO_DIR = ".repowise";
export const WORKSPACE_DIR = ".repowise-workspace";

/** Server discovery lockfile written by `repowise serve`. */
export const LOCKFILE_NAME = "serve.lock.json";
