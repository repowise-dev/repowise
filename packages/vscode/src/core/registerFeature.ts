import type * as vscode from "vscode";
import type { Logger } from "./log";

/**
 * Registers one feature, logging instead of throwing when the host lacks an
 * API it needs. Returns its Disposable, or undefined when registration failed.
 */
export function registerFeature(
  name: string,
  register: () => vscode.Disposable,
  log: Pick<Logger, "error">,
): vscode.Disposable | undefined {
  try {
    return register();
  } catch (err) {
    log.error(`Could not enable ${name} in this editor: ${String(err)}`);
    return undefined;
  }
}
