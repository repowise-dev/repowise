import * as vscode from "vscode";
import { buildRefactoringPlanPrompt, type AiPromptFlavor } from "@repowise-dev/ui/health/ai-prompt-builder";
import { typeMeta } from "@repowise-dev/ui/refactoring/meta";
import type { RefactoringPlan } from "@repowise-dev/ui/refactoring/types";
import { CONFIG_SECTION, InternalCommands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";
import { getPlansForFile } from "../core/plans";
import { openViewPanel } from "../core/webviews";

/** The four agent prompt flavors, in QuickPick order (first is the default). */
const FLAVOR_ITEMS: { label: string; flavor: AiPromptFlavor }[] = [
  { label: "Generic agent", flavor: "generic" },
  { label: "Claude Code", flavor: "claude-code" },
  { label: "Claude Code (with Repowise MCP)", flavor: "claude-code-mcp" },
  { label: "Cursor", flavor: "cursor" },
];

/**
 * Refactoring CodeLens: renders lenses per detected refactoring plan above the
 * plan's start line, gated on the `repowise.codeLens.enabled` setting and the
 * `ready` state. Owns the two internal commands the lens fans out to: opening
 * the plan panel and copying a ready-to-paste agent prompt.
 */
export function registerRefactoringLens(ctx: RepowiseContext): vscode.Disposable {
  const onDidChangeCodeLenses = new vscode.EventEmitter<void>();

  const provider = createProvider(ctx, onDidChangeCodeLenses.event);

  let watcherSub: vscode.Disposable | undefined;
  /** Subscribe to index-change events once, lazily (never during activate). */
  function ensureWatcher(): void {
    if (watcherSub) return;
    const watcher = ctx.events();
    if (!watcher) return;
    watcherSub = watcher.onDidChange((kind) => {
      if (kind !== "indexChanged") return;
      // A finished index update re-stamps head_commit; re-resolve it so the
      // next fetch caches under the new tag, then refire so lenses refetch.
      void ctx.refreshRepo().then(() => onDidChangeCodeLenses.fire());
    });
  }

  const stateSub = ctx.onDidChangeExtensionState((state) => {
    if (state === "ready") ensureWatcher();
    onDidChangeCodeLenses.fire();
  });

  const configSub = vscode.workspace.onDidChangeConfiguration((event) => {
    if (event.affectsConfiguration(`${CONFIG_SECTION}.codeLens`)) {
      onDidChangeCodeLenses.fire();
    }
  });

  if (ctx.getExtensionState() === "ready") ensureWatcher();

  return vscode.Disposable.from(
    vscode.languages.registerCodeLensProvider({ scheme: "file" }, provider),
    vscode.commands.registerCommand(InternalCommands.openRefactoringPlan, (plan: RefactoringPlan) =>
      openRefactoringPlan(ctx, plan),
    ),
    vscode.commands.registerCommand(InternalCommands.copyRefactoringPrompt, (plan: RefactoringPlan) =>
      copyRefactoringPrompt(plan),
    ),
    stateSub,
    configSub,
    onDidChangeCodeLenses,
    { dispose: () => watcherSub?.dispose() },
  );
}

function createProvider(
  ctx: RepowiseContext,
  onDidChangeCodeLenses: vscode.Event<void>,
): vscode.CodeLensProvider {
  return {
    onDidChangeCodeLenses,
    async provideCodeLenses(document): Promise<vscode.CodeLens[]> {
      const enabled = vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<boolean>("codeLens.enabled", true);
      if (!enabled || ctx.getExtensionState() !== "ready") return [];

      const relPath = repoRelativePath(ctx, document.uri);
      if (!relPath) return [];

      const plans = await getPlansForFile(ctx, relPath);
      const lenses: vscode.CodeLens[] = [];
      for (const plan of plans) {
        if (plan.line_start == null) continue;
        // Server line numbers are 1-based; editor ranges are 0-based.
        const line = Math.max(0, plan.line_start - 1);
        const range = new vscode.Range(line, 0, line, 0);
        lenses.push(
          new vscode.CodeLens(range, {
            title: lensTitle(plan),
            command: InternalCommands.openRefactoringPlan,
            arguments: [plan],
          }),
          new vscode.CodeLens(range, {
            title: "Copy for agent",
            command: InternalCommands.copyRefactoringPrompt,
            arguments: [plan],
          }),
        );
      }
      return lenses;
    },
    // Lens commands are resolved eagerly above; this satisfies the interface.
    resolveCodeLens: (lens) => lens,
  };
}

/** "Repowise: Extract Class plan (impact 1.20)". */
function lensTitle(plan: RefactoringPlan): string {
  return `Repowise: ${typeMeta(plan.refactoring_type).label} plan (impact ${plan.impact_delta.toFixed(2)})`;
}

/** Open the designed plan panel, seeded with this plan (and its file for the list). */
function openRefactoringPlan(ctx: RepowiseContext, plan: RefactoringPlan): void {
  if (!plan) return;
  openViewPanel(ctx, "refactoring", { planId: plan.id, filePath: plan.file_path });
}

/** Pick a prompt flavor and copy the shared agent prompt for this plan. */
async function copyRefactoringPrompt(plan: RefactoringPlan): Promise<void> {
  if (!plan) return;
  const picked = await vscode.window.showQuickPick(
    FLAVOR_ITEMS.map((item) => item.label),
    { placeHolder: "Copy refactoring plan as a prompt for..." },
  );
  if (!picked) return;
  const flavor = FLAVOR_ITEMS.find((item) => item.label === picked)?.flavor ?? "generic";

  const prompt = buildRefactoringPlanPrompt({ plan, flavor });
  await vscode.env.clipboard.writeText(prompt);
  void vscode.window.showInformationMessage(
    `Copied refactoring prompt (${prompt.length} characters) to the clipboard.`,
  );
}
