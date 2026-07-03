import * as vscode from "vscode";
import * as path from "node:path";
import { existsSync } from "node:fs";
import { Commands, REPO_DIR, WORKSPACE_DIR } from "./constants";
import { createLogger } from "./core/log";
import { createApi } from "./core/api";
import { createCache } from "./core/cache";
import { createCliRunner } from "./core/cliRunner";
import { RepowiseContext } from "./core/context";
import { registerStatusBar } from "./features/statusBar";
import { registerOnboarding } from "./features/onboarding";
import { registerServerManager } from "./features/serverManager";
import { registerMcp } from "./features/mcp";
import { registerDiagnostics } from "./features/diagnostics";
import { registerGutterHeat } from "./features/gutterHeat";
import { registerHovers } from "./features/hovers";
import { registerFileDecorations } from "./features/fileDecorations";
import { registerFileScoreStatus } from "./features/fileScoreStatus";
import { registerTrees } from "./features/trees";
import { registerBranchRisk } from "./features/branchRisk";
import { registerChangeIntel } from "./features/changeIntel";
import { registerStaleness } from "./features/staleness";
import { registerRefactoringLens } from "./features/refactoringLens";
import { registerDocs } from "./features/docs";
import { registerWebviews } from "./core/webviews";
import { registerDashboards } from "./features/dashboards";

let rootContext: RepowiseContext | undefined;

/**
 * Cheap synchronous guess at whether the first workspace folder carries an
 * index. A single stat is the only filesystem work allowed on the hot path; the
 * full workspace scan, lockfile read, and health probe are deferred.
 */
function hasMarkerCheap(): boolean {
  const first = vscode.workspace.workspaceFolders?.[0];
  if (!first) return false;
  const root = first.uri.fsPath;
  return (
    existsSync(path.join(root, REPO_DIR)) ||
    existsSync(path.join(root, WORKSPACE_DIR))
  );
}

export function activate(extCtx: vscode.ExtensionContext): void {
  const log = createLogger();
  extCtx.subscriptions.push(log);

  const api = createApi(log);
  const cache = createCache();
  const cli = createCliRunner(log, () =>
    vscode.workspace.getConfiguration("repowise").get<string>("cliPath", ""),
  );

  const ctx = new RepowiseContext(
    log,
    extCtx.workspaceState,
    api,
    cache,
    cli,
    extCtx.subscriptions,
  );
  rootContext = ctx;
  extCtx.subscriptions.push({ dispose: () => ctx.dispose() });

  // Initial welcome-view state from the one allowed stat. Refined later by the
  // deferred server discovery in the server manager.
  ctx.setExtensionState(hasMarkerCheap() ? "server-down" : "no-index");

  // Cross-cutting commands. Feature-owned commands (start/stop server, MCP
  // configuration, repository setup) are registered by their feature modules.
  extCtx.subscriptions.push(
    vscode.commands.registerCommand(Commands.showLog, () => ctx.log.show()),
    vscode.commands.registerCommand(Commands.checkSetup, () => checkSetup(ctx)),
  );

  // Features. Each returns a Disposable and owns its own commands. All data
  // features are event-driven: registration only wires providers and
  // listeners; nothing fetches until a surface becomes visible while ready.
  extCtx.subscriptions.push(
    registerStatusBar(ctx),
    registerOnboarding(ctx),
    registerServerManager(ctx),
    registerMcp(ctx),
    registerDiagnostics(ctx),
    registerGutterHeat(ctx),
    registerHovers(ctx),
    registerFileDecorations(ctx),
    registerFileScoreStatus(ctx),
    registerTrees(ctx),
    registerBranchRisk(ctx),
    registerChangeIntel(ctx),
    registerStaleness(ctx),
    registerRefactoringLens(ctx),
    registerDocs(ctx),
    registerWebviews(ctx, extCtx.extensionUri),
    registerDashboards(ctx),
  );

  // Workspace-folder changes are handled by the server manager, which owns
  // rescan + rediscovery; a second listener here would race it on the shared
  // freshness watcher.

  log.info("Repowise extension activated.");
}

/** Runs the read-only doctor and surfaces a one-line summary. */
async function checkSetup(ctx: RepowiseContext): Promise<void> {
  const cwd = ctx.workspace.repoRoot ?? undefined;
  try {
    const report = await ctx.cli.runDoctorJson(cwd);
    const failed = report.checks.filter((c) => !c.ok);
    for (const check of report.checks) {
      const line = `doctor: ${check.ok ? "ok" : "FAIL"} - ${check.name}: ${check.detail}`;
      if (check.ok) ctx.log.info(line);
      else ctx.log.warn(line);
    }
    if (report.ok) {
      void vscode.window.showInformationMessage(
        "Repowise setup looks healthy.",
      );
    } else {
      const summary =
        failed
          .slice(0, 3)
          .map((c) => c.name)
          .join(", ") || "see log";
      const choice = await vscode.window.showWarningMessage(
        `Repowise setup has ${failed.length} issue(s): ${summary}.`,
        "Show Log",
      );
      if (choice === "Show Log") ctx.log.show();
    }
  } catch (err) {
    ctx.log.error(`checkSetup failed: ${String(err)}`);
    const choice = await vscode.window.showErrorMessage(
      "Could not run the repowise CLI. Is it installed and on your PATH?",
      "Show Log",
    );
    if (choice === "Show Log") ctx.log.show();
  }
}

export function deactivate(): void {
  rootContext?.dispose();
  rootContext = undefined;
}
