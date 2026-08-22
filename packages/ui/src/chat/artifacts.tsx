"use client";

/**
 * Per-type chat artifact renderers. Dispatched by `ArtifactPanel` based on
 * `artifact.type`. Variant shapes are declared in
 * `@repowise-dev/types/chat`. Each renderer is intentionally small and
 * presentation-only — heavier visualisations (full graph canvases, tables,
 * etc.) belong in their own subpackages and are linked to from here.
 */

import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Box,
  CheckCircle2,
  FileCode,
  GitBranch,
  Layers,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import type {
  ContextArtifactData,
  DeadCodeArtifactData,
  DecisionsArtifactData,
  DiagramArtifactData,
  GraphPathArtifactData,
  OverviewArtifactData,
  RiskReportArtifactData,
  SearchResultsArtifactData,
} from "@repowise-dev/types/chat";
import { Markdown } from "../shared/markdown";
import { MermaidDiagram } from "../wiki/mermaid-diagram";
import { getPageTypeLabel } from "../lib/page-types";

// ---------------------------------------------------------------------------
// Shared atoms
// ---------------------------------------------------------------------------

function StatRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-[var(--color-text-tertiary)]">{label}</span>
      <span className="font-mono tabular-nums text-[var(--color-text-secondary)]">
        {value}
      </span>
    </div>
  );
}

function SectionTitle({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] mb-2">
      <Icon className="h-3 w-3" />
      <span>{children}</span>
    </div>
  );
}

