import * as vscode from "vscode";
import { CONFIG_SECTION, Commands, InternalCommands } from "../constants";
import { analyzeChange } from "../core/changeAnalysis";
import type { RepowiseContext } from "../core/context";
import { onDidChangeRepoState } from "../core/gitApi";
import {
  changeSignature,
  selectMissingCochanges,
  type MissingCochange,
} from "../shared/changeImpact";
import type { ChangeImpactReport } from "../shared/webviewMessages";

/**
 * Ambient "you may have forgotten a file" nudge. When the files you are editing
 * have a strong history of changing together with a file you have NOT touched,
 * a quiet status-bar item appears. It is deliberately restrained: no toast, no
 * modal, no colour alarm; it shows only above a configurable strength floor,
 * and it can be dismissed for the current change set. Co-changes are advisory
 * (plenty of edits legitimately touch only part of a cluster), so this must
 * inform without nagging.
 */

/** Git fires state changes on every save; coalesce a burst into one analysis. */
const DEBOUNCE_MS = 2_000;

/** How many partners the tooltip lists before collapsing to "+N more". */
const TOOLTIP_LIMIT = 5;

/** Workspace-state key holding the change-set signature the user dismissed. */
const DISMISS_KEY = "repowise.changeIntel.dismissedSignature";

function baseName(p: string): string {
  return p.split("/").pop() || p;
}

