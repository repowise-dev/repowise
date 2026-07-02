import { spawn } from "node:child_process";
import type { Logger } from "./log";

/** Outcome of one CLI invocation. */
export interface CliResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

export interface RunOptions {
  /** Working directory for the child process. */
  cwd?: string;
  /** Kill the process after this many ms. */
  timeoutMs?: number;
}

/** Structured payload of `repowise doctor --format json`. */
export interface DoctorReport {
  ok: boolean;
  checks: Array<{ name: string; ok: boolean; detail: string }>;
  workspace?: unknown;
}

/**
 * Runs short-lived CLI invocations one at a time. Calls are serialized so
 * concurrent commands cannot interleave on the same index. Never uses a shell:
 * arguments are passed as an array so nothing in a path or workspace name is
 * interpreted by a shell.
 */
export interface CliRunner {
  /**
   * Resolved executable: the `repowise.cliPath` setting, else `repowise`.
   * Re-resolved on every read so a settings change applies without a reload.
   */
  readonly executable: string;
  run(args: string[], options?: RunOptions): Promise<CliResult>;
  /** Runs `doctor --format json` (read-only) and parses the report. */
  runDoctorJson(cwd?: string): Promise<DoctorReport>;
}

const DEFAULT_TIMEOUT_MS = 30_000;

export function createCliRunner(
  log: Logger,
  resolveExecutable: () => string,
): CliRunner {
  let queue: Promise<unknown> = Promise.resolve();
  const currentExecutable = (): string => resolveExecutable() || "repowise";

  function exec(args: string[], options: RunOptions): Promise<CliResult> {
    return new Promise<CliResult>((resolve, reject) => {
      const executable = currentExecutable();
      log.debug(`cli: ${executable} ${args.join(" ")}`);
      const child = spawn(executable, args, {
        cwd: options.cwd,
        shell: false,
        windowsHide: true,
      });
      let stdout = "";
      let stderr = "";
      let settled = false;

      const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        child.kill();
        reject(new Error(`CLI timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      child.stdout.on("data", (chunk: Buffer) => {
        stdout += chunk.toString();
      });
      child.stderr.on("data", (chunk: Buffer) => {
        stderr += chunk.toString();
      });
      child.on("error", (err) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(err);
      });
      child.on("close", (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve({ code, stdout, stderr });
      });
    });
  }

  function run(args: string[], options: RunOptions = {}): Promise<CliResult> {
    // Chain onto the queue so only one child runs at a time. Failures do not
    // poison the queue: the next call runs regardless.
    const result = queue.then(
      () => exec(args, options),
      () => exec(args, options),
    );
    queue = result.catch(() => undefined);
    return result;
  }

  return {
    get executable(): string {
      return currentExecutable();
    },
    run,
    async runDoctorJson(cwd?: string): Promise<DoctorReport> {
      // Read-only diagnosis: never pass --repair alongside --format json.
      // Doctor inspects the index and store, which can take well over the
      // default timeout on a large repository.
      const { stdout } = await run(["doctor", "--format", "json"], {
        timeoutMs: 120_000,
        ...(cwd ? { cwd } : {}),
      });
      return JSON.parse(stdout) as DoctorReport;
    },
  };
}
