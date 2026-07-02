import * as vscode from "vscode";
import type { RepowiseContext } from "../core/context";
import { registerFindingsTree } from "./trees/findings";
import { registerRefactoringTree } from "./trees/refactoring";

/**
 * Instantiates the two activity-bar tree views (Findings, Refactoring) and
 * bundles their disposables into one. Registration is cheap: each provider
 * defers its first fetch until VS Code asks for children, which only happens
 * once the view is visible. Decisions live in the Home launcher and the
 * decisions panel; hotspots, ownership, dead code, and health are sections of
 * the Findings tree.
 */
export function registerTrees(ctx: RepowiseContext): vscode.Disposable {
  return vscode.Disposable.from(
    registerFindingsTree(ctx),
    registerRefactoringTree(ctx),
  );
}
