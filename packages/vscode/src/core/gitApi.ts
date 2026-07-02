import * as vscode from "vscode";

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
