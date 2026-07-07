import * as vscode from "vscode";
import { CONFIG_SECTION } from "../constants";
import type { RepowiseContext } from "../core/context";
import { getFileFindings, repoRelativePath } from "../core/fileSignals";
import type { HealthFinding, HealthSeverity } from "@repowise-dev/types/health";

/** Gutter/overview-ruler color per severity tier. */
const TIER_COLOR: Record<HealthSeverity, string> = {
  critical: "#f14c4c",
  high: "#ff8800",
  medium: "#ffcc00",
  low: "#75beff",
};

const TIERS: HealthSeverity[] = ["critical", "high", "medium", "low"];

/** A 2px gutter strip in the tier color, as a data-URI SVG for gutterIconPath. */
function stripIcon(color: string): vscode.Uri {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="6" height="18">` +
    `<rect x="0" y="0" width="2" height="18" fill="${color}" fill-opacity="0.85"/>` +
    `</svg>`;
  return vscode.Uri.parse(
    `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`,
  );
}

/** Line span (1-based, inclusive) to a 0-based range; null lines anchor at top. */
function rangeFor(finding: HealthFinding): vscode.Range {
  if (finding.line_start == null) return new vscode.Range(0, 0, 0, 0);
  const start = Math.max(0, finding.line_start - 1);
  const end = Math.max(start, (finding.line_end ?? finding.line_start) - 1);
  return new vscode.Range(start, 0, end, 0);
}

/**
 * Shades the gutter beside every line with a health finding in visible editors,
 * one color per severity tier. This is the deep layer under the quiet Problems
 * floor: it paints all findings regardless of the diagnostics severity setting.
 * Decoration types are created once and reused; each paint only swaps ranges.
 */
export function registerGutterHeat(ctx: RepowiseContext): vscode.Disposable {
  const disposables: vscode.Disposable[] = [];

  /** Decoration types per tier, created lazily on first paint and reused. */
  let decorations: Record<HealthSeverity, vscode.TextEditorDecorationType> | null =
    null;
  /** Lazily created freshness subscription, so activate() does no watching. */
  let watcherSub: vscode.Disposable | null = null;

  const enabled = (): boolean =>
    vscode.workspace
      .getConfiguration(CONFIG_SECTION)
      .get<boolean>("gutterHeat.enabled", true);

  function ensureDecorations(): Record<
    HealthSeverity,
    vscode.TextEditorDecorationType
  > {
    if (decorations) return decorations;
    const build = (severity: HealthSeverity): vscode.TextEditorDecorationType => {
      const color = TIER_COLOR[severity];
      const type = vscode.window.createTextEditorDecorationType({
        gutterIconPath: stripIcon(color),
        gutterIconSize: "contain",
        overviewRulerColor: color,
        overviewRulerLane: vscode.OverviewRulerLane.Left,
      });
      disposables.push(type);
      return type;
    };
    decorations = {
      critical: build("critical"),
      high: build("high"),
      medium: build("medium"),
      low: build("low"),
    };
    return decorations;
  }

  /** Clears every tier's decorations from all visible editors. */
  function clearAll(): void {
    if (!decorations) return;
    for (const editor of vscode.window.visibleTextEditors) {
      for (const tier of TIERS) editor.setDecorations(decorations[tier], []);
    }
  }

  function paint(
    editor: vscode.TextEditor,
    findings: HealthFinding[],
  ): void {
    const types = ensureDecorations();
    const byTier: Record<HealthSeverity, vscode.Range[]> = {
      critical: [],
      high: [],
      medium: [],
      low: [],
    };
    for (const finding of findings) byTier[finding.severity].push(rangeFor(finding));
    for (const tier of TIERS) editor.setDecorations(types[tier], byTier[tier]);
  }

  async function refreshAll(): Promise<void> {
    if (!enabled() || ctx.getExtensionState() !== "ready" || !ctx.repoId) {
      clearAll();
      return;
    }
    for (const editor of vscode.window.visibleTextEditors) {
      const rel = repoRelativePath(ctx, editor.document.uri);
      if (!rel) continue;
      const findings = await getFileFindings(ctx, rel);
      if (!enabled() || ctx.getExtensionState() !== "ready") return;
      paint(editor, findings);
    }
  }

  /** Subscribe to index-change events for this repo, once, while ready. */
  function ensureWatcher(): void {
    if (watcherSub) return;
    const watcher = ctx.events();
    if (!watcher) return;
    watcherSub = watcher.onDidChange((kind) => {
      if (kind !== "indexChanged") return;
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
      if (event.affectsConfiguration(`${CONFIG_SECTION}.gutterHeat.enabled`)) {
        void refreshAll();
      }
    }),
  );

  if (ctx.getExtensionState() === "ready") {
    ensureWatcher();
    void refreshAll();
  }

  return vscode.Disposable.from(...disposables);
}
