import * as vscode from "vscode";
import { CLIENT_ENV, shellKindOf, terminalCommandLine } from "./cliInvocation";

const TERMINAL_NAME = "Repowise";

/**
 * Runs the CLI in the shared "Repowise" terminal (reused when open), quoting
 * the executable for whichever shell that terminal runs.
 */
export function runCliInTerminal(cliPath: string, args: string[]): void {
  const terminal =
    vscode.window.terminals.find((t) => t.name === TERMINAL_NAME) ??
    vscode.window.createTerminal({ name: TERMINAL_NAME, env: { ...CLIENT_ENV } });
  terminal.show();
  // The detected shell type is unset until the shell reports in; the default
  // profile's executable is the best guess for a terminal we just created.
  const kind = shellKindOf(terminal.state.shell ?? vscode.env.shell);
  terminal.sendText(terminalCommandLine(cliPath || "repowise", args, kind));
}
