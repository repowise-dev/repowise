/**
 * Which mode Lens runs in, and how fresh the index is. Same discovery order as
 * the VS Code extension: lock file, pid alive, /health probe. Read-only
 * throughout: nothing here starts, stops or writes anything.
 */

import { getHealth } from "@repowise-dev/api-client/health";
import { listRepos } from "@repowise-dev/api-client/repos";
import { ApiClientError } from "@repowise-dev/api-client";
import { normalizeRepoPath } from "@repowise-dev/types/repos";
import { isServeLock, type ServeLock } from "@repowise-dev/types/serve-lock";
import type { Host } from "../host";
import type { IndexFreshness, LiteReason, Mode } from "../model/session";
import { connectApiClient, withTimeout } from "./transport";

export interface Discovery {
  mode: Mode;
  liteReason?: LiteReason;
  repoRoot: string | null;
}

const HEALTH_TIMEOUT_MS = 800;
const LIST_TIMEOUT_MS = 3_000;
/** A little over the engine's own 30 s MCP connection timeout. */
const CONNECT_TIMEOUT_MS = 35_000;
const GIT_TIMEOUT_MS = 5_000;
const MAX_WALK = 20;
const SHA = /^[0-9a-f]{7,64}$/i;

export function isWindowsPath(p: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(p) || p.startsWith("\\\\");
}

function join(dir: string, rel: string): string {
  return `${dir.replace(/[\\/]+$/, "")}/${rel}`;
}

function parentDir(p: string): string | null {
  const trimmed = p.replace(/[\\/]+$/, "");
  const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  if (cut < 0) return null;
  if (cut === 0) return trimmed === "" ? null : "/";
  return trimmed.slice(0, cut);
}

/**
 * The nearest directory at or above `cwd` holding an index. The marker is
 * `.repowise/state.json`, not the bare directory: `~/.repowise` is the user
 * config dir and has no state file, so it never reads as an indexed repo.
 */
export async function findIndexedRoot(host: Host, cwd: string): Promise<string | null> {
  let dir: string | null = cwd;
  for (let i = 0; dir !== null && i < MAX_WALK; i++) {
    if (await host.fs.exists(join(dir, ".repowise/state.json"))) return dir;
    dir = parentDir(dir);
  }
  return null;
}

async function readJson(host: Host, path: string): Promise<unknown> {
  try {
    return JSON.parse(await host.fs.read(path)) as unknown;
  } catch {
    return null;
  }
}

/** The parsed lock, or null when missing or malformed. A stale lock still parses: gate on the pid. */
export async function readServeLock(host: Host, repoRoot: string): Promise<ServeLock | null> {
  const parsed = await readJson(host, join(repoRoot, ".repowise/serve.lock.json"));
  return isServeLock(parsed) ? parsed : null;
}

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

/**
 * Whether the lock points at plain http on this machine. The lock sits in the
 * repo, so a cloned repo can carry one naming any host, and a pid gate is no
 * defence (some pids are always alive). Lens probes nothing else.
 */
export function isLoopbackUrl(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "http:" && LOOPBACK_HOSTS.has(u.hostname);
  } catch {
    return false;
  }
}

/**
 * Whether a process with this pid exists: true, false, or null when the probe
 * could not run (no process API on this surface). A null does not block the
 * /health probe, which still has to answer.
 */
export async function isPidAlive(host: Host, pid: number, cwd: string): Promise<boolean | null> {
  try {
    if (isWindowsPath(cwd)) {
      const out = await host.process.run(["tasklist", "/FI", `PID eq ${pid}`, "/NH", "/FO", "CSV"], cwd);
      return out.exitCode === 0 && out.stdout.includes(`"${pid}"`);
    }
    const out = await host.process.run(["ps", "-p", String(pid), "-o", "pid="], cwd);
    return out.exitCode === 0 && out.stdout.trim() === String(pid);
  } catch {
    return null;
  }
}

type ServerProbe = { kind: "ok"; repoId: string } | { kind: "down" } | { kind: "auth" } | { kind: "unlisted" };

/**
 * Health-probes the server the lock names, then finds this repo in its list.
 * A server can list several repos (a workspace DB), so the match is on
 * `local_path`, never the first row. Loopback needs no key; a 401 means one
 * is configured, which Lens will not read.
 */
