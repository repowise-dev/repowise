"use client";

import { useState } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { Radar, Plus, Flame } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils/cn";
import {
  analyzeBlastRadius,
  type BlastRadiusResponse,
  type DirectRiskEntry,
  type TransitiveEntry,
  type CochangeWarning,
  type ReviewerEntry,
} from "@/lib/api/blast-radius";
import { getHotspots } from "@/lib/api/git";

// ---------------------------------------------------------------------------
// Risk score gauge card
// ---------------------------------------------------------------------------

function RiskScoreCard({ score }: { score: number }) {
  const color =
    score >= 7
      ? "text-red-500 border-red-500/30 bg-red-500/5"
      : score >= 4
        ? "text-amber-500 border-amber-500/30 bg-amber-500/5"
        : "text-emerald-500 border-emerald-500/30 bg-emerald-500/5";
  const label = score >= 7 ? "High Risk" : score >= 4 ? "Medium Risk" : "Low Risk";

  return (
    <Card className={cn("border", color)}>
      <CardContent className="flex flex-col items-center justify-center py-8 gap-2">
        <span className={cn("text-6xl font-bold tabular-nums", color.split(" ")[0])}>
          {score.toFixed(1)}
        </span>
        <span className={cn("text-sm font-medium", color.split(" ")[0])}>{label}</span>
        <span className="text-xs text-[var(--color-text-tertiary)]">Overall Risk Score (0–10)</span>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Simple table helpers
// ---------------------------------------------------------------------------

function TableSection({
  title,
  children,
  empty,
}: {
  title: string;
  children: React.ReactNode;
  empty: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {empty ? (
          <p className="text-xs text-[var(--color-text-tertiary)] py-2">None</p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="text-left text-xs font-medium text-[var(--color-text-tertiary)] py-1.5 pr-4 whitespace-nowrap">
      {children}
    </th>
  );
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <td
      className={cn(
        "text-xs text-[var(--color-text-secondary)] py-1.5 pr-4 align-top",
        className,
      )}
    >
      {children}
    </td>
  );
}

// ---------------------------------------------------------------------------
// Result tables
// ---------------------------------------------------------------------------

function DirectRisksTable({ rows }: { rows: DirectRiskEntry[] }) {
  // Risk scores from the API are 0–1; render as 0–10 to match the gauge.
  const fmt10 = (v: number) => (v * 10).toFixed(2);
  // Centrality is small; render as percentage with 2 decimals.
  const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`;
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Direct risks for the changed files</caption>
        <thead>
          <tr>
            <Th>File</Th>
            <Th>Risk Score</Th>
            <Th>Temporal Hotspot</Th>
            <Th>Centrality</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.path} className="border-t border-[var(--color-border-default)]">
              <Td>
                <span className="font-mono break-all" title={r.path}>{r.path}</span>
              </Td>
              <Td className="text-right tabular-nums">{fmt10(r.risk_score)}</Td>
              <Td className="text-right tabular-nums">{fmt10(r.temporal_hotspot)}</Td>
              <Td className="text-right tabular-nums">{fmtPct(r.centrality)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TransitiveTable({ rows }: { rows: TransitiveEntry[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Transitively affected files</caption>
        <thead>
          <tr>
            <Th>File</Th>
            <Th>Depth</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.path} className="border-t border-[var(--color-border-default)]">
              <Td>
                <span className="font-mono break-all" title={r.path}>{r.path}</span>
              </Td>
              <Td className="text-right tabular-nums">{r.depth}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CochangeTable({ rows }: { rows: CochangeWarning[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Co-change warnings</caption>
        <thead>
          <tr>
            <Th>Changed File</Th>
            <Th>Missing Partner</Th>
            <Th>Co-change Count</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={`${r.changed}|${r.missing_partner}|${i}`}
              className="border-t border-[var(--color-border-default)]"
            >
              <Td>
                <span className="font-mono break-all" title={r.changed}>{r.changed}</span>
              </Td>
              <Td>
                <span className="font-mono break-all" title={r.missing_partner}>{r.missing_partner}</span>
              </Td>
              <Td className="text-right tabular-nums">{r.score}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewersTable({ rows }: { rows: ReviewerEntry[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <caption className="sr-only">Recommended reviewers</caption>
        <thead>
          <tr>
            <Th>Email</Th>
            <Th>Files Owned</Th>
            <Th>Avg Ownership %</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.email} className="border-t border-[var(--color-border-default)]">
              <Td>{r.email}</Td>
              <Td className="text-right tabular-nums">{r.files}</Td>
              <Td className="text-right tabular-nums">{(r.ownership_pct * 100).toFixed(1)}%</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TestGapsList({ gaps }: { gaps: string[] }) {
  return (
    <ul className="space-y-1">
      {gaps.map((g) => (
        <li key={g} className="text-xs font-mono text-[var(--color-text-secondary)] break-all">
          {g}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

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
          {error && (
            <p className="text-xs text-red-500">{error}</p>
          )}
        </CardContent>
      </Card>

      {/* Results */}
      {result && (
        <div className="space-y-4">
          {/* Risk gauge + stats row */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
            <RiskScoreCard score={result.overall_risk_score} />
            <Card className="sm:col-span-3">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm">Summary</CardTitle>
              </CardHeader>
              <CardContent className="pt-0 grid grid-cols-2 sm:grid-cols-4 gap-4">
                {[
                  { label: "Direct Risks", value: result.direct_risks.length },
                  { label: "Transitive Files", value: result.transitive_affected.length },
                  { label: "Co-change Warnings", value: result.cochange_warnings.length },
                  { label: "Test Gaps", value: result.test_gaps.length },
                ].map(({ label, value }) => (
                  <div key={label} className="space-y-1">
                    <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
                      {value}
                    </p>
                    <p className="text-xs text-[var(--color-text-tertiary)]">{label}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <TableSection title="Direct Risks" empty={result.direct_risks.length === 0}>
            <DirectRisksTable rows={result.direct_risks} />
          </TableSection>

          <TableSection
            title="Transitive Affected Files"
            empty={result.transitive_affected.length === 0}
          >
            <TransitiveTable rows={result.transitive_affected} />
          </TableSection>

          <TableSection
            title="Co-change Warnings"
            empty={result.cochange_warnings.length === 0}
          >
            <CochangeTable rows={result.cochange_warnings} />
          </TableSection>

          <TableSection
            title="Recommended Reviewers"
            empty={result.recommended_reviewers.length === 0}
          >
            <ReviewersTable rows={result.recommended_reviewers} />
          </TableSection>

          <TableSection title="Test Gaps" empty={result.test_gaps.length === 0}>
            <TestGapsList gaps={result.test_gaps} />
          </TableSection>
        </div>
      )}
    </div>
  );
}
