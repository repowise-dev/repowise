import * as vscode from "vscode";
import type { RepowiseContext } from "../core/context";
import { registerDeadCodeTree } from "./trees/deadCode";
import { registerDecisionsTree } from "./trees/decisions";
import { registerHealthTree } from "./trees/health";
import { registerHotspotsTree } from "./trees/hotspots";
import { registerRefactoringTree } from "./trees/refactoring";

/**
 * Instantiates the five activity-bar tree views (Health, Refactoring, Hotspots
 * & Ownership, Dead Code, Decisions) and bundles their disposables into one.
 * Registration is cheap: each provider defers its first fetch until VS Code
 * asks for children, which only happens once the view is visible.
 */
export function registerTrees(ctx: RepowiseContext): vscode.Disposable {
  return vscode.Disposable.from(
    registerHealthTree(ctx),
    registerRefactoringTree(ctx),
    registerHotspotsTree(ctx),
    registerDeadCodeTree(ctx),
    registerDecisionsTree(ctx),
  );
}
