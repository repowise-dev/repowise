import * as vscode from "vscode";
import { Commands } from "../constants";
import type { RepowiseContext } from "../core/context";

/** Grace period before the one deferred CLI-presence probe runs. */
const PRESENCE_CHECK_DELAY_MS = 1000;
/** Short timeout for `repowise --version`: present-or-not, nothing heavy. */
const VERSION_PROBE_TIMEOUT_MS = 5000;

/**
 * Onboarding surface: welcome views, first-run prompts, and the guided setup
 * flow that walks a user from "no index" to "connected" mount here. Owns the
 * repository setup command.
 */
export function registerOnboarding(ctx: RepowiseContext): vscode.Disposable {
  let presenceTimer: ReturnType<typeof setTimeout> | undefined;
  // Watcher for the index directory to appear after `repowise init`. Transient:
  // created on runInit, disposed on first hit or when the feature disposes.
  let indexWatcher: vscode.Disposable | undefined;

  /** True when `repowise --version` runs, false on ENOENT/failure/timeout. */
  async function cliIsPresent(): Promise<boolean> {
    try {
      const result = await ctx.cli.run(["--version"], {
        timeoutMs: VERSION_PROBE_TIMEOUT_MS,
      });
      return result.code === 0;
    } catch {
      return false;
    }
  }

  // Refine "no-index" into "not-installed" when the CLI is missing. Only runs
  // for the no-index state (an indexed repo already proves the CLI story, and
  // the server manager owns those states). Never toasts: the welcome view
  // carries the message.
  function scheduledPresenceCheck(): void {
    if (ctx.getExtensionState() !== "no-index") return;
    if (presenceTimer) clearTimeout(presenceTimer);
    presenceTimer = setTimeout(() => {
      presenceTimer = undefined;
      void (async () => {
        if (ctx.getExtensionState() !== "no-index") return;
        if (!(await cliIsPresent())) {
          if (ctx.getExtensionState() === "no-index") {
            ctx.setExtensionState("not-installed");
          }
        }
      })();
    }, PRESENCE_CHECK_DELAY_MS);
  }

  async function runInit(): Promise<void> {
    if (!vscode.workspace.isTrusted) {
      void vscode.window.showWarningMessage(
        "Repowise cannot index a restricted workspace. Trust this workspace, " +
          "then run setup again.",
      );
      return;
    }

    const firstFolder = vscode.workspace.workspaceFolders?.[0];
    if (!firstFolder) {
      void vscode.window.showWarningMessage(
        "Open a folder before setting up Repowise.",
      );
      return;
    }

    // Quick re-probe: if the CLI is missing, guide the install instead of
    // opening a terminal that would just error.
    if (!(await cliIsPresent())) {
      ctx.setExtensionState("not-installed");
      const choice = await vscode.window.showWarningMessage(
        "The repowise CLI was not found. Install it with: pip install repowise",
        "Copy command",
      );
      if (choice === "Copy command") {
        await vscode.env.clipboard.writeText("pip install repowise");
      }
      return;
    }

    // Reuse a Repowise terminal if one is open, else create it.
    const terminal =
      vscode.window.terminals.find((t) => t.name === "Repowise") ??
      vscode.window.createTerminal("Repowise");
    terminal.show();

    const cliPath = ctx.config.cliPath();
    const exe = cliPath
      ? cliPath.includes(" ")
        ? `"${cliPath}"`
        : cliPath
      : "repowise";
    terminal.sendText(`${exe} init`);

    // The index directory appearing is the completion signal; we do not parse
    // terminal output. Watch under the open folder for `.repowise` to be born.
    indexWatcher?.dispose();
    const watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(firstFolder, "**/.repowise/**"),
      false, // fire on create
      true, // ignore change
      true, // ignore delete
    );
    let handled = false;
    const onCreated = () => {
      if (handled) return;
      handled = true;
      watcher.dispose();
      if (indexWatcher === watcher) indexWatcher = undefined;
      ctx.rescanWorkspace();
      ctx.setExtensionState("server-down");
      void (async () => {
        const choice = await vscode.window.showInformationMessage(
          "Index created. Start the local server to connect.",
          "Start Server",
        );
        if (choice === "Start Server") {
          void vscode.commands.executeCommand(Commands.startServer);
        }
      })();
    };
    watcher.onDidCreate(onCreated);
    indexWatcher = watcher;
  }

  // Refine state shortly after activation, and again whenever trust is granted.
  scheduledPresenceCheck();

  const disposables: vscode.Disposable[] = [
    vscode.commands.registerCommand(Commands.runInit, () => runInit()),
    vscode.workspace.onDidGrantWorkspaceTrust(() => scheduledPresenceCheck()),
    {
      dispose: () => {
        if (presenceTimer) clearTimeout(presenceTimer);
        indexWatcher?.dispose();
      },
    },
  ];

  return vscode.Disposable.from(...disposables);
}
