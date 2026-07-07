import * as vscode from "vscode";
import { CONFIG_SECTION } from "../constants";
import { getBulkHealth, type FileScores } from "../core/bulkHealth";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";

/** Single-glyph badge for worst-tier files (FileDecoration badge max is 2). */
const BADGE = "!";

/**
 * Badges the worst-health files in the explorer. Quiet by default: only files
 * at or below the configured defect-score threshold are decorated, and the
 * whole provider output is gated by a setting. Decoration lookups are
 * synchronous against an in-memory map; the bulk fetch is kicked lazily and the
 * decorations refresh when it lands.
 */
export function registerFileDecorations(ctx: RepowiseContext): vscode.Disposable {
  const emitter = new vscode.EventEmitter<undefined>();
  const errorColor = new vscode.ThemeColor("list.errorForeground");

  /** Loaded scores, keyed by repo-relative forward-slash path; null until fetched. */
  let scores: Map<string, FileScores> | null = null;
  /** Guards against overlapping background fetches. */
  let loading = false;
  /** Lazily created freshness subscription, so activate() does no watching. */
  let watcherSub: vscode.Disposable | null = null;

  const read = (): vscode.WorkspaceConfiguration =>
    vscode.workspace.getConfiguration(CONFIG_SECTION);
  const isEnabled = (): boolean => read().get<boolean>("fileDecorations.enabled", true);
  const maxScore = (): number => read().get<number>("fileDecorations.maxScore", 4);

  /** Starts the shared bulk fetch once while ready; refreshes decorations on land. */
  function kick(): void {
    if (loading || scores) return;
    if (ctx.getExtensionState() !== "ready" || !ctx.repoId) return;
    loading = true;
    void getBulkHealth(ctx).then((map) => {
      loading = false;
      if (map) {
        scores = map;
        emitter.fire(undefined);
      }
    });
  }

  const provider: vscode.FileDecorationProvider = {
    onDidChangeFileDecorations: emitter.event,
    provideFileDecoration(uri) {
      if (!isEnabled()) return undefined;
      if (!scores) {
        // First lookup while ready primes the map; nothing to show until it lands.
        kick();
        return undefined;
      }
      const rel = repoRelativePath(ctx, uri);
      if (!rel) return undefined;
      const entry = scores.get(rel);
      if (!entry) return undefined;
      const defect = entry.defectScore ?? entry.score;
      if (defect > maxScore()) return undefined;
      return {
        badge: BADGE,
        tooltip: `Repowise defect score ${defect.toFixed(1)}`,
        color: errorColor,
        propagate: false,
      };
    },
  };

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
        emitter.fire(undefined);
        kick();
      });
    });
  }

  const stateSub = ctx.onDidChangeExtensionState((state) => {
    if (state === "ready") {
      ensureWatcher();
      kick();
    } else {
      // Leaving ready invalidates the resolved repo; drop the map and clear.
      scores = null;
      loading = false;
      emitter.fire(undefined);
    }
  });

  const configSub = vscode.workspace.onDidChangeConfiguration((event) => {
    if (event.affectsConfiguration(`${CONFIG_SECTION}.fileDecorations`)) {
      emitter.fire(undefined);
    }
  });

  if (ctx.getExtensionState() === "ready") ensureWatcher();

  const providerSub = vscode.window.registerFileDecorationProvider(provider);

  return vscode.Disposable.from(
    providerSub,
    stateSub,
    configSub,
    { dispose: () => watcherSub?.dispose() },
    emitter,
  );
}
