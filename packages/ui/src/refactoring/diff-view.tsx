"use client";

import * as React from "react";

/**
 * A self-contained unified-diff renderer for the generated-code panel.
 *
 * It parses a git-style unified diff (`--- a/path`, `+++ b/path`, `@@` hunks)
 * into per-file blocks and colorizes added / removed / context / header lines.
 * Pure presentation, no dependencies — so the hosted frontend can reuse it as
 * is. Unparseable input degrades to a plain monospace block.
 */

type DiffLineKind = "add" | "del" | "hunk" | "meta" | "context";

interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

interface DiffFile {
  /** The b-side path (new file), falling back to the a-side. */
  path: string;
  lines: DiffLine[];
  added: number;
  removed: number;
}

function classifyLine(text: string): DiffLineKind {
  if (text.startsWith("@@")) return "hunk";
  if (
    text.startsWith("diff ") ||
    text.startsWith("index ") ||
    text.startsWith("--- ") ||
    text.startsWith("+++ ") ||
    text.startsWith("new file") ||
    text.startsWith("deleted file") ||
    text.startsWith("similarity ") ||
    text.startsWith("rename ")
  ) {
    return "meta";
  }
  if (text.startsWith("+")) return "add";
  if (text.startsWith("-")) return "del";
  return "context";
}

function stripPathPrefix(raw: string): string {
  // `+++ b/pkg/x.py` → `pkg/x.py`; tolerate a missing a//b/ prefix.
  const path = raw.replace(/^[+-]{3}\s+/, "").trim();
  if (path === "/dev/null") return path;
  return path.replace(/^[ab]\//, "");
}

/** Split a unified diff into per-file blocks with add/remove tallies. */
export function parseUnifiedDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];

  const open = (path: string): DiffFile => {
    const file: DiffFile = { path, lines: [], added: 0, removed: 0 };
    files.push(file);
    return file;
  };

  const last = (): DiffFile => files[files.length - 1] ?? open("");

  for (const rawText of diff.split("\n")) {
    const text = rawText.replace(/\r$/, "");
    const kind = classifyLine(text);

    if (text.startsWith("diff ") || text.startsWith("+++ ")) {
      const path = text.startsWith("+++ ")
        ? stripPathPrefix(text)
        : text.replace(/^diff\s+--git\s+/, "");
      const cur = files[files.length - 1];
      if (text.startsWith("+++ ") && cur && cur.lines.length === 0) {
        cur.path = path; // refine the path from the +++ header
      } else if (!cur || cur.lines.length > 0) {
        open(path);
      }
    }

    const file = last();
    file.lines.push({ kind, text });
    if (kind === "add") file.added += 1;
    else if (kind === "del") file.removed += 1;
  }

  // Drop a leading empty synthetic file if the diff started mid-content.
  return files.filter((f) => f.lines.length > 0);
}

const LINE_CLASS: Record<DiffLineKind, string> = {
  add: "bg-[var(--color-success)]/10 text-[var(--color-text-primary)]",
  del: "bg-[var(--color-error)]/10 text-[var(--color-text-primary)]",
  hunk: "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]",
  meta: "text-[var(--color-text-tertiary)]",
  context: "text-[var(--color-text-secondary)]",
};

export interface DiffViewProps {
  diff: string;
  /** When the diff is empty, render this instead (e.g. the raw content). */
  emptyFallback?: React.ReactNode;
}

export function DiffView({ diff, emptyFallback }: DiffViewProps) {
  const files = React.useMemo(() => parseUnifiedDiff(diff), [diff]);

  if (!diff.trim() || files.length === 0) {
    return <>{emptyFallback ?? null}</>;
  }

  return (
    <div className="space-y-3">
      {files.map((file, i) => (
        <div
          key={`${file.path}-${i}`}
          className="overflow-hidden rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]"
        >
          <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-1.5">
            <span className="truncate font-mono text-xs text-[var(--color-text-primary)]" title={file.path}>
              {file.path || "(diff)"}
            </span>
            <span className="shrink-0 font-mono text-[11px] tabular-nums">
              <span className="text-[var(--color-success)]">+{file.added}</span>{" "}
              <span className="text-[var(--color-error)]">-{file.removed}</span>
            </span>
          </div>
          <pre className="overflow-x-auto px-0 py-1 text-[12px] leading-[1.5]">
            <code className="block">
              {file.lines.map((line, j) => (
                <span key={j} className={`block px-3 ${LINE_CLASS[line.kind]}`}>
                  {line.text || " "}
                </span>
              ))}
            </code>
          </pre>
        </div>
      ))}
    </div>
  );
}
