import * as vscode from "vscode";
import { getRefactoringTargets } from "@repowise-dev/api-client/refactoring";
import { InternalCommands, Views } from "../../constants";
import type { RepowiseContext } from "../../core/context";
import {
  baseName,
  humanizeType,
  mountView,
  RepowiseTreeProvider,
  type RepoTreeNode,
} from "./shared";

/** Response and plan shapes derived from the client so no extra import is needed. */
type RefactoringTargets = Awaited<ReturnType<typeof getRefactoringTargets>>;
type RefactoringPlan = RefactoringTargets["plans"][number];

const PLANS_KEY = "plans:all";
const MAX_PLANS = 100;

/** Reads the first present dependent/file/caller count from an open blast dict. */
function blastCount(blast: Record<string, unknown>): number | null {
  for (const key of ["dependents_count", "file_count", "callers"]) {
    const value = blast[key];
    if (typeof value === "number") return value;
    if (Array.isArray(value)) return value.length;
  }
  return null;
}

/**
 * Refactoring view: server-ranked plans shown as a flat, top-N list (plans
 * arrive already ordered by rank). The item command hands the full plan object
 * to a runtime command registered elsewhere; this module only references it by
 * id. The badge is the server's total count, read from the same response.
 */
class RefactoringTreeProvider extends RepowiseTreeProvider {
  protected readonly name = "Refactoring";

  protected async loadRoots(repoId: string): Promise<RepoTreeNode[]> {
    const targets = await this.cached(PLANS_KEY, () =>
      getRefactoringTargets(repoId, { minConfidence: "medium" }),
    );
    // Badge what the tree actually lists (medium+ confidence, top-N), not the
    // server's global total: a four-digit badge on the activity bar is noise.
    const listed = Math.min(targets.plans.length, MAX_PLANS);
    this.setBadge(listed, `${listed} ranked refactoring plans`);

    if (targets.plans.length === 0) {
      return [this.messageNode("No refactoring targets")];
    }
    return targets.plans.slice(0, MAX_PLANS).map((plan) => this.planNode(plan));
  }

  private planNode(plan: RefactoringPlan): RepoTreeNode {
    const tooltip = new vscode.MarkdownString();
    tooltip.appendMarkdown(`**${humanizeType(plan.refactoring_type)}**\n\n`);
    tooltip.appendMarkdown(`\`${plan.file_path}\`\n\n`);
    tooltip.appendMarkdown(`- Impact: ${plan.impact_delta.toFixed(2)}\n`);
    tooltip.appendMarkdown(`- Effort: ${plan.effort_bucket}\n`);
    tooltip.appendMarkdown(`- Confidence: ${plan.confidence}\n`);
    const blast = blastCount(plan.blast_radius);
    if (blast != null) tooltip.appendMarkdown(`- Blast radius: ${blast}\n`);
    return {
      key: `plan:${plan.id}`,
      label: `${humanizeType(plan.refactoring_type)} ${
        plan.target_symbol ?? baseName(plan.file_path)
      }`,
      description: `impact ${plan.impact_delta.toFixed(2)} · ${plan.effort_bucket}`,
      tooltip,
      icon: new vscode.ThemeIcon("wrench"),
      collapsibleState: vscode.TreeItemCollapsibleState.None,
      command: {
        command: InternalCommands.openRefactoringPlan,
        title: "Open Refactoring Plan",
        arguments: [plan],
      },
    };
  }
}

/** Registers the Refactoring tree view. */
export function registerRefactoringTree(ctx: RepowiseContext): vscode.Disposable {
  return mountView(ctx, Views.refactoring, new RefactoringTreeProvider(ctx));
}
