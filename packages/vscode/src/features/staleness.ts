import * as vscode from "vscode";
import { readFile } from "node:fs/promises";
import * as path from "node:path";
import { Commands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { getHeadCommit } from "../core/gitApi";

/** Update runs can be long on a large repo; well above the default timeout. */
const UPDATE_TIMEOUT_MS = 30 * 60_000;

/** One newline-delimited progress event from `repowise update --progress json`. */
interface UpdateEvent {
  event: string;
  name?: string;
  total?: number;
  completed?: number;
  ok?: boolean;
  message?: string;
}

/**
 * Surfaces "index behind the working tree" state in its own status-bar item and
 * owns `Commands.updateIndex`. Stale means the server's indexed `head_commit`
 * differs from the live checked-out commit. The badge is visible only while
 * stale; a click runs an incremental index update that clears it.
 */
export function registerStaleness(ctx: RepowiseContext): vscode.Disposable {
  const item = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    0,
  );
  item.text = "$(sync-ignored) Repowise: index behind";
  item.tooltip =
    "The Repowise index is behind the checked-out commit. Click to run an index update.";
  item.command = Commands.updateIndex;
  item.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");

  let watcherSub: vscode.Disposable | null = null;
  /** True while an update is in flight, to enforce single-flight. */
  let updating = false;

  /** Live checked-out commit, preferring the git extension over disk reads. */
  async function liveHead(root: string): Promise<string | null> {
    const viaGit = await getHeadCommit(root);
    if (viaGit) return viaGit;
    return readHeadFromDisk(root);
  }

  async function evaluate(): Promise<void> {
    const indexed = ctx.repo?.head_commit;
    const root = ctx.workspace.repoRoot;
    // Only flag when both sides are known: an unresolved repo or an
    // undeterminable HEAD is not evidence of staleness.
    if (!indexed || !root) {
      item.hide();
      return;
    }
    const live = await liveHead(root);
    if (!live) {
      item.hide();
      return;
    }
    if (commitsMatch(indexed, live)) item.hide();
    else item.show();
  }

  /** Subscribe to freshness events lazily (never during activate()). */
  function ensureWatcher(): void {
    if (watcherSub) return;
    const watcher = ctx.events();
    if (!watcher) return;
    watcherSub = watcher.onDidChange((kind) => {
      if (kind === "headChanged") {
        void evaluate();
      } else if (kind === "indexChanged") {
        // An index rebuild re-stamps head_commit; re-resolve before comparing.
        void ctx.refreshRepo().then(() => evaluate());
      }
    });
  }

  async function updateIndex(): Promise<void> {
    if (updating) {
      void vscode.window.showInformationMessage(
        "A Repowise index update is already running.",
      );
      return;
    }
    const root = ctx.workspace.repoRoot;
    if (!root) {
      void vscode.window.showWarningMessage(
        "No Repowise index found in this workspace.",
      );
      return;
    }

    updating = true;
    try {
      const { ok, detail } = await runUpdate(root);
      if (ok) {
        await ctx.refreshRepo();
        await evaluate();
        void vscode.window.showInformationMessage("Repowise index updated.");
      } else {
        const choice = await vscode.window.showErrorMessage(
          detail ?? "The Repowise index update failed.",
          "Show Log",
        );
        if (choice === "Show Log") ctx.log.show();
      }
    } finally {
      updating = false;
    }
  }

  /**
   * Runs the CLI update, streaming NDJSON progress into a notification. A `done`
   * event with `ok: false`, an `error` event, or a nonzero exit code all count
   * as failure. Non-JSON lines are ignored silently.
   */
  async function runUpdate(
    root: string,
  ): Promise<{ ok: boolean; detail: string | null }> {
    return vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Updating Repowise index",
        cancellable: false,
      },
      async (progress) => {
        let total: number | null = null;
        let lastPct = 0;
        let failed = false;
        let errorMessage: string | null = null;

        const onStdoutLine = (line: string): void => {
          let evt: UpdateEvent;
          try {
            evt = JSON.parse(line) as UpdateEvent;
          } catch {
            return;
          }
          switch (evt.event) {
            case "start":
              progress.report({ message: "Scanning changes" });
              break;
            case "stage":
              if (evt.name) progress.report({ message: humanize(evt.name) });
              break;
            case "total_known":
              total = typeof evt.total === "number" ? evt.total : null;
              break;
            case "page_done": {
              const completed = evt.completed ?? 0;
              const message = total
                ? `Generating pages ${completed}/${total}`
                : `Generating pages (${completed})`;
              if (total && total > 0) {
                const pct = Math.min(100, (completed / total) * 100);
                progress.report({ increment: pct - lastPct, message });
                lastPct = pct;
              } else {
                progress.report({ message });
              }
              break;
            }
            case "done":
              if (evt.ok === false) failed = true;
              break;
            case "error":
              failed = true;
              errorMessage = evt.message ?? errorMessage;
              break;
            default:
              break;
          }
        };

        try {
          // Bypass the serialized queue: a minutes-long update would make
          // every queued short call (doctor, version probes) appear frozen.
          // The `updating` flag above is the single-flight guard.
          const result = await ctx.cli.run(["update", "--progress", "json"], {
            cwd: root,
            timeoutMs: UPDATE_TIMEOUT_MS,
            onStdoutLine,
            bypassQueue: true,
          });
          const ok = result.code === 0 && !failed;
          const detail = ok
            ? null
            : errorMessage ??
              `The Repowise index update failed (exit code ${result.code ?? "unknown"}).`;
          return { ok, detail };
        } catch (err) {
          ctx.log.error(`index update failed: ${String(err)}`);
          return { ok: false, detail: "The Repowise index update failed." };
        }
      },
    );
  }

  const disposables: vscode.Disposable[] = [
    item,
    vscode.commands.registerCommand(Commands.updateIndex, () => void updateIndex()),
    ctx.onDidChangeExtensionState((state) => {
      if (state === "ready") {
        ensureWatcher();
        void evaluate();
      } else {
        item.hide();
      }
    }),
  ];

  // If the extension is already ready when this registers (unusual: discovery
  // runs post-activation), evaluate once off the hot path.
  if (ctx.getExtensionState() === "ready") {
    const timer = setTimeout(() => {
      ensureWatcher();
      void evaluate();
    }, 0);
    disposables.push({ dispose: () => clearTimeout(timer) });
  }

  return {
    dispose(): void {
      watcherSub?.dispose();
      watcherSub = null;
      for (const d of disposables) d.dispose();
    },
  };
}

/** True when two commit ids refer to the same commit (tolerates short shas). */
function commitsMatch(a: string, b: string): boolean {
  if (a === b) return true;
  const shorter = a.length < b.length ? a : b;
  const longer = a.length < b.length ? b : a;
  return shorter.length > 0 && longer.startsWith(shorter);
}

/**
 * Reads the checked-out commit from `.git` when the git extension is
 * unavailable. Resolves a symbolic ref to its loose ref file. Packed refs are
 * not read here; that path degrades to null (no false staleness) and the git
 * extension covers it when enabled.
 */
async function readHeadFromDisk(root: string): Promise<string | null> {
  try {
    const raw = (await readFile(path.join(root, ".git", "HEAD"), "utf8")).trim();
    if (raw.startsWith("ref:")) {
      const ref = raw.slice(4).trim();
      const refPath = path.join(root, ".git", ...ref.split("/"));
      const sha = (await readFile(refPath, "utf8")).trim();
      return sha || null;
    }
    // Detached HEAD: the file holds the commit id directly.
    return raw || null;
  } catch {
    return null;
  }
}

/** Turns a snake_case stage name into a readable progress message. */
function humanize(name: string): string {
  const spaced = name.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
