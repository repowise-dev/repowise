import * as vscode from "vscode";
import { readFile } from "node:fs/promises";
import * as path from "node:path";

/**
 * Guarded adapter over the built-in `vscode.git` extension. We deliberately do
 * NOT declare an extensionDependency on it, so every path here degrades to
 * null / a no-op when git is missing, disabled, or not yet initialized. Callers
 * fall back to reading `.git/HEAD` directly when this returns null.
 *
 * Only the members actually used are typed below; the full `git.d.ts` surface is
 * intentionally not vendored.
 */

/** `onDidChangeState` values reported by the git API. */
type APIState = "uninitialized" | "initialized";

interface Branch {
  readonly name?: string;
  readonly commit?: string;
}

interface RepositoryState {
  readonly HEAD?: Branch;
  readonly onDidChange: vscode.Event<void>;
}

interface Repository {
  readonly state: RepositoryState;
}

interface GitAPI {
  readonly state: APIState;
  readonly onDidChangeState: vscode.Event<APIState>;
  getRepository(uri: vscode.Uri): Repository | null;
}

interface GitExtension {
  readonly enabled: boolean;
  getAPI(version: 1): GitAPI;
}

/**
 * Resolves the git API, or null when unavailable. Checks `enabled` BEFORE
 * `getAPI(1)`, because `getAPI` throws "Git model not found" when
 * `git.enabled` is false. Waits for the API to reach `initialized` so an
 * early call does not read an empty repository set.
 */
async function getApi(): Promise<GitAPI | null> {
  try {
    const ext = vscode.extensions.getExtension<GitExtension>("vscode.git");
    if (!ext) return null;
    const exports = ext.isActive ? ext.exports : await ext.activate();
    if (!exports.enabled) return null;
    const api = exports.getAPI(1);
    if (api.state === "initialized") return api;
    return await waitForInitialized(api);
  } catch {
    return null;
  }
}

/** Resolves once the API reports `initialized`, or null after a short wait. */
function waitForInitialized(api: GitAPI): Promise<GitAPI | null> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      sub.dispose();
      resolve(null);
    }, 5_000);
    const sub = api.onDidChangeState((state) => {
      if (state !== "initialized") return;
      clearTimeout(timer);
      sub.dispose();
      resolve(api);
    });
  });
}

/** Resolves the repository for a root, or null when git cannot serve it. */
async function getRepository(repoRoot: string): Promise<Repository | null> {
  const api = await getApi();
  if (!api) return null;
  try {
    return api.getRepository(vscode.Uri.file(repoRoot));
  } catch {
    return null;
  }
}

/** Current branch name, or null when detached, unavailable, or git is off. */
export async function getCurrentBranchName(
  repoRoot: string,
): Promise<string | null> {
  const repo = await getRepository(repoRoot);
  return repo?.state.HEAD?.name ?? null;
}

/** Checked-out commit sha, or null when unavailable or git is off. */
export async function getHeadCommit(repoRoot: string): Promise<string | null> {
  const repo = await getRepository(repoRoot);
  return repo?.state.HEAD?.commit ?? null;
}

/**
 * Checked-out commit, preferring the git extension and falling back to reading
 * `.git/HEAD` from disk. Null when neither side can resolve it.
 */
export async function resolveLiveHead(repoRoot: string): Promise<string | null> {
  const viaGit = await getHeadCommit(repoRoot);
  if (viaGit) return viaGit;
  return readHeadFromDisk(repoRoot);
}

/**
 * Reads the checked-out commit from `.git` when the git extension is
 * unavailable. Resolves a symbolic ref to its loose ref file. Packed refs are
 * not read here; that path degrades to null (no false staleness) and the git
 * extension covers it when enabled.
 */
async function readHeadFromDisk(root: string): Promise<string | null> {
  try {
    const raw = (await readFile(path.join(root, ".git", "HEAD"), "utf8")).trim();
    if (raw.startsWith("ref:")) {
      const ref = raw.slice(4).trim();
      const refPath = path.join(root, ".git", ...ref.split("/"));
      const sha = (await readFile(refPath, "utf8")).trim();
      return sha || null;
    }
    // Detached HEAD: the file holds the commit id directly.
    return raw || null;
  } catch {
    return null;
  }
}

/** True when two commit ids refer to the same commit (tolerates short shas). */
export function commitsMatch(a: string, b: string): boolean {
  if (a === b) return true;
  const shorter = a.length < b.length ? a : b;
  const longer = a.length < b.length ? b : a;
  return shorter.length > 0 && longer.startsWith(shorter);
}

/**
 * Invokes `cb` when the repository's HEAD name or commit changes. Returns a
 * disposable, or a no-op disposable when git is unavailable. The underlying
 * `state.onDidChange` fires on any repository change, so we filter to HEAD
 * transitions to avoid waking callers on unrelated working-tree events.
 */
export async function onDidChangeHead(
  repoRoot: string,
  cb: () => void,
): Promise<vscode.Disposable> {
  const repo = await getRepository(repoRoot);
  if (!repo) return { dispose: () => {} };
  let lastName = repo.state.HEAD?.name;
  let lastCommit = repo.state.HEAD?.commit;
  return repo.state.onDidChange(() => {
    const name = repo.state.HEAD?.name;
    const commit = repo.state.HEAD?.commit;
    if (name === lastName && commit === lastCommit) return;
    lastName = name;
    lastCommit = commit;
    cb();
  });
}
