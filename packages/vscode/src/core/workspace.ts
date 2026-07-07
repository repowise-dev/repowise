import * as vscode from "vscode";
import * as path from "node:path";
import { existsSync } from "node:fs";
import { LOCKFILE_NAME, REPO_DIR, WORKSPACE_DIR } from "../constants";

/** A workspace folder that carries a Repowise index. */
export interface RepoCandidate {
  /** Absolute path to the folder that contains the marker directory. */
  root: string;
  /** True when the marker is `.repowise-workspace` (multi-repo workspace mode). */
  workspaceMode: boolean;
  /** Absolute path to the discovery lockfile for this candidate. */
  lockfilePath: string;
}

export interface WorkspaceInfo {
  /** Primary repo root, or null when no workspace folder is Repowise-enabled. */
  repoRoot: string | null;
  /** Lockfile path for the primary candidate, or null. */
  lockfilePath: string | null;
  /** True when the primary candidate is a `.repowise-workspace` root. */
  workspaceMode: boolean;
  /** Every Repowise-enabled folder found, in workspace-folder order. */
  candidates: RepoCandidate[];
}

function markerDir(root: string): { dir: string; workspaceMode: boolean } | null {
  // Prefer a single-repo marker; fall back to a workspace-mode marker.
  if (existsSync(path.join(root, REPO_DIR))) {
    return { dir: REPO_DIR, workspaceMode: false };
  }
  if (existsSync(path.join(root, WORKSPACE_DIR))) {
    return { dir: WORKSPACE_DIR, workspaceMode: true };
  }
  return null;
}

/**
 * Scans open workspace folders for a Repowise marker. Multi-root: the first
 * folder that carries `.repowise` wins as the primary; every match is exposed
 * as a candidate so later work can offer a picker.
 */
export function detectWorkspace(
  folders: readonly vscode.WorkspaceFolder[] | undefined = vscode.workspace
    .workspaceFolders,
): WorkspaceInfo {
  const candidates: RepoCandidate[] = [];
  for (const folder of folders ?? []) {
    const root = folder.uri.fsPath;
    const marker = markerDir(root);
    if (!marker) continue;
    candidates.push({
      root,
      workspaceMode: marker.workspaceMode,
      lockfilePath: path.join(root, marker.dir, LOCKFILE_NAME),
    });
  }
  const primary = candidates[0] ?? null;
  return {
    repoRoot: primary?.root ?? null,
    lockfilePath: primary?.lockfilePath ?? null,
    workspaceMode: primary?.workspaceMode ?? false,
    candidates,
  };
}
