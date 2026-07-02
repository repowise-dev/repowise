import * as vscode from "vscode";
import { LOCKFILE_NAME } from "../constants";

export type FreshnessEventKind =
  | "headChanged"
  | "indexChanged"
  | "lockfileChanged";

/**
 * Watches the working tree for the two things that make served data go stale:
 * the checked-out commit (`.git/HEAD`) and the index directory (`.repowise/`).
 * Events are debounced so a burst of writes (an index rebuild, a checkout)
 * collapses into one notification per kind.
 *
 * We watch `.git/HEAD` directly rather than depending on the built-in git
 * extension, to keep activation cheap and avoid an extra activation dependency.
 */
export interface FreshnessWatcher extends vscode.Disposable {
  readonly onDidChange: vscode.Event<FreshnessEventKind>;
}

const DEBOUNCE_MS = 500;

/**
 * Creates the watchers for a single repo root. Intentionally created lazily
 * (never during activate()) so a cold start does no filesystem watching.
 * `markerDir` is the index directory name for this root (`.repowise`, or
 * `.repowise-workspace` in multi-repo workspace mode); the lockfile and index
 * artifacts live under it.
 */
export function createFreshnessWatcher(
  repoRoot: string,
  markerDir: string,
): FreshnessWatcher {
  const emitter = new vscode.EventEmitter<FreshnessEventKind>();
  const disposables: vscode.Disposable[] = [emitter];
  const timers = new Map<FreshnessEventKind, ReturnType<typeof setTimeout>>();

  function emitDebounced(kind: FreshnessEventKind): void {
    const existing = timers.get(kind);
    if (existing) clearTimeout(existing);
    timers.set(
      kind,
      setTimeout(() => {
        timers.delete(kind);
        emitter.fire(kind);
      }, DEBOUNCE_MS),
    );
  }

  const headWatcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(repoRoot, ".git/HEAD"),
  );
  headWatcher.onDidChange(() => emitDebounced("headChanged"));
  headWatcher.onDidCreate(() => emitDebounced("headChanged"));
  disposables.push(headWatcher);

  const indexWatcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(repoRoot, `${markerDir}/**`),
  );
  const onIndexEvent = (uri: vscode.Uri): void => {
    emitDebounced(
      uri.path.endsWith(LOCKFILE_NAME) ? "lockfileChanged" : "indexChanged",
    );
  };
  indexWatcher.onDidChange(onIndexEvent);
  indexWatcher.onDidCreate(onIndexEvent);
  indexWatcher.onDidDelete(onIndexEvent);
  disposables.push(indexWatcher);

  return {
    onDidChange: emitter.event,
    dispose(): void {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
      for (const d of disposables) d.dispose();
    },
  };
}
