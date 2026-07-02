import * as vscode from "vscode";
import { Commands } from "../constants";
import { getBulkHealth, type FileScores } from "../core/bulkHealth";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";

/** Renders a score, or a dash when the dimension is absent from the payload. */
function fmt(value: number | null): string {
  return value === null ? "-" : value.toFixed(1);
}

/**
 * A dedicated status-bar item showing the active editor's three health scores
 * (defect, maintainability, performance) compactly, with a tooltip that names
 * each dimension plus size and module. Data comes from the shared bulk fetch,
 * never a per-file request; the item hides whenever the active file has no
 * score or the extension is not ready. Clicking focuses the health view.
 */
export function registerFileScoreStatus(ctx: RepowiseContext): vscode.Disposable {
  const item = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    // Its own slot, lower priority than default source-control/language items.
    50,
  );
  item.command = Commands.showFileHealth;

  /** Loaded scores, keyed by repo-relative forward-slash path; null until fetched. */
  let scores: Map<string, FileScores> | null = null;
  /** Guards against overlapping background fetches. */
  let loading = false;
  /** Lazily created freshness subscription, so activate() does no watching. */
  let watcherSub: vscode.Disposable | null = null;

  function render(): void {
    const editor = vscode.window.activeTextEditor;
    if (ctx.getExtensionState() !== "ready" || !scores || !editor) {
      item.hide();
      return;
    }
    const rel = repoRelativePath(ctx, editor.document.uri);
    const entry = rel ? scores.get(rel) : undefined;
    if (!entry) {
      item.hide();
      return;
    }
    const defect = entry.defectScore ?? entry.score;
    item.text = `$(heart) ${fmt(defect)} · ${fmt(entry.maintainabilityScore)} · ${fmt(
      entry.performanceScore,
    )}`;
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown("**Repowise health**\n\n");
    tooltip.appendMarkdown(
      `Defect ${fmt(defect)} · Maintainability ${fmt(
        entry.maintainabilityScore,
      )} · Performance ${fmt(entry.performanceScore)}\n\n`,
    );
    tooltip.appendMarkdown(
      `${entry.nloc} lines${entry.module ? ` · ${entry.module}` : ""}`,
    );
    item.tooltip = tooltip;
    item.show();
  }

  /** Starts the shared bulk fetch once while ready; re-renders when it lands. */
  function kick(): void {
    if (loading || scores) return;
    if (ctx.getExtensionState() !== "ready" || !ctx.repoId) return;
    loading = true;
    void getBulkHealth(ctx).then((map) => {
      loading = false;
      if (map) {
        scores = map;
        render();
      }
    });
  }

  /** Subscribe to index-change events for this repo, once. */
  function ensureWatcher(): void {
    if (watcherSub) return;
    const watcher = ctx.events();
    if (!watcher) return;
    watcherSub = watcher.onDidChange((kind) => {
      if (kind !== "indexChanged") return;
      // A finished index update re-stamps head_commit; re-resolve it so the
      // next fetch caches under the new tag, then refetch and refresh.
      void ctx.refreshRepo().then(() => {
        scores = null;
        render();
        kick();
      });
    });
  }

  const editorSub = vscode.window.onDidChangeActiveTextEditor(() => {
    kick();
    render();
  });

  const stateSub = ctx.onDidChangeExtensionState((state) => {
    if (state === "ready") {
      ensureWatcher();
      kick();
      render();
    } else {
      scores = null;
      loading = false;
      item.hide();
    }
  });

  // Clicking the score opens the full health dashboard panel.
  const commandSub = vscode.commands.registerCommand(Commands.showFileHealth, () => {
    void vscode.commands.executeCommand(Commands.showHealthDashboard);
  });

  if (ctx.getExtensionState() === "ready") {
    ensureWatcher();
    kick();
    render();
  }

  return vscode.Disposable.from(item, editorSub, stateSub, commandSub, {
    dispose: () => watcherSub?.dispose(),
  });
}
