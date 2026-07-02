import * as vscode from "vscode";
import { Commands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { openViewPanel } from "../core/webviews";

/**
 * Opens the branch-risk webview, which scores the working branch against a base
 * and renders the result. Bound to the palette command and the SCM title button
 * (both call `Commands.checkBranchRisk`). The webview owns fetching, so this
 * only opens the panel; openViewPanel guards the ready state and warns when the
 * server is not connected or the repository is not indexed.
 */
export function registerBranchRisk(ctx: RepowiseContext): vscode.Disposable {
  return vscode.commands.registerCommand(Commands.checkBranchRisk, () =>
    openViewPanel(ctx, "risk", {}),
  );
}
