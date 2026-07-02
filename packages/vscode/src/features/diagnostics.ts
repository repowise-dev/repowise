import * as vscode from "vscode";
import { CONFIG_SECTION } from "../constants";
import type { RepowiseContext } from "../core/context";
import { getFileFindings, repoRelativePath } from "../core/fileSignals";
import type { HealthDimension, HealthFinding, HealthSeverity } from "@repowise-dev/types/health";

/** Severity ordering, high number = more severe, for the minSeverity floor. */
const SEVERITY_RANK: Record<HealthSeverity, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

const DEFAULT_DIMENSIONS: HealthDimension[] = [
  "defect",
  "maintainability",
  "performance",
];

/** Config keys this feature reacts to, for the configuration-change filter. */
const CONFIG_PREFIX = `${CONFIG_SECTION}.diagnostics`;

/** Maps a finding severity to a diagnostic severity, capped at Warning. */
function toDiagnosticSeverity(severity: HealthSeverity): vscode.DiagnosticSeverity {
  switch (severity) {
    case "critical":
    case "high":
      return vscode.DiagnosticSeverity.Warning;
    case "medium":
      return vscode.DiagnosticSeverity.Information;
    case "low":
      return vscode.DiagnosticSeverity.Hint;
  }
}

/** Line span (1-based, inclusive) to a 0-based range; null lines anchor at top. */
function rangeFor(finding: HealthFinding): vscode.Range {
  if (finding.line_start == null) return new vscode.Range(0, 0, 0, 0);
  const start = Math.max(0, finding.line_start - 1);
  const end = Math.max(start, (finding.line_end ?? finding.line_start) - 1);
  return new vscode.Range(start, 0, end, Number.MAX_SAFE_INTEGER);
}

/**
 * Publishes health findings for visible editors into the Problems panel. Only
 * findings at or above the configured severity floor and within the configured
 * dimensions surface; the deeper set stays in the gutter. Work is confined to
 * visible editors, and a file is only republished when its finding set changes.
 */
export function registerDiagnostics(ctx: RepowiseContext): vscode.Disposable {
  const collection = vscode.languages.createDiagnosticCollection("repowise");
  const disposables: vscode.Disposable[] = [collection];

  /** Last published finding-set signature per document URI, for diff-only set. */
  const signatures = new Map<string, string>();
  /** Lazily created freshness subscription, so activate() does no watching. */
  let watcherSub: vscode.Disposable | null = null;

  const cfg = () => vscode.workspace.getConfiguration(CONFIG_SECTION);
  const enabled = (): boolean => cfg().get<boolean>("diagnostics.enabled", true);
  const minSeverity = (): HealthSeverity =>
    cfg().get<HealthSeverity>("diagnostics.minSeverity", "high");
  const dimensions = (): Set<string> =>
    new Set(cfg().get<string[]>("diagnostics.dimensions", DEFAULT_DIMENSIONS));

  function clearAll(): void {
    collection.clear();
    signatures.clear();
  }

  /** Diff-only publish: skips the set when the file's finding set is unchanged. */
  function publish(doc: vscode.TextDocument, findings: HealthFinding[]): void {
    const floor = SEVERITY_RANK[minSeverity()];
    const dims = dimensions();
    const filtered = findings.filter(
      (f) =>
        SEVERITY_RANK[f.severity] >= floor && dims.has(f.dimension ?? "defect"),
    );

    const signature = filtered
      .map((f) => `${f.id}:${f.line_start}:${f.line_end}:${f.severity}`)
      .sort()
      .join("|");
    const uriKey = doc.uri.toString();
    if (signatures.get(uriKey) === signature) return;
    signatures.set(uriKey, signature);

    const diagnostics = filtered.map((f) => {
      const diagnostic = new vscode.Diagnostic(
        rangeFor(f),
        f.reason,
        toDiagnosticSeverity(f.severity),
      );
      diagnostic.source = "repowise";
      diagnostic.code = f.biomarker_type;
      return diagnostic;
    });
    collection.set(doc.uri, diagnostics);
  }

  async function refreshAll(): Promise<void> {
    if (!enabled() || ctx.getExtensionState() !== "ready" || !ctx.repoId) {
      clearAll();
      return;
    }

    const editors = vscode.window.visibleTextEditors;
    const visible = new Set<string>();
    for (const editor of editors) {
      const rel = repoRelativePath(ctx, editor.document.uri);
      if (!rel) continue;
      visible.add(editor.document.uri.toString());
      const findings = await getFileFindings(ctx, rel);
      // State or config may have changed across the await; re-check before set.
      if (!enabled() || ctx.getExtensionState() !== "ready") return;
      publish(editor.document, findings);
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
      if (event.affectsConfiguration(CONFIG_PREFIX)) void refreshAll();
    }),
  );

  if (ctx.getExtensionState() === "ready") {
    ensureWatcher();
    void refreshAll();
  }

  return vscode.Disposable.from(...disposables);
}