function FilePathChip({ path }: { path: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[10px] font-mono text-[var(--color-text-secondary)]">
      <FileCode className="h-2.5 w-2.5 opacity-60" />
      <span className="truncate max-w-[200px]" title={path}>
        {path}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

export function OverviewRenderer({ data }: { data: OverviewArtifactData }) {
  const langs = Object.entries(data.languages ?? {})
    .sort((a, b) => (b[1] as number) - (a[1] as number))
    .slice(0, 6);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <StatRow label="Files" value={data.total_files.toLocaleString()} />
        <StatRow label="Symbols" value={data.total_symbols.toLocaleString()} />
        <StatRow label="Hotspots" value={data.hotspot_count.toLocaleString()} />
        <StatRow label="Monorepo" value={data.is_monorepo ? "yes" : "no"} />
      </div>

      {langs.length > 0 && (
        <div>
          <SectionTitle icon={Layers}>Languages</SectionTitle>
          <div className="space-y-1">
            {langs.map(([lang, count]) => (
              <StatRow
                key={lang}
                label={lang}
                value={(count as number).toLocaleString()}
              />
            ))}
          </div>
        </div>
      )}

      {data.modules.length > 0 && (
        <div>
          <SectionTitle icon={Box}>Modules</SectionTitle>
          <div className="flex flex-wrap gap-1">
            {data.modules.slice(0, 12).map((m) => (
              <span
                key={m}
                className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[10px] font-mono text-[var(--color-text-secondary)]"
              >
                {m}
              </span>
            ))}
          </div>
        </div>
      )}

      {data.entry_points.length > 0 && (
        <div>
          <SectionTitle icon={ArrowRight}>Entry points</SectionTitle>
          <div className="space-y-1">
            {data.entry_points.slice(0, 5).map((ep) => (
              <FilePathChip key={ep} path={ep} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

export function ContextRenderer({ data }: { data: ContextArtifactData }) {
  const targets = Object.entries(data.targets ?? {});
  if (targets.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No context loaded.</p>
    );
  }
  return (
    <div className="space-y-5">
      {targets.map(([target, info]) => {
        const md = info.docs?.content_md ?? "";
        return (
          <div key={target}>
            <h3 className="text-xs font-mono text-[var(--color-accent-primary)] mb-2 break-all">
              {target}
            </h3>
            {md ? (
              <Markdown content={md} />
            ) : (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                No documentation available.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Risk report
// ---------------------------------------------------------------------------

type NormalizedRiskTarget = {
  file_path: string;
  /** 0–1 fraction from MCP `hotspot_score` / `churn_percentile`. */
  score: number | null;
  is_hotspot: boolean;
  risk_type?: string;
  trend?: string;
  risk_summary?: string;
};

/** Same top-quartile cut `git_indexer/enrich.py` uses for `is_hotspot`. */
const HOTSPOT_SCORE_THRESHOLD = 0.75;

function formatHotspotPct(score: number): string {
  return `${Math.round(score * 100)}th`;
}

function normalizeRiskTargets(data: RiskReportArtifactData): NormalizedRiskTarget[] {
  const raw = data.targets;
  if (!raw) return [];

  const rows: Array<{
    file_path: string;
    hotspot_score?: number;
    churn_percentile?: number;
    is_hotspot?: boolean;
    risk_type?: string;
    trend?: string;
    risk_summary?: string;
  }> = Array.isArray(raw)
    ? raw.map((t) => ({
        ...t,
        file_path: t.file_path ?? t.target ?? "",
      }))
    : Object.entries(raw).map(([key, value]) => ({
        ...value,
        file_path: value.file_path ?? value.target ?? key,
      }));

  return rows
    .filter((t) => t.file_path.length > 0)
    .map((t) => {
      const score =
        typeof t.hotspot_score === "number"
          ? t.hotspot_score
          : typeof t.churn_percentile === "number"
            ? t.churn_percentile
            : null;
      const out: NormalizedRiskTarget = {
        file_path: t.file_path,
        score,
        // MCP target rows omit `is_hotspot`; derive from the enrich.py cut.
        is_hotspot:
          Boolean(t.is_hotspot) ||
          (typeof score === "number" && score >= HOTSPOT_SCORE_THRESHOLD),
      };
      if (typeof t.risk_type === "string") out.risk_type = t.risk_type;
      if (typeof t.trend === "string") out.trend = t.trend;
      if (typeof t.risk_summary === "string") out.risk_summary = t.risk_summary;
      return out;
    });
}

function normalizeGlobalHotspots(data: RiskReportArtifactData): Array<{
  path: string;
  score: number;
}> {
  return (data.global_hotspots ?? [])
    .map((h) => {
      const path = h.file_path ?? h.path ?? "";
      const score =
        typeof h.hotspot_score === "number"
          ? h.hotspot_score
          : typeof h.churn_percentile === "number"
            ? h.churn_percentile
            : null;
      if (!path || score === null) return null;
      return { path, score };
    })
    .filter((h): h is { path: string; score: number } => h !== null);
}

export function RiskReportRenderer({ data }: { data: RiskReportArtifactData }) {
  // get_change_risk returns a live-git score card, not per-file targets.
  if (data.error || data.ref || data.review_priority || data.risk_percentile != null) {
    const pct =
      typeof data.risk_percentile === "number"
        ? Math.round(data.risk_percentile)
        : null;
    return (
      <div className="space-y-3">
        {data.error && (
          <p className="text-xs text-[var(--color-warning)]">{String(data.error)}</p>
        )}
        {data.ref && (
          <StatRow label="Change" value={String(data.ref)} />
        )}
        {data.review_priority && (
          <StatRow label="Review priority" value={String(data.review_priority)} />
        )}
        {pct !== null && <StatRow label="Risk percentile" value={`p${pct}`} />}
        {typeof data.score === "number" && (
          <StatRow label="Score" value={data.score.toFixed(1)} />
        )}
        {data.classification && (
          <p className="text-xs text-[var(--color-text-secondary)]">
            {String(data.classification)}
          </p>
        )}
        {data.warning && (
          <p className="text-xs text-[var(--color-text-tertiary)]">{String(data.warning)}</p>
        )}
      </div>
    );
  }

  const targets = normalizeRiskTargets(data);
  const hotspots = normalizeGlobalHotspots(data);

  if (targets.length === 0 && hotspots.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No risk data.</p>
    );
  }

  return (
    <div className="space-y-4">
      {targets.length > 0 && (
        <div>
          <SectionTitle icon={ShieldAlert}>Targets</SectionTitle>
          <div className="space-y-1.5">
            {targets.map((t, i) => (
              <div
                key={`${t.file_path}:${i}`}
                className="rounded-lg border border-[var(--color-border-default)] p-2.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-[var(--color-text-primary)] truncate">
                    {t.file_path}
                  </span>
                  {t.is_hotspot && (
                    <span className="inline-flex items-center gap-1 rounded bg-[var(--color-warning)]/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-warning)]">
                      <AlertTriangle className="h-2.5 w-2.5" /> hotspot
                    </span>
                  )}
                </div>
                {typeof t.score === "number" && (
                  <div className="text-[10px] text-[var(--color-text-tertiary)] mt-0.5 tabular-nums">
                    Hotspot {formatHotspotPct(t.score)} pct
                    {t.risk_type ? ` · ${t.risk_type}` : ""}
                    {t.trend ? ` · ${t.trend}` : ""}
                  </div>
                )}
                {t.risk_summary && (
                  <p className="text-[10px] text-[var(--color-text-secondary)] mt-1 line-clamp-2">
                    {t.risk_summary}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {hotspots.length > 0 && (
        <div>
          <SectionTitle icon={Activity}>Top global hotspots</SectionTitle>
          <div className="space-y-1">
            {hotspots.slice(0, 5).map((h, i) => (
              <div
                key={`${h.path}:${i}`}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span className="font-mono text-[var(--color-text-secondary)] truncate">
                  {h.path}
                </span>
                <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                  {formatHotspotPct(h.score)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Search results
// ---------------------------------------------------------------------------

export function SearchResultsRenderer({
  data,
}: {
  data: SearchResultsArtifactData;
}) {
  if (!data.results || data.results.length === 0) {
    return (
      <div className="space-y-2">
        <SectionTitle icon={Search}>Query</SectionTitle>
        <p className="text-xs text-[var(--color-text-secondary)] font-mono">
          {data.query}
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          No results found.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <SectionTitle icon={Search}>{data.query}</SectionTitle>
      {data.results.map((r, i) => (
        <div
          key={`${r.page_id ?? r.title}:${i}`}
          className="rounded-lg border border-[var(--color-border-default)] p-3"
        >
          <div className="text-xs font-medium text-[var(--color-text-primary)]">
            {r.title}
          </div>
          <div className="text-[10px] text-[var(--color-text-tertiary)] mt-0.5">
            {getPageTypeLabel(r.page_type)}
            {typeof r.relevance_score === "number" && (
              <> · score {(r.relevance_score as number).toFixed(2)}</>
            )}
          </div>
          {r.snippet && (
            <p className="text-xs text-[var(--color-text-secondary)] mt-1 line-clamp-3">
              {r.snippet}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Graph (dependency path)
// ---------------------------------------------------------------------------

export function GraphPathRenderer({ data }: { data: GraphPathArtifactData }) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--color-text-secondary)]">
        {data.explanation}
      </p>
      {data.path.length > 0 ? (
        <div>
          <SectionTitle icon={GitBranch}>Path</SectionTitle>
          <ol className="space-y-1">
            {data.path.map((node, i) => (
              <li
                key={`${node}:${i}`}
                className="flex items-center gap-2 text-xs"
              >
                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] text-[10px] font-mono tabular-nums text-[var(--color-text-tertiary)]">
                  {i + 1}
                </span>
                <span className="font-mono text-[var(--color-text-secondary)] truncate">
                  {node}
                </span>
                {i < data.path.length - 1 && (
                  <ArrowRight className="h-3 w-3 text-[var(--color-text-tertiary)] ml-auto shrink-0" />
                )}
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          No path found.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Decisions
// ---------------------------------------------------------------------------

function decisionRows(
  data: DecisionsArtifactData,
): Array<{
  title: string;
  status?: string;
  decision?: string;
  rationale?: string;
  affected_files?: string[];
}> {
  // MCP search/path emit `decisions`; older chat fixtures used `results`.
  return (data.decisions ?? data.results ?? []) as Array<{
    title: string;
    status?: string;
    decision?: string;
    rationale?: string;
    affected_files?: string[];
  }>;
}

function DecisionCards({
  rows,
}: {
  rows: Array<{
    title: string;
    status?: string;
    decision?: string;
    rationale?: string;
    affected_files?: string[];
  }>;
}) {
  return (
    <div className="space-y-3">
      {rows.map((r, i) => (
        <div
          key={`${r.title}:${i}`}
          className="rounded-lg border border-[var(--color-border-default)] p-3 space-y-1.5"
        >
          <h3 className="text-xs font-medium text-[var(--color-text-primary)]">
            {r.title}
            {r.status && (
              <span className="ml-1.5 text-[10px] font-normal text-[var(--color-text-tertiary)]">
                ({r.status})
              </span>
            )}
          </h3>
          {r.decision && (
            <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
              {r.decision}
            </p>
          )}
          {r.rationale && (
            <p className="text-xs text-[var(--color-text-tertiary)] leading-relaxed">
              {r.rationale}
            </p>
          )}
          {r.affected_files && r.affected_files.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1">
              {r.affected_files.slice(0, 6).map((f) => (
                <FilePathChip key={f} path={f} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function DecisionsRenderer({ data }: { data: DecisionsArtifactData }) {
  if (data.mode === "health") {
    const counts = data.counts ?? {};
    const active =
      counts.active ??
      counts.total ??
      data.total_decisions ??
      0;
    const stale = data.stale_decisions ?? [];
    const proposed = data.proposed_awaiting_review ?? [];
    const hotspots = data.ungoverned_hotspots ?? [];
    const legacyRecent = data.decisions ?? [];

    return (
      <div className="space-y-4">
        {data.summary && (
          <p className="text-xs text-[var(--color-text-secondary)]">{data.summary}</p>
        )}
        <div className="grid grid-cols-2 gap-2">
          <StatRow label="Active" value={Number(active).toLocaleString()} />
          <StatRow
            label="Stale"
            value={Number(counts.stale ?? stale.length).toLocaleString()}
          />
          <StatRow
            label="Proposed"
            value={Number(counts.proposed ?? proposed.length).toLocaleString()}
          />
          <StatRow
            label="Ungoverned"
            value={hotspots.length.toLocaleString()}
          />
        </div>
        {data.by_source && Object.keys(data.by_source).length > 0 && (
          <div>
            <SectionTitle icon={Layers}>By source</SectionTitle>
            <div className="space-y-1">
              {Object.entries(data.by_source).map(([source, count]) => (
                <StatRow
                  key={source}
                  label={source}
                  value={(count as number).toLocaleString()}
                />
              ))}
            </div>
          </div>
        )}
        {stale.length > 0 && (
          <div>
            <SectionTitle icon={AlertTriangle}>Stale decisions</SectionTitle>
            <ul className="space-y-1">
              {stale.slice(0, 8).map((d, i) => (
                <li key={i} className="text-xs text-[var(--color-text-secondary)]">
                  <span className="truncate">{d.title}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {proposed.length > 0 && (
          <div>
            <SectionTitle icon={Sparkles}>Proposed</SectionTitle>
            <ul className="space-y-1">
              {proposed.slice(0, 8).map((d, i) => (
                <li key={i} className="text-xs text-[var(--color-text-secondary)]">
                  <span className="truncate">{d.title}</span>
                  {d.source && (
                    <span className="ml-1.5 text-[10px] text-[var(--color-text-tertiary)]">
                      ({d.source})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {legacyRecent.length > 0 && stale.length === 0 && proposed.length === 0 && (
          <div>
            <SectionTitle icon={Sparkles}>Recent</SectionTitle>
            <ul className="space-y-1">
              {legacyRecent.map((d, i) => (
                <li key={i} className="text-xs text-[var(--color-text-secondary)]">
                  <span className="truncate">{d.title}</span>
                  {d.status && (
                    <span className="ml-1.5 text-[10px] text-[var(--color-text-tertiary)]">
                      ({d.status})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  const rows = decisionRows(data);
  const heading =
    data.mode === "path"
      ? data.path ?? data.query
      : data.query;

  if (rows.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        No matching decisions
        {heading ? ` for "${heading}"` : " for this query"}.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {heading && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {data.mode === "path" ? "Governing" : "Matches for"}{" "}
          <span className="font-mono text-[var(--color-text-secondary)]">
            "{heading}"
          </span>
        </p>
      )}
      {data.alignment?.summary && (
        <p className="text-xs text-[var(--color-text-secondary)]">
          {data.alignment.summary}
        </p>
      )}
      <DecisionCards rows={rows} />
      {data.origin_story?.primary_author && (
        <p className="text-[10px] text-[var(--color-text-tertiary)]">
          Primary author: {String(data.origin_story.primary_author)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dead code
// ---------------------------------------------------------------------------

export function DeadCodeRenderer({ data }: { data: DeadCodeArtifactData }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <StatRow
          label="Findings"
          value={data.total_findings.toLocaleString()}
        />
        <StatRow
          label="Candidate lines"
          value={data.deletable_lines.toLocaleString()}
        />
      </div>

      {data.high_confidence.length > 0 && (
        <div>
          <SectionTitle icon={CheckCircle2}>High confidence</SectionTitle>
          <div className="space-y-1.5">
            {data.high_confidence.map((f, i) => (
              <DeadCodeRow key={`${f.file_path}:${i}`} finding={f} showLines />
            ))}
          </div>
        </div>
      )}

      {data.medium_confidence.length > 0 && (
        <div>
          <SectionTitle icon={AlertTriangle}>Medium confidence</SectionTitle>
          <div className="space-y-1.5">
            {data.medium_confidence.map((f, i) => (
              <DeadCodeRow key={`${f.file_path}:${i}`} finding={f} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DeadCodeRow({
  finding,
  showLines = false,
}: {
  finding: {
    file_path: string;
    symbol_name?: string | null;
    kind: string;
    confidence: number;
    reason: string;
    lines?: number;
    safe_to_delete?: boolean;
  };
  showLines?: boolean;
}) {
  return (
    <div className="rounded-md border border-[var(--color-border-default)] p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[var(--color-text-primary)] truncate">
          {finding.symbol_name ? `${finding.file_path}::${finding.symbol_name}` : finding.file_path}
        </span>
        <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)] shrink-0">
          {(finding.confidence * 100).toFixed(0)}%
        </span>
      </div>
      <div className="text-[10px] text-[var(--color-text-tertiary)] mt-0.5">
        {finding.kind}
        {showLines && typeof finding.lines === "number" && (
          <> · {finding.lines} lines</>
        )}
        {finding.safe_to_delete && <> · cleanup-ready</>}
      </div>
      <p className="text-xs text-[var(--color-text-secondary)] mt-1 line-clamp-2">
        {finding.reason}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Diagram (Mermaid)
// ---------------------------------------------------------------------------

export function DiagramRenderer({ data }: { data: DiagramArtifactData }) {
  if (!data.mermaid_syntax) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        No diagram available.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {data.description && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {data.description}
        </p>
      )}
      <MermaidDiagram chart={data.mermaid_syntax} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generic JSON fallback
// ---------------------------------------------------------------------------

export function GenericJsonRenderer({
  data,
}: {
  data: Record<string, unknown>;
}) {
  return (
    <pre className="text-[10px] font-mono text-[var(--color-text-secondary)] overflow-auto max-h-[70vh] whitespace-pre-wrap break-words">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
