import * as path from "node:path";
import type { SpawnOptions } from "node:child_process";

/**
 * Env var naming the launcher, so the CLI's usage telemetry labels a run the
 * extension started as `vscode_ext` instead of guessing from the terminal.
 */
export const CLIENT_ENV = { REPOWISE_CLIENT: "vscode_ext" } as const;

/** Environment for every CLI process the extension spawns. */
export function cliEnv(base: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  return { ...base, ...CLIENT_ENV };
}

/** A `.cmd`/`.bat` shim, which Windows cannot run without a shell. */
export function isBatchShim(executable: string, platform = process.platform): boolean {
  return platform === "win32" && /\.(cmd|bat)$/i.test(executable);
}

/** Tokens safe to pass through `cmd.exe` unquoted: no metacharacters. */
const PLAIN_CMD_ARG = /^[\w.:/\\=@+-]+$/;

/**
 * How to spawn the CLI: the command, its argv, and spawn options. Never uses a
 * shell, except for a `.cmd`/`.bat` shim on Windows, which Node refuses to
 * spawn directly. That one goes through `cmd.exe /d /s /c` with the shim path
 * quoted; the extension only passes fixed flag tokens, and any argument with a
 * cmd metacharacter is refused rather than escaped.
 */
export function cliSpawn(
  executable: string,
  args: string[],
  options: SpawnOptions,
  platform = process.platform,
): { command: string; args: string[]; options: SpawnOptions } {
  const base: SpawnOptions = { ...options, shell: false, env: cliEnv() };
  if (!isBatchShim(executable, platform)) {
    return { command: executable, args, options: base };
  }
  if (executable.includes('"')) {
    throw new Error("repowise.cliPath must not contain a double quote.");
  }
  for (const arg of args) {
    if (!PLAIN_CMD_ARG.test(arg)) {
      throw new Error(`Refusing to pass "${arg}" to a .cmd shim through cmd.exe.`);
    }
  }
  // With /s, cmd strips the outer quote pair and runs the rest verbatim.
  const line = `""${executable}" ${args.join(" ")}"`;
  return {
    command: process.env["ComSpec"] || "cmd.exe",
    args: ["/d", "/s", "/c", line],
    options: { ...base, windowsVerbatimArguments: true },
  };
}

/** The shell family a terminal runs, which decides how a command is quoted. */
export type ShellKind = "powershell" | "cmd" | "posix";

/**
 * Classifies a terminal shell from VS Code's detected shell type (`pwsh`,
 * `cmd`, `bash`, ...) or a shell executable path (`vscode.env.shell`).
 * Unknown shells read as POSIX off Windows; on Windows an unknown or missing
 * shell reads as PowerShell, the default terminal there.
 */
export function shellKindOf(shell: string | undefined, platform = process.platform): ShellKind {
  const name = path.win32
    .basename(shell ?? "")
    .toLowerCase()
    .replace(/\.exe$/, "");
  if (name === "pwsh" || name === "powershell") return "powershell";
  if (name === "cmd") return "cmd";
  if (name) return "posix";
  return platform === "win32" ? "powershell" : "posix";
}

/** Executable names that need no quoting; POSIX shells also eat backslashes. */
const PLAIN_EXE = /^[\w@+=:,./\\-]+$/;
const PLAIN_POSIX_EXE = /^[\w@+=:,./-]+$/;

/**
 * The line to type into a terminal to run the CLI. A path with spaces is
 * quoted for the shell: PowerShell needs the call operator (`& 'path' args`)
 * or it reads the quoted path as a string, cmd takes double quotes, POSIX
 * shells take single quotes.
 */
export function terminalCommandLine(executable: string, args: string[], kind: ShellKind): string {
  const tail = args.length ? ` ${args.join(" ")}` : "";
  const plain = kind === "posix" ? PLAIN_POSIX_EXE : PLAIN_EXE;
  if (plain.test(executable)) return `${executable}${tail}`;
  switch (kind) {
    case "powershell":
      return `& '${executable.replace(/'/g, "''")}'${tail}`;
    case "cmd":
      return `"${executable}"${tail}`;
    case "posix":
      return `'${executable.replace(/'/g, "'\\''")}'${tail}`;
  }
}
