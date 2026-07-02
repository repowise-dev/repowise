import * as vscode from "vscode";
import { Commands } from "../constants";
import type {
  RepowiseContext,
  StatusBarDetail,
  StatusBarState,
} from "../core/context";

/**
 * Presents local-server connection state in a single status-bar item. Purely
 * reactive: it holds no timers and does no background work, only rendering the
 * state the server manager pushes through `ctx.setStatusBarState`. The update
 * surface is registered on the context so any feature can drive it without
 * importing this module.
 */
export function registerStatusBar(ctx: RepowiseContext): vscode.Disposable {
  const item = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    // Low priority: the connection indicator sits to the right of more
    // important language/source-control items.
    0,
  );

  const warnBackground = new vscode.ThemeColor(
    "statusBarItem.warningBackground",
  );

  const setState = (state: StatusBarState, detail?: StatusBarDetail): void => {
    switch (state) {
      // Quiet states: not a Repowise repo, not indexed, or untrusted. The
      // welcome view carries any call to action, so the status bar stays out
      // of the way entirely.
      case "no-index":
      case "untrusted":
        item.hide();
        return;

      // A stale lockfile reports the same way as no server: it is not
      // running, and the click affordance starts it.
      case "server-down":
        item.text = "$(circle-slash) Repowise";
        item.tooltip = "Local server not running. Click to start.";
        item.command = Commands.startServer;
        item.backgroundColor = undefined;
        break;

      case "connecting":
        item.text = "$(sync~spin) Repowise";
        item.tooltip = "Starting local server";
        item.command = undefined;
        item.backgroundColor = undefined;
        break;

      case "connected": {
        item.text = "$(check) Repowise";
        const lines = ["Connected to the local Repowise server."];
        if (detail?.version) lines.push(`Version ${detail.version}`);
        if (detail?.url) lines.push(detail.url);
        item.tooltip = lines.join("\n");
        item.command = Commands.showLog;
        item.backgroundColor = undefined;
        break;
      }

      case "version-low":
        item.text = "$(warning) Repowise";
        item.tooltip =
          "Server version below the minimum this extension supports. Update the repowise package.";
        item.command = Commands.checkSetup;
        item.backgroundColor = warnBackground;
        break;
    }
    item.show();
  };

  // Register the update surface, then start hidden until the deferred server
  // discovery reports a concrete state (so no wrong affordance flashes first).
  ctx.bindStatusBar(setState);
  setState(vscode.workspace.isTrusted ? "no-index" : "untrusted");

  return item;
}
