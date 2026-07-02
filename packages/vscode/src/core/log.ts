import * as vscode from "vscode";

/**
 * Narrow logging surface the rest of the extension codes against, so no feature
 * module has to hold the raw output channel. Backed by a single
 * LogOutputChannel, which gives us log levels and a user-visible panel for free.
 */
export interface Logger {
  trace(message: string, ...args: unknown[]): void;
  debug(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
  warn(message: string, ...args: unknown[]): void;
  error(message: string | Error, ...args: unknown[]): void;
  /** Reveal the log panel to the user. */
  show(): void;
}

/** Creates the one log channel for the extension. Dispose it on deactivate. */
export function createLogger(name = "Repowise"): Logger & vscode.Disposable {
  const channel = vscode.window.createOutputChannel(name, { log: true });
  return {
    trace: (message, ...args) => channel.trace(message, ...args),
    debug: (message, ...args) => channel.debug(message, ...args),
    info: (message, ...args) => channel.info(message, ...args),
    warn: (message, ...args) => channel.warn(message, ...args),
    error: (message, ...args) => channel.error(message as string, ...args),
    show: () => channel.show(true),
    dispose: () => channel.dispose(),
  };
}
