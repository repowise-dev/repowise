import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { Commands, InternalCommands } from "../constants";
import type { RepowiseContext } from "../core/context";
import { hostedStoryUrl, readCliAnonId } from "../shared/hostedLinks";

/** globalState key: the post-setup publish prompt has been shown once. */
const SETUP_PROMPT_SHOWN = "repowise.hosted.setupPromptShown";

/**
 * repowise.dev from the editor: the `repowise.publish` command (runs the CLI's
 * `repowise publish` in a terminal, so the flow and its messages live in one
 * place) and the one-time prompt after setup succeeds. Every link carries its
 * `src`; the CLI's anonymous install id rides along only while VS Code
 * telemetry is on. `vscode.env.machineId` is never sent.
 */
export function registerHosted(
  ctx: RepowiseContext,
  globalState: vscode.Memento,
): vscode.Disposable {
  function runPublish(): void {
    const cliPath = ctx.config.cliPath();
    const exe = cliPath ? (cliPath.includes(" ") ? `"${cliPath}"` : cliPath) : "repowise";
    const terminal =
      vscode.window.terminals.find((t) => t.name === "Repowise") ??
      vscode.window.createTerminal("Repowise");
    terminal.show();
    terminal.sendText(`${exe} publish`);
  }

  async function offerAfterSetup(): Promise<void> {
    if (globalState.get<boolean>(SETUP_PROMPT_SHOWN)) return;
    // Shown once, whatever the answer: it is never repeated.
    await globalState.update(SETUP_PROMPT_SHOWN, true);
    const choice = await vscode.window.showInformationMessage(
      "Also use this repo from Claude.ai / ChatGPT: publish it to repowise.dev. " +
        "Free for public repos.",
      "Publish it free",
      "Learn more",
    );
    if (choice === "Publish it free") runPublish();
    if (choice === "Learn more") {
      await vscode.env.openExternal(vscode.Uri.parse(storyUrl("vscode_setup_done", "mcp")));
    }
  }

  return vscode.Disposable.from(
    vscode.commands.registerCommand(Commands.publish, runPublish),
    vscode.commands.registerCommand(InternalCommands.offerPublish, offerAfterSetup),
  );
}

/** The /hosted story at `moment`, with `aid` only while telemetry is on. */
export function storyUrl(src: string, moment: string): string {
  const aid = vscode.env.isTelemetryEnabled
    ? readCliAnonId(readPlatformState(), process.env)
    : null;
  return hostedStoryUrl(src, moment, aid);
}

function readPlatformState(): unknown {
  try {
    const file = path.join(os.homedir(), ".repowise", "platform.json");
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}
