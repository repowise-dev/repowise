"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { Radar, Plus, Flame } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { Card, CardHeader, CardTitle, CardContent } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { BlastRadiusResults } from "@repowise-dev/ui/blast-radius";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { analyzeBlastRadius } from "@/lib/api/blast-radius";
import { getHotspots } from "@/lib/api/git";

export default function BlastRadiusPage() {
  const params = useParams<{ id: string }>();
  const repoId = params.id;

  const [files, setFiles] = useState("");
  const [maxDepth, setMaxDepth] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BlastRadiusResponse | null>(null);

  // Suggestions: top 8 hotspots so users can prefill with one click instead
  // of remembering paths. Falls back gracefully if the call fails.
  const { data: hotspotSuggestions, isLoading: hotspotSuggestionsLoading } = useSWR(
    repoId ? ["blast-radius-suggestions", repoId] : null,
    () => getHotspots(repoId, 8),
  );

  const addSuggestion = (path: string) => {
    setFiles((prev) => {
      const lines = prev.split("\n").map((l) => l.trim()).filter(Boolean);
      if (lines.includes(path)) return prev;
      return [...lines, path].join("\n");
    });
  };

  const useAllHotspots = () => {
    if (!hotspotSuggestions) return;
    setFiles(hotspotSuggestions.map((h) => h.file_path).join("\n"));
  };

  const clearFiles = () => setFiles("");

  const handleAnalyze = async () => {
    const changedFiles = files
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

    if (changedFiles.length === 0) {
      setError("Enter at least one file path.");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const data = await analyzeBlastRadius(repoId, {
        changed_files: changedFiles,
        max_depth: maxDepth,
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <Radar className="h-5 w-5 text-violet-500" />
          Blast Radius
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Estimate the impact of a proposed PR — direct risks, transitive effects, reviewer
          suggestions, and test gaps.
        </p>
      </div>

      {/* Input form */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Changed Files</CardTitle>
        </CardHeader>
        <CardContent className="pt-0 space-y-4">
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Paste a list of file paths (one per line) — typically the files in your PR diff.
            Don&apos;t know what to try? Click a hotspot below to prefill, or use{" "}
            <button
              type="button"
              onClick={useAllHotspots}
              className="underline underline-offset-2 hover:text-[var(--color-text-primary)]"
              disabled={!hotspotSuggestions || hotspotSuggestions.length === 0}
            >
              Use top hotspots
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
                  onClick={() => addSuggestion(h.file_path)}
                  className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1 text-[11px] font-mono text-[var(--color-text-secondary)] hover:border-[var(--color-accent-primary)] hover:text-[var(--color-text-primary)] transition-colors"
                  title={h.file_path}
                  aria-label={`Add ${h.file_path} to changed files`}
                >
                  <Flame className="h-3 w-3 text-orange-500" />
                  <span className="truncate max-w-[260px]">{h.file_path}</span>
                  <Plus className="h-3 w-3 opacity-60" />
                </button>
              ))}
            </div>
          )}

          <label htmlFor="blast-radius-changed-files" className="sr-only">
            Changed file paths, one per line
          </label>
          <textarea
            id="blast-radius-changed-files"
            className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2 text-xs font-mono text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] resize-y min-h-[120px]"
            placeholder={"src/auth/login.py\nsrc/models/user.py\n..."}
            value={files}
            onChange={(e) => setFiles(e.target.value)}
            aria-label="Changed file paths, one per line"
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
            {files && (
              <Button onClick={clearFiles} disabled={loading} size="sm" variant="outline">
                Clear
              </Button>
            )}
          </div>
          {error && <p className="text-xs text-red-500">{error}</p>}
        </CardContent>
      </Card>

      {result && <BlastRadiusResults result={result} />}
    </div>
  );
}