export function registerChangeIntel(ctx: RepowiseContext): vscode.Disposable {
  const item = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    1,
  );
  item.command = InternalCommands.reviewCochanges;

  let repoStateSub: vscode.Disposable | null = null;
  /** Guards the async subscribe so a ready/not-ready flap cannot double-arm it. */
  let subscribing = false;
  let debounce: NodeJS.Timeout | null = null;
  /** The partners behind the currently shown item, for the QuickPick. */
  let current: MissingCochange[] = [];
  /** Signature of the change set the item currently reflects. */
  let currentSignature = "";

  function enabled(): boolean {
    return vscode.workspace
      .getConfiguration(CONFIG_SECTION)
      .get<boolean>("changeIntel.cochangeNudge", true);
  }

  function minScore(): number {
    return vscode.workspace
      .getConfiguration(CONFIG_SECTION)
      .get<number>("changeIntel.cochangeMinScore", 4);
  }

  function hide(): void {
    current = [];
    item.hide();
  }

  async function evaluate(): Promise<void> {
    if (!enabled() || ctx.getExtensionState() !== "ready") {
      hide();
      return;
    }
    let report: ChangeImpactReport;
    try {
      report = await analyzeChange(ctx, "working");
    } catch (err) {
      ctx.log.debug(`co-change nudge analysis failed: ${String(err)}`);
      hide();
      return;
    }
    const partners = selectMissingCochanges(report, minScore());
    currentSignature = changeSignature(report.changed);
    if (partners.length === 0) {
      hide();
      return;
    }
    // Respect a dismissal until the change set itself moves on.
    if (ctx.state.get<string>(DISMISS_KEY) === currentSignature) {
      hide();
      return;
    }
    current = partners;
    render(partners);
  }

  function render(partners: MissingCochange[]): void {
    const n = partners.length;
    item.text = `$(git-pull-request) ${n} related`;
    const md = new vscode.MarkdownString();
    md.appendMarkdown(
      `**Files that usually change with your edits**\n\nYou have not touched ${
        n === 1 ? "this file" : "these files"
      }:\n`,
    );
    for (const p of partners.slice(0, TOOLTIP_LIMIT)) {
      md.appendMarkdown(`\n- \`${p.partner}\``);
    }
    if (n > TOOLTIP_LIMIT) md.appendMarkdown(`\n- +${n - TOOLTIP_LIMIT} more`);
    md.appendMarkdown("\n\nClick to review. This is advisory, not a rule.");
    item.tooltip = md;
    item.show();
  }

  /** Opens a light QuickPick to review, jump to, or dismiss the related files. */
  async function reviewCochanges(): Promise<void> {
    if (current.length === 0) {
      void vscode.window.showInformationMessage(
        "No related files to review for the current change set.",
      );
      return;
    }
    // Snapshot the signature: a debounced re-evaluate may reassign
    // currentSignature while the QuickPick is open, and a dismiss must apply to
    // the set the user is actually looking at.
    const signatureAtOpen = currentSignature;
    type Item = vscode.QuickPickItem & {
      action: "open" | "panel" | "dismiss";
      partner?: string;
    };
    const fileItems: Item[] = current.map((p) => ({
      label: `$(file) ${baseName(p.partner)}`,
      description: `co-changed ${p.score}×`,
      detail: `${p.partner} — usually changes with ${p.withChanged}`,
      action: "open",
      partner: p.partner,
    }));
    const actions: Item[] = [
      {
        label: "$(pulse) Open Change Risk",
        detail: "See the full impact of this change set",
        action: "panel",
      },
      {
        label: "$(bell-slash) Dismiss for this change",
        detail: "Hide until the set of changed files changes",
        action: "dismiss",
      },
    ];
    const picked = await vscode.window.showQuickPick<Item>(
      [
        ...fileItems,
        { label: "", kind: vscode.QuickPickItemKind.Separator, action: "open" },
        ...actions,
      ],
      {
        placeHolder: "Files your history says usually change together with this one",
        matchOnDetail: true,
      },
    );
    if (!picked) return;
    if (picked.action === "open" && picked.partner) {
      const root = ctx.workspace.repoRoot;
      if (!root) return;
      const abs = vscode.Uri.joinPath(vscode.Uri.file(root), ...picked.partner.split("/"));
      try {
        await vscode.window.showTextDocument(abs, { preview: true });
      } catch {
        void vscode.window.showWarningMessage(`Could not open ${picked.partner}.`);
      }
    } else if (picked.action === "panel") {
      await vscode.commands.executeCommand(Commands.checkBranchRisk);
    } else if (picked.action === "dismiss") {
      await ctx.state.update(DISMISS_KEY, signatureAtOpen);
      hide();
    }
  }

  function scheduleEval(): void {
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => void evaluate(), DEBOUNCE_MS);
  }

  /** Subscribe to git state lazily (never during activate). */
  function ensureWatcher(): void {
    // The subscribe is async (git can take seconds to init); a second call
    // before it resolves would otherwise slip past a `repoStateSub`-only guard
    // and orphan a duplicate listener on a ready/not-ready flap.
    if (repoStateSub || subscribing) return;
    const root = ctx.workspace.repoRoot;
    if (!root) return;
    subscribing = true;
    void onDidChangeRepoState(root, scheduleEval).then((sub) => {
      subscribing = false;
      // The extension may have torn down before git resolved; drop the sub.
      if (disposed) sub.dispose();
      else repoStateSub = sub;
    });
  }

  let disposed = false;
  const disposables: vscode.Disposable[] = [
    item,
    vscode.commands.registerCommand(InternalCommands.reviewCochanges, () =>
      reviewCochanges(),
    ),
    ctx.onDidChangeExtensionState((state) => {
      if (state === "ready") {
        ensureWatcher();
        void evaluate();
      } else {
        hide();
      }
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration(`${CONFIG_SECTION}.changeIntel`)) void evaluate();
    }),
  ];

  if (ctx.getExtensionState() === "ready") {
    const timer = setTimeout(() => {
      ensureWatcher();
      void evaluate();
    }, 0);
    disposables.push({ dispose: () => clearTimeout(timer) });
  }

  return {
    dispose(): void {
      disposed = true;
      if (debounce) clearTimeout(debounce);
      repoStateSub?.dispose();
      repoStateSub = null;
      for (const d of disposables) d.dispose();
    },
  };
}