export async function probeServer(host: Host, lock: ServeLock, repoRoot: string): Promise<ServerProbe> {
  connectApiClient(host, lock.url);
  try {
    await withTimeout(getHealth(), HEALTH_TIMEOUT_MS, "health");
  } catch {
    return { kind: "down" };
  }
  try {
    const repos = await withTimeout(listRepos(), LIST_TIMEOUT_MS, "listRepos");
    const win = isWindowsPath(repoRoot);
    const target = normalizeRepoPath(repoRoot, win);
    const match = repos.find((r) => r.local_path && normalizeRepoPath(r.local_path, win) === target);
    return match ? { kind: "ok", repoId: match.id } : { kind: "unlisted" };
  } catch (err) {
    if (err instanceof ApiClientError && (err.status === 401 || err.status === 403)) return { kind: "auth" };
    return { kind: "down" };
  }
}

/**
 * Whether this plugin's MCP server connects. Connecting, not calling a tool:
 * a single-repo server does not list `list_repos`, and the engine's own
 * connect says outright when `repowise mcp` cannot start.
 */
export async function mcpReachable(host: Host): Promise<boolean> {
  try {
    return await withTimeout(host.mcp.connect(), CONNECT_TIMEOUT_MS, "mcp connect");
  } catch {
    return false;
  }
}

/** Mode for a directory with no index: quiet outside a git work tree, else depends on the CLI. */
async function unindexedMode(host: Host, cwd: string): Promise<Mode> {
  const inWorkTree = (await git(host, cwd, ["rev-parse", "--is-inside-work-tree"]))?.trim() === "true";
  if (!inWorkTree) return "no-repo";
  return (await mcpReachable(host)) ? "no-index" : "no-cli";
}

/** The local server's state: only a loopback lock with a pid not known dead is probed. */
async function serverState(host: Host, repoRoot: string): Promise<ServerProbe["kind"]> {
  const lock = await readServeLock(host, repoRoot);
  if (!lock || !isLoopbackUrl(lock.url)) return "down";
  if ((await isPidAlive(host, lock.pid, repoRoot)) === false) return "down";
  return (await probeServer(host, lock, repoRoot)).kind;
}

export async function discover(host: Host): Promise<Discovery> {
  const cwd = await host.session.cwd();
  const repoRoot = await findIndexedRoot(host, cwd);
  if (repoRoot === null) return { mode: await unindexedMode(host, cwd), repoRoot };
  const server = await serverState(host, repoRoot);
  if (server === "ok") return { mode: "full", repoRoot };
  if (!(await mcpReachable(host))) return { mode: "no-cli", repoRoot };
  const liteReason: LiteReason = server === "down" ? "no-server" : server;
  return { mode: "lite", liteReason, repoRoot };
}

async function git(host: Host, cwd: string, args: string[]): Promise<string | null> {
  try {
    const out = await withTimeout(host.process.run(["git", ...args], cwd), GIT_TIMEOUT_MS, "git");
    return out.exitCode === 0 ? out.stdout : null;
  } catch {
    return null;
  }
}

/**
 * True while `repowise update` holds its lock with a live pid. The band stays
 * quiet then rather than asking for an update that is already running.
 * Ceiling: the post-commit hook's short-lived `.update.queued` marker is not
 * read, so the row can show for the few seconds before the update starts.
 */
async function updateRunning(host: Host, repoRoot: string): Promise<boolean> {
  const lock = await readJson(host, join(repoRoot, ".repowise/.update.lock"));
  const pid = (lock as { pid?: unknown } | null)?.pid;
  return typeof pid === "number" && (await isPidAlive(host, pid, repoRoot)) === true;
}

/**
 * Index freshness, compared the way the SessionStart hook does it: the
 * state file's `last_sync_commit` against `git rev-parse HEAD`, and the count
 * from `git diff --name-only`. Read-only git only. null means nothing to
 * say: current, unknown, or an update already running.
 */
export async function readFreshness(host: Host, repoRoot: string): Promise<IndexFreshness | null> {
  const state = await readJson(host, join(repoRoot, ".repowise/state.json"));
  const indexed = (state as { last_sync_commit?: unknown } | null)?.last_sync_commit;
  // The state file is repo content: only a bare hex sha may reach git's argv.
  if (typeof indexed !== "string" || !SHA.test(indexed)) return null;
  const head = (await git(host, repoRoot, ["rev-parse", "HEAD"]))?.trim();
  if (!head || !SHA.test(head) || head === indexed) return null;
  if (await updateRunning(host, repoRoot)) return null;
  const diff = await git(host, repoRoot, ["diff", "--name-only", indexed, head, "--"]);
  const changedFiles = diff === null ? null : diff.split("\n").filter((l) => l.trim() !== "").length;
  return { changedFiles };
}
