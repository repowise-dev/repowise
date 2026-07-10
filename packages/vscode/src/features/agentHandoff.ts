import { existsSync } from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { buildRefactoringPlanPrompt, type AiPromptFlavor } from "@repowise-dev/ui/health/ai-prompt-builder";
import { typeMeta } from "@repowise-dev/ui/refactoring/meta";
import type { RefactoringPlan } from "@repowise-dev/ui/refactoring/types";
import { CONFIG_SECTION, InternalCommands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { repoRelativePath } from "../core/fileSignals";
import { getPlansForFile } from "../core/plans";

/** The Refactor sub-kinds this provider can emit, declared up front so VS Code
 *  only asks us when a Refactor-family action could be shown. */
const PROVIDED_KINDS = [
  vscode.CodeActionKind.RefactorExtract,
  vscode.CodeActionKind.RefactorMove,
  vscode.CodeActionKind.RefactorRewrite,
];

/**
 * Lightbulb code actions that hand a detected refactoring plan to the user's
 * AI agent: Copilot agent mode, Claude Code, or the clipboard. Gated on the
 * `repowise.agentHandoff.enabled` setting and the `ready` state; reuses the
 * per-file plan cache the CodeLens already populates, so showing the lightbulb
 * costs no extra fetch. The copy action reuses the command the CodeLens owns.
 */
export function registerAgentHandoff(ctx: RepowiseContext): vscode.Disposable {
  return vscode.Disposable.from(
    vscode.languages.registerCodeActionsProvider(
      { scheme: "file" },
      createProvider(ctx),
      { providedCodeActionKinds: PROVIDED_KINDS },
    ),
    vscode.commands.registerCommand(InternalCommands.handPlanToCopilot, (plan: RefactoringPlan) =>
      handPlanToCopilot(ctx, plan),
    ),
    vscode.commands.registerCommand(InternalCommands.handPlanToClaudeCode, (plan: RefactoringPlan) =>
      handPlanToClaudeCode(ctx, plan),
    ),
  );
}

function createProvider(ctx: RepowiseContext): vscode.CodeActionProvider {
  return {
    async provideCodeActions(
      document: vscode.TextDocument,
      range: vscode.Range | vscode.Selection,
      context: vscode.CodeActionContext,
      token: vscode.CancellationToken,
    ): Promise<vscode.CodeAction[]> {
      const enabled = vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<boolean>("agentHandoff.enabled", true);
      if (!enabled || ctx.getExtensionState() !== "ready") return [];

      // Only show up when the editor is asking for Refactor-family actions.
      if (context.only && !context.only.intersects(vscode.CodeActionKind.Refactor)) {
        return [];
      }

      const relPath = repoRelativePath(ctx, document.uri);
      if (!relPath) return [];

      const plans = await getPlansForFile(ctx, relPath);
      if (token.isCancellationRequested) return [];

      const actions: vscode.CodeAction[] = [];
      for (const plan of plans) {
        if (plan.line_start == null) continue;
        // Server line numbers are 1-based; editor ranges are 0-based. A plan
        // without an explicit end covers its start line only.
        const start = Math.max(0, plan.line_start - 1);
        const end = Math.max(start, (plan.line_end ?? plan.line_start) - 1);
        const planRange = new vscode.Range(start, 0, end, Number.MAX_SAFE_INTEGER);
        if (!planRange.intersection(range)) continue;

        actions.push(...planActions(plan));
      }
      return actions;
    },
  };
}

/** Map a plan's type onto the Refactor sub-kind the editor filters on. */
function planKind(plan: RefactoringPlan): vscode.CodeActionKind {
  const type = plan.refactoring_type;
  if (type.includes("extract")) return vscode.CodeActionKind.RefactorExtract;
  if (type.includes("move")) return vscode.CodeActionKind.RefactorMove;
  return vscode.CodeActionKind.RefactorRewrite;
}

/** The three handoff actions for one plan, all under the plan's kind. */
function planActions(plan: RefactoringPlan): vscode.CodeAction[] {
  const label = typeMeta(plan.refactoring_type).label;
  const kind = planKind(plan);

  const make = (title: string, command: string): vscode.CodeAction => {
    const action = new vscode.CodeAction(title, kind);
    action.command = { title, command, arguments: [plan] };
    return action;
  };

  return [
    make(`Repowise: Hand ${label} plan to Copilot agent mode`, InternalCommands.handPlanToCopilot),
    make(`Repowise: Hand ${label} plan to Claude Code`, InternalCommands.handPlanToClaudeCode),
    make(`Repowise: Copy ${label} plan for agent`, InternalCommands.copyRefactoringPrompt),
  ];
}

/**
 * Open Copilot chat in agent mode seeded with the plan prompt. The chat-open
 * command is stringly typed and its argument shape is not a stable API, so any
 * rejection falls back to the clipboard with one confirmation message.
 */
async function handPlanToCopilot(ctx: RepowiseContext, plan: RefactoringPlan): Promise<void> {
  if (!plan) return;
  const prompt = buildRefactoringPlanPrompt({
    plan,
    flavor: "generic",
    ...(ctx.repo?.name ? { repoName: ctx.repo.name } : {}),
  });
  // The chat-open command exists even without a chat provider configured; it
  // would open the setup view and silently drop the query. Only hand off when
  // Copilot Chat is actually installed, otherwise the clipboard is the payload.
  if (vscode.extensions.getExtension("github.copilot-chat")) {
    try {
      await vscode.commands.executeCommand("workbench.action.chat.open", {
        query: prompt,
        mode: "agent",
      });
      return;
    } catch (err) {
      ctx.log.debug(`Copilot chat handoff failed, falling back to clipboard: ${String(err)}`);
    }
  }
  await vscode.env.clipboard.writeText(prompt);
  void vscode.window.showInformationMessage(
    "Couldn't open Copilot agent mode; the plan prompt was copied to the clipboard. Paste it into your agent chat.",
  );
}

/**
 * Hand the plan to Claude Code. The extension has no prompt-injection command,
 * so the clipboard is the payload channel: copy the prompt (MCP-aware flavor
 * when the workspace is configured for the Repowise MCP server), focus the
 * Claude Code panel when the extension is installed, and confirm once.
 */
async function handPlanToClaudeCode(ctx: RepowiseContext, plan: RefactoringPlan): Promise<void> {
  if (!plan) return;
  const root = ctx.workspace.repoRoot;
  const hasMcp =
    !!root &&
    (existsSync(path.join(root, ".mcp.json")) || existsSync(path.join(root, ".vscode", "mcp.json")));
  const flavor: AiPromptFlavor = hasMcp ? "claude-code-mcp" : "claude-code";

  const prompt = buildRefactoringPlanPrompt({
    plan,
    flavor,
    ...(ctx.repo?.name ? { repoName: ctx.repo.name } : {}),
  });
  await vscode.env.clipboard.writeText(prompt);

  if (vscode.extensions.getExtension("anthropic.claude-code")) {
    try {
      await vscode.commands.executeCommand("claude-vscode.focus");
    } catch (err) {
      ctx.log.debug(`Claude Code focus failed: ${String(err)}`);
    }
  }
  void vscode.window.showInformationMessage(
    "Plan copied for Claude Code. Paste it into the conversation.",
  );
}
