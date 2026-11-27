import type * as vscode from "vscode";

/**
 * The server manager's decisions, kept free of workspace packages so the
 * tests can load them outside the bundle.
 */

export type AutoStartMode = "ask" | "always" | "never";

export interface ServerMenuItem extends vscode.QuickPickItem {
  action: "start" | "stop" | "restart" | "webUi" | "log";
}

/** Status-bar menu entries for the current server state. */
export function serverMenuItems(running: boolean, hasWebUi: boolean): ServerMenuItem[] {
  if (!running) {
    return [
      { label: "$(play) Start Server", action: "start" },
      { label: "$(output) Show Log", action: "log" },
    ];
  }
  const items: ServerMenuItem[] = [
    { label: "$(debug-stop) Stop Server", description: "Frees its memory", action: "stop" },
    { label: "$(debug-restart) Restart Server", action: "restart" },
  ];
  if (hasWebUi) items.push({ label: "$(globe) Open Web UI", action: "webUi" });
  items.push({ label: "$(output) Show Log", action: "log" });
  return items;
}

/** Buttons on the "start the server?" prompt. */
export const AutoStartPrompt = {
  message:
    "Start the Repowise server? It powers the editor views; your agent's MCP tools work without it. Stop it any time from the status bar.",
  start: "Start",
  always: "Always",
  never: "Never",
} as const;

/**
 * Whether to start the server for an indexed repo with none running. "always"
 * and "never" answer alone; "ask" shows one prompt whose Always / Never answers
 * persist the setting so it is not asked again. A dismissed prompt means no.
 */
export async function resolveAutoStart(
  mode: AutoStartMode,
  ui: {
    ask: (message: string, ...items: string[]) => Thenable<string | undefined>;
    persist: (mode: AutoStartMode) => Thenable<void>;
  },
): Promise<boolean> {
  if (mode === "always") return true;
  if (mode === "never") return false;
  const choice = await ui.ask(
    AutoStartPrompt.message,
    AutoStartPrompt.start,
    AutoStartPrompt.always,
    AutoStartPrompt.never,
  );
  // A failed settings write still honours this answer; it is only not saved.
  const save = async (value: AutoStartMode): Promise<void> => {
    try {
      await ui.persist(value);
    } catch {
      // Read-only or invalid settings file: nothing more to do here.
    }
  };
  if (choice === AutoStartPrompt.always) {
    await save("always");
    return true;
  }
  if (choice === AutoStartPrompt.never) {
    await save("never");
    return false;
  }
  return choice === AutoStartPrompt.start;
}
