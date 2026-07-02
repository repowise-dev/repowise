import * as vscode from "vscode";
import { Commands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { openViewPanel } from "../core/webviews";

/**
 * Palette commands for the standalone dashboard panels. The other panel
 * surfaces open from their owning features (docs command, branch risk
 * command, refactoring lens and tree), so this module stays a thin command
 * fan-out with no data logic.
 */
export function registerDashboards(ctx: RepowiseContext): vscode.Disposable {
  const disposables = [
    vscode.commands.registerCommand(Commands.showHealthDashboard, () =>
      openViewPanel(ctx, "health"),
    ),
    vscode.commands.registerCommand(Commands.showArchitecture, () =>
      openViewPanel(ctx, "architecture"),
    ),
    vscode.commands.registerCommand(Commands.showKnowledgeGraph, () =>
      openViewPanel(ctx, "graph"),
    ),
    vscode.commands.registerCommand(Commands.showDecisionTimeline, () =>
      openViewPanel(ctx, "decisions"),
    ),
  ];
  return {
    dispose(): void {
      for (const d of disposables) d.dispose();
    },
  };
}
