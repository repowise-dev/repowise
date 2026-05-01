"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Clock, Diff, ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { usePageVersions } from "@/lib/hooks/use-page";
import { Badge } from "@repowise/ui/ui/badge";
import { formatRelativeTime } from "@repowise/ui/lib/format";
import type { PageVersionResponse } from "@/lib/api/types";

interface VersionHistoryProps {
  pageId: string;
  currentVersion: number;
  currentContent: string;
}

export function VersionHistory({ pageId, currentVersion, currentContent }: VersionHistoryProps) {
  const { versions, isLoading } = usePageVersions(pageId);
  const [expanded, setExpanded] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState<PageVersionResponse | null>(null);
  const [showDiff, setShowDiff] = useState(false);

  if (isLoading || versions.length === 0) return null;

  if (selectedVersion) {
    return (
      <div className="border border-[var(--color-border-default)] rounded-lg overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-bg-elevated)] border-b border-[var(--color-border-default)]">
          <button
            onClick={() => { setSelectedVersion(null); setShowDiff(false); }}
            className="flex items-center gap-1.5 text-xs text-[var(--color-accent-primary)] hover:underline"
          >
            <ArrowLeft className="h-3 w-3" />
            Back to versions
          </button>
          <div className="flex items-center gap-2">
            <span className="text-xs text-[var(--color-text-tertiary)]">
              v{selectedVersion.version}
            </span>
            <button
              onClick={() => setShowDiff((d) => !d)}
              className={cn(
                "flex items-center gap-1 text-xs px-2 py-0.5 rounded border transition-colors",
                showDiff
                  ? "bg-[var(--color-accent-primary)]/10 border-[var(--color-accent-primary)]/30 text-[var(--color-accent-primary)]"
                  : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
              )}
            >
              <Diff className="h-3 w-3" />
              {showDiff ? "Showing diff" : "Show diff"}
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="max-h-[500px] overflow-auto p-4">
          {showDiff ? (
            <DiffView oldContent={selectedVersion.content} newContent={currentContent} />
          ) : (
            <pre className="text-xs font-mono text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">
              {selectedVersion.content}
            </pre>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="border border-[var(--color-border-default)] rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex items-center justify-between w-full px-4 py-2 bg-[var(--color-bg-elevated)] hover:bg-[var(--color-bg-elevated)]/80 transition-colors"
      >
        <div className="flex items-center gap-1.5">
          <Clock className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          <span className="text-xs font-medium text-[var(--color-text-secondary)]">
            Version History
          </span>
          <Badge variant="outline" className="text-[10px] ml-1">
            {versions.length} versions
          </Badge>
        </div>
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
        )}
      </button>

      {expanded && (
        <div className="divide-y divide-[var(--color-border-default)]">
          {/* Current version */}
          <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-accent-primary)]/5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-[var(--color-accent-primary)]">
                v{currentVersion}
              </span>
              <Badge variant="accent" className="text-[10px]">current</Badge>
            </div>
          </div>

          {/* Previous versions */}
          {versions.map((v) => (
            <button
              key={v.id}
              onClick={() => setSelectedVersion(v)}
              className="flex items-center justify-between w-full px-4 py-2 hover:bg-[var(--color-bg-elevated)] transition-colors text-left"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-xs font-mono text-[var(--color-text-secondary)]">
                  v{v.version}
                </span>
                <span className="text-[10px] text-[var(--color-text-tertiary)] font-mono truncate">
                  {v.model_name}
                </span>
              </div>
              <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0 ml-2">
                {formatRelativeTime(v.archived_at)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function DiffView({ oldContent, newContent }: { oldContent: string; newContent: string }) {
  const oldLines = oldContent.split("\n");
  const newLines = newContent.split("\n");

  // Simple line-by-line diff (LCS-based)
  const diff = computeLineDiff(oldLines, newLines);

  return (
    <div className="text-xs font-mono space-y-0">
      {diff.map((line, i) => (
        <div
          key={i}
          className={cn(
            "px-2 py-0.5 whitespace-pre-wrap break-all",
            line.type === "added" && "bg-green-500/10 text-green-400",
            line.type === "removed" && "bg-red-500/10 text-red-400",
            line.type === "unchanged" && "text-[var(--color-text-tertiary)]",
          )}
        >
          <span className="inline-block w-4 text-right mr-2 select-none opacity-50">
            {line.type === "added" ? "+" : line.type === "removed" ? "-" : " "}
          </span>
          {line.text}
        </div>
      ))}
    </div>
  );
}

interface DiffLine {
  type: "added" | "removed" | "unchanged";
  text: string;
}

function computeLineDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  // Build LCS table
  const m = oldLines.length;
  const n = newLines.length;

  // For very large files, fall back to a simpler approach
  if (m * n > 500_000) {
    return simpleDiff(oldLines, newLines);
  }

  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    Array(n + 1).fill(0),
  );

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack to produce diff
  const result: DiffLine[] = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      result.unshift({ type: "unchanged", text: oldLines[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.unshift({ type: "added", text: newLines[j - 1] });
      j--;
    } else {
      result.unshift({ type: "removed", text: oldLines[i - 1] });
      i--;
    }
  }

  return result;
}

function simpleDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  const result: DiffLine[] = [];
  for (const line of oldLines) {
    result.push({ type: "removed", text: line });
  }
  for (const line of newLines) {
    result.push({ type: "added", text: line });
  }
  return result;
}
