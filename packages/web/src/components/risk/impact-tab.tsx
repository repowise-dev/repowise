"use client";

import { useState } from "react";
import useSWR from "swr";
import { Plus, Flame } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { Card, CardHeader, CardTitle, CardContent } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { FilePathPicker } from "@repowise-dev/ui/health";
import { BlastRadiusResults } from "@repowise-dev/ui/blast-radius";
import { ReviewerSuggestions } from "@repowise-dev/ui/git/reviewer-suggestions";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import type { ReviewerSuggestion } from "@repowise-dev/types/modules";
import { analyzeBlastRadius } from "@/lib/api/blast-radius";
import { getHotspots, getReviewerSuggestions } from "@/lib/api/git";
import { searchNodes } from "@/lib/api/graph";

export function ImpactTab({ repoId }: { repoId: string }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [maxDepth, setMaxDepth] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BlastRadiusResponse | null>(null);
  const [reviewers, setReviewers] = useState<ReviewerSuggestion[] | null>(null);

  const { data: hotspotSuggestions, isLoading: hotspotSuggestionsLoading } = useSWR(
    repoId ? ["blast-radius-suggestions", repoId] : null,
    () => getHotspots(repoId, 8),
  );

  const addPaths = (paths: string[]) => {
    setSelected((prev) => {
      const set = new Set(prev);
      for (const p of paths) {
        const trimmed = p.trim();
        if (trimmed) set.add(trimmed);
      }
      return [...set];
    });
  };
  const removePath = (path: string) =>
    setSelected((prev) => prev.filter((p) => p !== path));
  const useAllHotspots = () => {
    if (!hotspotSuggestions) return;
    addPaths(hotspotSuggestions.map((h) => h.file_path));
  };

  const handleAnalyze = async () => {
    if (selected.length === 0) {
      setError("Add at least one file path.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    setReviewers(null);
    try {
      const [data, suggestions] = await Promise.all([
        analyzeBlastRadius(repoId, { changed_files: selected, max_depth: maxDepth }),
        getReviewerSuggestions(repoId, selected, 8).catch(() => null),
      ]);
      setResult(data);
      setReviewers(suggestions?.suggestions ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Impact Analyzer</CardTitle>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Estimate the blast radius of a proposed change — direct and transitive risks,
            reviewer suggestions, and test gaps.
          </p>
        </CardHeader>
        <CardContent className="pt-0 space-y-4">
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Type to search the file index, paste a list of paths (your PR diff),
            click a hotspot below, or{" "}
            <button
              type="button"
              onClick={useAllHotspots}
              className="underline underline-offset-2 hover:text-[var(--color-text-primary)]"
              disabled={!hotspotSuggestions || hotspotSuggestions.length === 0}
            >
              use top hotspots
            </button>
            .
          </p>

          {hotspotSuggestionsLoading && !hotspotSuggestions && (
            <div className="flex flex-wrap gap-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-7 w-32 rounded-full" />
              ))}
            </div>
          )}

          {hotspotSuggestions && hotspotSuggestions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {hotspotSuggestions.map((h) => (
                <button
                  key={h.file_path}
                  type="button"
                  onClick={() => addPaths([h.file_path])}
                  className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1 text-[11px] font-mono text-[var(--color-text-secondary)] hover:border-[var(--color-accent-primary)] hover:text-[var(--color-text-primary)] transition-colors"
                  title={h.file_path}
                  aria-label={`Add ${h.file_path} to changed files`}
                >
                  <Flame className="h-3 w-3 text-[var(--color-warning)]" />
                  <span className="truncate max-w-[260px]">{h.file_path}</span>
                  <Plus className="h-3 w-3 opacity-60" />
                </button>
              ))}
            </div>
          )}

          <FilePathPicker
            selected={selected}
            onAdd={addPaths}
            onRemove={removePath}
            onSearch={async (q) => {
              const results = await searchNodes(repoId, q, 8);
              return results.map((r) => r.node_id);
            }}
          />

          <div className="flex items-center gap-4 flex-wrap">
            <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
              Max depth
              <input
                type="number"
                min={1}
                max={10}
                value={maxDepth}
                onChange={(e) => setMaxDepth(Math.max(1, Math.min(10, Number(e.target.value))))}
                className="w-16 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)]"
                aria-label="Maximum dependency depth (1–10)"
              />
            </label>
            <Button onClick={handleAnalyze} disabled={loading} size="sm">
              {loading ? "Analyzing…" : "Analyze"}
            </Button>
            {selected.length > 0 && (
              <Button onClick={() => setSelected([])} disabled={loading} size="sm" variant="outline">
                Clear
              </Button>
            )}
          </div>
          {error && <p className="text-xs text-[var(--color-error)]">{error}</p>}
        </CardContent>
      </Card>

      {result && (
        <BlastRadiusResults
          result={result}
          reviewersSlot={
            reviewers && reviewers.length > 0 ? (
              <ReviewerSuggestions
                suggestions={reviewers}
                subtitle={`Based on authorship and co-change history for ${selected.length} changed paths`}
              />
            ) : undefined
          }
        />
      )}
    </div>
  );
}
