import * as vscode from "vscode";
import * as path from "node:path";
import { buildRefactoringPlanPrompt, type AiPromptFlavor } from "@repowise-dev/ui/health/ai-prompt-builder";
import { typeMeta } from "@repowise-dev/ui/refactoring/meta";
import {
  blastCount,
  blastFiles,
  evidenceRows,
  planSynopsis,
  planWins,
  type RefactoringPlan,
} from "@repowise-dev/ui/refactoring/types";
import { CONFIG_SECTION, InternalCommands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";
import { getPlansForFile } from "../core/plans";

/** Virtual-document scheme for the rendered refactoring plan preview. */
const PLAN_SCHEME = "repowise-plan";

/** The four agent prompt flavors, in QuickPick order (first is the default). */
const FLAVOR_ITEMS: { label: string; flavor: AiPromptFlavor }[] = [
  { label: "Generic agent", flavor: "generic" },
  { label: "Claude Code", flavor: "claude-code" },
  { label: "Claude Code (with Repowise MCP)", flavor: "claude-code-mcp" },
  { label: "Cursor", flavor: "cursor" },
];

/**
 * Refactoring CodeLens: renders one lens per detected refactoring plan above the
 * plan's start line, gated on the `repowise.codeLens.enabled` setting and the
 * `ready` state. Owns the two internal commands the lens fans out to: opening a
 * rendered plan preview and copying a ready-to-paste agent prompt.
 */
export function registerRefactoringLens(ctx: RepowiseContext): vscode.Disposable {
  const onDidChangeCodeLenses = new vscode.EventEmitter<void>();

  const provider = createProvider(ctx, onDidChangeCodeLenses.event);

  // Rendered plan markdown, keyed by virtual-doc uri. Kept for the session so a
  // reopened preview does not need to re-render.
  const planDocs = new Map<string, string>();
  const planContentProvider: vscode.TextDocumentContentProvider = {
    provideTextDocumentContent: (uri) => planDocs.get(uri.toString()) ?? "",
  };

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
    vscode.workspace.registerTextDocumentContentProvider(PLAN_SCHEME, planContentProvider),
    vscode.commands.registerCommand(InternalCommands.openRefactoringPlan, (plan: RefactoringPlan) =>
      openRefactoringPlan(planDocs, plan),
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
        );
      }
      return lenses;
    },
    // Lens commands are resolved eagerly above; this satisfies the interface.
    resolveCodeLens: (lens) => lens,
  };
}

/** "Repowise: Extract Class (impact 1.20, M)". */
function lensTitle(plan: RefactoringPlan): string {
  return `Repowise: ${typeMeta(plan.refactoring_type).label} (impact ${plan.impact_delta.toFixed(
    2,
  )}, ${plan.effort_bucket})`;
}

/** `path:start-end` (or `path:start`, or `path`) for the plan's target. */
function planLocation(plan: RefactoringPlan): string {
  if (plan.line_start == null) return plan.file_path;
  if (plan.line_end != null && plan.line_end !== plan.line_start) {
    return `${plan.file_path}:${plan.line_start}-${plan.line_end}`;
  }
  return `${plan.file_path}:${plan.line_start}`;
}

/** Human-readable plan preview (distinct from the agent prompt). */
function renderPlanMarkdown(plan: RefactoringPlan): string {
  const meta = typeMeta(plan.refactoring_type);
  const affected = blastFiles(plan).filter((f) => f !== plan.file_path);
  const count = blastCount(plan);
  const wins = planWins(plan);
  const evidence = evidenceRows(plan);

  const lines: string[] = [
    `# ${meta.label}`,
    "",
    planSynopsis(plan) || meta.blurb,
    "",
    "## Target",
    "",
    `- Location: \`${planLocation(plan)}\``,
    ...(plan.target_symbol ? [`- Symbol: \`${plan.target_symbol}\``] : []),
    `- Confidence: ${plan.confidence}`,
    `- Effort: ${plan.effort_bucket}`,
    `- Impact: ${plan.impact_delta.toFixed(2)} health points`,
    "",
  ];

  if (wins.length > 0) {
    lines.push("## What you gain", "");
    for (const win of wins) lines.push(`- ${win.label}`);
    lines.push("");
  }

  if (evidence.length > 0) {
    lines.push("## Evidence", "");
    for (const row of evidence) lines.push(`- ${row.label}: ${row.value}`);
    lines.push("");
  }

  lines.push("## Blast radius", "");
  if (affected.length > 0) {
    lines.push(`${count} file${count === 1 ? "" : "s"} to keep consistent:`, "");
    for (const file of affected.slice(0, 20)) lines.push(`- \`${file}\``);
    if (affected.length > 20) lines.push(`- ...and ${affected.length - 20} more`);
  } else {
    lines.push(
      count > 0
        ? `${count} dependent${count === 1 ? "" : "s"} affected.`
        : "No dependent files recorded.",
    );
  }
  lines.push("");

  lines.push(
    "---",
    "",
    'Run "Repowise: Copy plan for agent" (the action on this preview) to copy a ready-to-paste prompt for an AI coding agent.',
  );

  return lines.join("\n");
}

/** Render the plan into a virtual markdown doc and open its preview. */
async function openRefactoringPlan(
  planDocs: Map<string, string>,
  plan: RefactoringPlan,
): Promise<void> {
  if (!plan) return;
  const meta = typeMeta(plan.refactoring_type);
  const base = path.basename(plan.file_path) || "file";
  // Unique per plan id so distinct plans get distinct previews; readable path so
  // the preview tab shows the refactoring type.
  const uri = vscode.Uri.from({
    scheme: PLAN_SCHEME,
    path: `/${meta.label} - ${base}.md`,
    query: plan.id,
  });
  planDocs.set(uri.toString(), renderPlanMarkdown(plan));

  await vscode.commands.executeCommand("markdown.showPreview", uri);

  const choice = await vscode.window.showInformationMessage(
    `Repowise refactoring plan: ${meta.label}`,
    "Copy plan for agent",
  );
  if (choice === "Copy plan for agent") {
    await vscode.commands.executeCommand(InternalCommands.copyRefactoringPrompt, plan);
  }
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
