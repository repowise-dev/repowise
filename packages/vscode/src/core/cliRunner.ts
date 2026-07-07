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
  /**
   * Receives each complete stdout line as it arrives, for commands that
   * stream newline-delimited progress. The full stdout is still collected in
   * the result. Callback errors are swallowed so a bad consumer cannot kill
   * the run.
   */
  onStdoutLine?: (line: string) => void;
  /**
   * Runs immediately instead of waiting in the serialized queue. For
   * long-running streams (an index update can take many minutes) that would
   * otherwise make every queued short call appear frozen. The caller is
   * responsible for its own single-flight guard.
   */
  bypassQueue?: boolean;
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

      let lineBuffer = "";
      const emitLines = (text: string, flush: boolean): void => {
        const onLine = options.onStdoutLine;
        if (!onLine) return;
        lineBuffer += text;
        const lines = lineBuffer.split(/\r?\n/);
        lineBuffer = flush ? "" : (lines.pop() ?? "");
        for (const line of lines) {
          if (!line) continue;
          try {
            onLine(line);
          } catch (err) {
            log.debug(`stdout line handler failed: ${String(err)}`);
          }
        }
      };

      child.stdout.on("data", (chunk: Buffer) => {
        const text = chunk.toString();
        stdout += text;
        emitLines(text, false);
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
        // A final line without a trailing newline is still a line.
        emitLines("", true);
        resolve({ code, stdout, stderr });
      });
    });
  }

  function run(args: string[], options: RunOptions = {}): Promise<CliResult> {
    if (options.bypassQueue) return exec(args, options);
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
