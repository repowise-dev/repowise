import * as assert from "node:assert";
import * as os from "node:os";
import * as path from "node:path";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import type * as vscode from "vscode";
import {
  cliEnv,
  cliSpawn,
  shellKindOf,
  terminalCommandLine,
} from "../core/cliInvocation";
import {
  isPidAlive,
  killProcessTree,
  planStop,
  removeStaleLockfile,
  waitForPidExit,
} from "../core/serverProcess";
import {
  AutoStartPrompt,
  resolveAutoStart,
  serverMenuItems,
  type AutoStartMode,
} from "../features/serverActions";
import { hasMcpProviderApi } from "../core/hostApi";
import { registerFeature } from "../core/registerFeature";

/** A pid no process holds: above the Windows and Linux pid ceilings. */
const DEAD_PID = 0x7ffffff0;

function writeLock(dir: string, pid: number): string {
  const lockPath = path.join(dir, "serve.lock.json");
  writeFileSync(
    lockPath,
    JSON.stringify({
      pid,
      host: "127.0.0.1",
      port: 7337,
      url: "http://127.0.0.1:7337",
      ui_port: null,
      server_version: "0.63.0",
      started_at: new Date().toISOString(),
    }),
  );
  return lockPath;
}

describe("autoStart", () => {
  function ui(answer: string | undefined) {
    const calls = { asked: 0, persisted: [] as AutoStartMode[], message: "" };
    return {
      calls,
      ui: {
        ask: (message: string) => {
          calls.asked++;
          calls.message = message;
          return Promise.resolve(answer);
        },
        persist: (mode: AutoStartMode) => {
          calls.persisted.push(mode);
          return Promise.resolve();
        },
      },
    };
  }

  it("asks on the default setting and starts on Start without persisting", async () => {
    const { calls, ui: deps } = ui(AutoStartPrompt.start);
    assert.strictEqual(await resolveAutoStart("ask", deps), true);
    assert.strictEqual(calls.asked, 1);
    assert.deepStrictEqual(calls.persisted, []);
    assert.ok(calls.message.includes("MCP tools work without it"));
    assert.ok(calls.message.includes("status bar"));
  });

  it("persists Always and Never", async () => {
    const always = ui(AutoStartPrompt.always);
    assert.strictEqual(await resolveAutoStart("ask", always.ui), true);
    assert.deepStrictEqual(always.calls.persisted, ["always"]);

    const never = ui(AutoStartPrompt.never);
    assert.strictEqual(await resolveAutoStart("ask", never.ui), false);
    assert.deepStrictEqual(never.calls.persisted, ["never"]);
  });

  it("treats a dismissed prompt as no", async () => {
    const { calls, ui: deps } = ui(undefined);
    assert.strictEqual(await resolveAutoStart("ask", deps), false);
    assert.deepStrictEqual(calls.persisted, []);
  });

  it("never asks when the setting already answers", async () => {
    const always = ui(undefined);
    assert.strictEqual(await resolveAutoStart("always", always.ui), true);
    const never = ui(undefined);
    assert.strictEqual(await resolveAutoStart("never", never.ui), false);
    assert.strictEqual(always.calls.asked + never.calls.asked, 0);
  });
});

describe("server stop", () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(path.join(os.tmpdir(), "repowise-lock-"));
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("plans by who started the server", () => {
    const alive = () => true;
    const dead = () => false;
    assert.strictEqual(planStop(true, { pid: 1 }, alive), "own");
    assert.strictEqual(planStop(true, null, alive), "own");
    assert.strictEqual(planStop(false, { pid: 1 }, alive), "external");
    assert.strictEqual(planStop(false, { pid: 1 }, dead), "stale");
    assert.strictEqual(planStop(false, null, alive), "none");
  });

  it("removes a lockfile whose pid is gone", () => {
    const lockPath = writeLock(dir, DEAD_PID);
    assert.strictEqual(isPidAlive(DEAD_PID), false);
    assert.strictEqual(removeStaleLockfile(lockPath), true);
    assert.strictEqual(existsSync(lockPath), false);
  });

  it("keeps a lockfile whose pid is alive", () => {
    const lockPath = writeLock(dir, process.pid);
    assert.strictEqual(removeStaleLockfile(lockPath), false);
    assert.strictEqual(existsSync(lockPath), true);
  });

  it("stops a server started elsewhere by its lockfile pid", async () => {
    // Stand-in for `repowise serve` started in a terminal.
    const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
      env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
      stdio: "ignore",
      windowsHide: true,
    });
    assert.ok(child.pid);
    const lockPath = writeLock(dir, child.pid);
    assert.strictEqual(planStop(false, { pid: child.pid }), "external");

    killProcessTree(child.pid);
    assert.strictEqual(await waitForPidExit(child.pid, 10_000), true);
    assert.strictEqual(removeStaleLockfile(lockPath), true);
    assert.strictEqual(existsSync(lockPath), false);
  });

  it("offers the actions that fit the state", () => {
    const actions = (running: boolean, webUi: boolean) =>
      serverMenuItems(running, webUi).map((i) => i.action);
    assert.deepStrictEqual(actions(false, false), ["start", "log"]);
    assert.deepStrictEqual(actions(true, false), ["stop", "restart", "log"]);
    assert.deepStrictEqual(actions(true, true), ["stop", "restart", "webUi", "log"]);
  });
});

