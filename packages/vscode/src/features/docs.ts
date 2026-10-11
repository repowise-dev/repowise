import * as vscode from "vscode";
import { Commands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";
import { openViewPanel } from "../core/webviews";

/**
 * Docs command: opens the docs browser panel focused on the active editor's
 * file. Resolution of the file's wiki page happens inside the panel; here we
 * only translate the editor uri to a repo-relative path. With no active
 * editor the panel opens on the repository overview.
 */
export function registerDocs(ctx: RepowiseContext): vscode.Disposable {
  return vscode.commands.registerCommand(Commands.openDocs, () => openDocs(ctx));
}

function openDocs(ctx: RepowiseContext): void {
  const editor = vscode.window.activeTextEditor;
  const relPath = editor ? repoRelativePath(ctx, editor.document.uri) : null;
  openViewPanel(ctx, "docs", relPath ? { filePath: relPath } : {});
}
