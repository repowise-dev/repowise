import * as vscode from "vscode";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";

/** One Problems-panel feature, as a configuration of the shared lifecycle. */
export interface EditorDiagnosticsSpec<T> {
  /** Diagnostic collection name. */
  name: string;
  /** Config section whose changes trigger a refresh. */
  configPrefix: string;
  enabled(): boolean;
  /** Which visible documents the feature covers; all when omitted. */
  accept?(doc: vscode.TextDocument): boolean;
  /** The items to publish for one repo-relative file, already filtered. */
  load(ctx: RepowiseContext, rel: string): Promise<T[]>;
  /** Per-item key; a file is republished only when its sorted set changes. */
  signature(item: T): string;
  toDiagnostic(doc: vscode.TextDocument, item: T): vscode.Diagnostic;
}

/**
 * Publishes per-file items for visible editors into a diagnostic collection.
 * Work is confined to visible editors, a file is only republished when its item
 * set changes, and an index change re-resolves the head commit and refreshes.
 */
export function registerEditorDiagnostics<T>(
  ctx: RepowiseContext,
  spec: EditorDiagnosticsSpec<T>,
): vscode.Disposable {
  const collection = vscode.languages.createDiagnosticCollection(spec.name);
  const disposables: vscode.Disposable[] = [collection];

  /** Last published item-set signature per document URI, for diff-only set. */
  const signatures = new Map<string, string>();
  /** Lazily created freshness subscription, so activate() does no watching. */
  let watcherSub: vscode.Disposable | null = null;
  /** Bumped per refresh run; a run that sees a newer value stops publishing. */
  let generation = 0;

  const active = (): boolean =>
    spec.enabled() && ctx.getExtensionState() === "ready" && !!ctx.repoId;

  function clearAll(): void {
    collection.clear();
    signatures.clear();
  }

  function publish(doc: vscode.TextDocument, items: T[]): void {
    const signature = items.map(spec.signature).sort().join("|");
    const uriKey = doc.uri.toString();
    if (signatures.get(uriKey) === signature) return;
    signatures.set(uriKey, signature);
    collection.set(doc.uri, items.map((item) => spec.toDiagnostic(doc, item)));
  }

  async function refreshAll(): Promise<void> {
    const run = ++generation;
    if (!active()) {
      clearAll();
      return;
    }

    const visible = new Set<string>();
    for (const editor of vscode.window.visibleTextEditors) {
      const doc = editor.document;
      if (spec.accept && !spec.accept(doc)) continue;
      const rel = repoRelativePath(ctx, doc.uri);
      if (!rel) continue;
      visible.add(doc.uri.toString());
      const items = await spec.load(ctx, rel);
      // A newer run owns the collection now (it may hold a newer index's
      // findings), and state or config may have changed across the await.
      if (run !== generation || !active()) return;
      publish(doc, items);
    }

    // Drop entries for documents that are no longer visible.
    for (const uriKey of [...signatures.keys()]) {
      if (!visible.has(uriKey)) {
        collection.delete(vscode.Uri.parse(uriKey));
        signatures.delete(uriKey);
      }
    }
  }

  /** Subscribe to index-change events for this repo, once, while ready. */
  function ensureWatcher(): void {
    if (watcherSub) return;
    const watcher = ctx.events();
    if (!watcher) return;
    watcherSub = watcher.onDidChange((kind) => {
      if (kind !== "indexChanged") return;
      // A finished index update re-stamps head_commit; re-resolve it so the
      // next fetch caches under the new tag, then recompute visible editors.
      void ctx.refreshRepo().then(() => refreshAll());
    });
    disposables.push(watcherSub);
  }

  disposables.push(
    ctx.onDidChangeExtensionState((state) => {
      if (state === "ready") {
        ensureWatcher();
        void refreshAll();
      } else {
        clearAll();
      }
    }),
    vscode.window.onDidChangeVisibleTextEditors(() => void refreshAll()),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration(spec.configPrefix)) void refreshAll();
    }),
  );

  if (ctx.getExtensionState() === "ready") {
    ensureWatcher();
    void refreshAll();
  }

  // Reads the array at dispose time, so the lazily pushed watcher is included.
  return new vscode.Disposable(() => disposables.forEach((d) => d.dispose()));
}