describe("terminal quoting", () => {
  const spaced = "C:\\Program Files\\repowise\\repowise.exe";

  it("invokes a spaced path through the call operator in PowerShell", () => {
    assert.strictEqual(
      terminalCommandLine(spaced, ["init"], "powershell"),
      "& 'C:\\Program Files\\repowise\\repowise.exe' init",
    );
    assert.strictEqual(
      terminalCommandLine("C:\\it's here\\repowise.exe", ["publish"], "powershell"),
      "& 'C:\\it''s here\\repowise.exe' publish",
    );
  });

  it("double-quotes for cmd and single-quotes for POSIX shells", () => {
    assert.strictEqual(
      terminalCommandLine(spaced, ["init"], "cmd"),
      `"${spaced}" init`,
    );
    assert.strictEqual(
      terminalCommandLine("/opt/my tools/repowise", ["init"], "posix"),
      "'/opt/my tools/repowise' init",
    );
    // Git Bash would eat unquoted backslashes.
    assert.strictEqual(
      terminalCommandLine("C:\\tools\\repowise.exe", ["init"], "posix"),
      "'C:\\tools\\repowise.exe' init",
    );
  });

  it("leaves a plain executable alone", () => {
    for (const kind of ["powershell", "cmd", "posix"] as const) {
      assert.strictEqual(terminalCommandLine("repowise", ["init"], kind), "repowise init");
    }
  });

  it("detects the shell from its type or path", () => {
    assert.strictEqual(shellKindOf("pwsh"), "powershell");
    assert.strictEqual(
      shellKindOf("C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"),
      "powershell",
    );
    assert.strictEqual(shellKindOf("C:\\WINDOWS\\system32\\cmd.exe"), "cmd");
    assert.strictEqual(shellKindOf("gitbash"), "posix");
    assert.strictEqual(shellKindOf("/bin/zsh", "darwin"), "posix");
    assert.strictEqual(shellKindOf(undefined, "win32"), "powershell");
    assert.strictEqual(shellKindOf(undefined, "linux"), "posix");
  });
});

describe("CLI spawn", () => {
  it("labels every spawn for telemetry", () => {
    assert.strictEqual(cliEnv({ PATH: "x" })["REPOWISE_CLIENT"], "vscode_ext");
    const spec = cliSpawn("repowise", ["--version"], {}, "linux");
    assert.strictEqual(spec.command, "repowise");
    assert.strictEqual(spec.options.shell, false);
    assert.strictEqual(spec.options.env?.["REPOWISE_CLIENT"], "vscode_ext");
  });

  it("runs a Windows .cmd shim through cmd.exe with the path quoted", () => {
    const spec = cliSpawn("C:\\My Tools\\repowise.cmd", ["serve", "--no-ui"], {}, "win32");
    assert.match(spec.command, /cmd(\.exe)?$/i);
    assert.deepStrictEqual(spec.args, [
      "/d",
      "/s",
      "/c",
      '""C:\\My Tools\\repowise.cmd" serve --no-ui"',
    ]);
    assert.strictEqual(spec.options.windowsVerbatimArguments, true);
  });

  it("refuses an argument cmd.exe would interpret", () => {
    assert.throws(() => cliSpawn("repowise.cmd", ["a&b"], {}, "win32"));
  });
});

describe("activation guard", () => {
  it("detects a host without the MCP provider API", () => {
    assert.strictEqual(hasMcpProviderApi({}), false);
    assert.strictEqual(hasMcpProviderApi({ lm: {} }), false);
    assert.strictEqual(
      hasMcpProviderApi({
        lm: { registerMcpServerDefinitionProvider: (() => undefined) as never },
      }),
      false,
    );
    assert.strictEqual(
      hasMcpProviderApi({
        lm: { registerMcpServerDefinitionProvider: (() => undefined) as never },
        McpStdioServerDefinition: class {},
      }),
      true,
    );
  });

  it("keeps activating when one feature throws", () => {
    const errors: string[] = [];
    const log = { error: (m: string) => errors.push(m) };
    const failed = registerFeature(
      "mcp",
      () => {
        throw new TypeError("vscode.lm is undefined");
      },
      log,
    );
    assert.strictEqual(failed, undefined);
    assert.strictEqual(errors.length, 1);
    const ok = { dispose: () => undefined } as vscode.Disposable;
    assert.strictEqual(registerFeature("docs", () => ok, log), ok);
  });
});
