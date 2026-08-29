"use client";

import { Component, memo, useCallback, useEffect, useMemo, useState, type ErrorInfo, type ReactNode } from "react";
import { Copy, Download, ExternalLink, GitCompareArrows, MessageSquarePlus, Pin, PinOff } from "lucide-react";
import { cn } from "../lib/cn";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { Markdown } from "../shared/markdown";
import { getArtifactSourceTarget } from "./artifact-source";
import {
  CallPathRenderer, ContextRenderer, DeadCodeRenderer, DecisionsRenderer,
  DependencyPathRenderer, DiagramRenderer, GenericJsonRenderer, GraphPathRenderer,
  HealthRenderer, OverviewRenderer, RiskReportRenderer, SearchResultsRenderer, SourceRenderer,
} from "./artifacts";
import type {
  ChatArtifact, ContextArtifactData, DeadCodeArtifactData, DecisionsArtifactData,
  DiagramArtifactData, GraphPathArtifactData, OverviewArtifactData,
  RiskReportArtifactData, SearchResultsArtifactData,
} from "@repowise-dev/types/chat";

interface ArtifactPanelProps {
  artifacts: ChatArtifact[];
  activeArtifactId?: string | null;
  compareArtifactId?: string | null;
  open: boolean;
  onClose: () => void;
  onSelect?: (artifactId: string) => void;
  onCompare?: (artifactId: string | null) => void;
  onPin?: (artifact: ChatArtifact, pinned: boolean) => void | Promise<void>;
  onOpenSource?: (artifact: ChatArtifact) => void;
  onFollowUp?: (text: string) => void;
}

export function ArtifactPanel({ artifacts, activeArtifactId, compareArtifactId, open, onClose, onSelect, onCompare, onPin, onOpenSource, onFollowUp }: ArtifactPanelProps) {
  const [pinOverrides, setPinOverrides] = useState<Record<string, boolean>>({});
  const [rawOpen, setRawOpen] = useState(false);
  const withPin = useCallback((artifact: ChatArtifact | undefined) => artifact ? { ...artifact, pinned: pinOverrides[artifact.id] ?? artifact.pinned } as ChatArtifact : undefined, [pinOverrides]);
  const active = useMemo(() => withPin(artifacts.find((item) => item.id === activeArtifactId) ?? artifacts[0]), [activeArtifactId, artifacts, withPin]);
  const comparison = useMemo(() => withPin(artifacts.find((item) => item.id === compareArtifactId && item.id !== active?.id)), [active?.id, artifacts, compareArtifactId, withPin]);
  useEffect(() => setRawOpen(false), [active?.id, open]);
  const pinArtifact = useCallback(async (artifact: ChatArtifact, pinned: boolean) => {
    const previous = pinOverrides[artifact.id] ?? Boolean(artifact.pinned);
    setPinOverrides((current) => ({ ...current, [artifact.id]: pinned }));
    try { await onPin?.(artifact, pinned); }
    catch (error) { setPinOverrides((current) => ({ ...current, [artifact.id]: previous })); throw error; }
  }, [onPin, pinOverrides]);
  if (!active) return null;

  return (
    <AdaptivePanel open={open} onOpenChange={(next) => { if (!next) { setRawOpen(false); onClose(); } }} title="Artifact workspace" widthClassName="md:max-w-[720px] xl:max-w-[880px]" modal={false}>
      {artifacts.length > 1 && (
        <div className="flex shrink-0 overflow-x-auto border-b border-[var(--color-border-default)] px-2" aria-label="Research artifacts">
          {artifacts.map((artifact) => (
            <button key={artifact.id} type="button" aria-pressed={artifact.id === active.id} onClick={() => onSelect?.(artifact.id)} className={cn("border-b-2 px-3 py-2 text-xs whitespace-nowrap transition-colors motion-reduce:transition-none", artifact.id === active.id ? "border-[var(--color-border-hover)] text-[var(--color-text-primary)]" : "border-transparent text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]")}>
              {artifact.title || artifact.type}
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
        <ArtifactHeader key={active.id} artifact={active} comparing={Boolean(comparison)} {...(onCompare && artifacts.length > 1 ? { onCompare } : {})} {...(onPin ? { onPin: pinArtifact } : {})} {...(onOpenSource && getArtifactSourceTarget(active) ? { onOpenSource } : {})} {...(onFollowUp ? { onFollowUp } : {})} />
        <div className={cn("mt-5 grid gap-6", comparison && "lg:grid-cols-2")}>
          <section aria-label="Primary artifact" className="min-w-0">
            <EvidenceQualifiers artifact={active} />
            <ArtifactRenderBoundary artifactId={active.id}><ArtifactRenderer artifact={active} /></ArtifactRenderBoundary>
          </section>
          {comparison && (
            <section aria-label="Comparison artifact" className="min-w-0 border-t border-[var(--color-border-default)] pt-5 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
              <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">Compared with {comparison.title || comparison.type}</p>
              <EvidenceQualifiers artifact={comparison} />
              <ArtifactRenderBoundary artifactId={comparison.id}><ArtifactRenderer artifact={comparison} /></ArtifactRenderBoundary>
            </section>
          )}
        </div>
        <details open={rawOpen} className="mt-6 border-t border-[var(--color-border-default)] pt-3" onToggle={(event) => setRawOpen(event.currentTarget.open)}>
          <summary className="cursor-pointer text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">Inspect raw result</summary>
          {rawOpen && <div className="mt-3"><GenericJsonRenderer data={active.data as unknown as Record<string, unknown>} /></div>}
        </details>
      </div>
    </AdaptivePanel>
  );
}

function ArtifactHeader({ artifact, comparing, onCompare, onPin, onOpenSource, onFollowUp }: { artifact: ChatArtifact; comparing: boolean; onCompare?: (artifactId: string | null) => void; onPin?: (artifact: ChatArtifact, pinned: boolean) => void | Promise<void>; onOpenSource?: (artifact: ChatArtifact) => void; onFollowUp?: (text: string) => void }) {
  const [status, setStatus] = useState("");
  const copy = useCallback(async () => { await navigator.clipboard.writeText(JSON.stringify(artifact.data, null, 2)); setStatus("Artifact copied."); }, [artifact.data]);
  const exportArtifact = useCallback(() => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(artifact, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${artifact.tool_name}-${artifact.id}.json`;
    link.click();
    URL.revokeObjectURL(url);
    setStatus("Artifact exported.");
  }, [artifact]);
  const actionClass = "inline-flex h-8 items-center gap-1.5 px-2 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]";
  return (
    <header className="border-b border-[var(--color-border-default)] pb-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><h2 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{artifact.title || artifact.type}</h2><p className="mt-1 font-mono text-[10px] text-[var(--color-text-tertiary)]">{artifact.tool_name} · {artifact.id}</p></div>
        {artifact.pinned && <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">Pinned</span>}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-1">
        {onPin && <button type="button" aria-label={artifact.pinned ? "Unpin artifact" : "Pin artifact"} className={actionClass} onClick={() => { const next = !artifact.pinned; void Promise.resolve(onPin(artifact, next)).catch(() => setStatus("Artifact pin update failed.")); }}>{artifact.pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}<span>{artifact.pinned ? "Unpin" : "Pin"}</span></button>}
        {onCompare && <button type="button" aria-label="Compare artifact" className={actionClass} onClick={() => onCompare(comparing ? null : artifact.id)}><GitCompareArrows className="h-3.5 w-3.5" /><span>Compare</span></button>}
        <button type="button" aria-label="Copy artifact" className={actionClass} onClick={() => void copy()}><Copy className="h-3.5 w-3.5" /><span>Copy</span></button>
        <button type="button" aria-label="Export artifact" className={actionClass} onClick={exportArtifact}><Download className="h-3.5 w-3.5" /><span>Export</span></button>
        {onOpenSource && <button type="button" aria-label="Open artifact source" className={actionClass} onClick={() => onOpenSource(artifact)}><ExternalLink className="h-3.5 w-3.5" /><span>Source</span></button>}
        {onFollowUp && <button type="button" aria-label="Follow up on artifact" className={actionClass} onClick={() => onFollowUp(`Follow up on ${artifact.title || artifact.type}: `)}><MessageSquarePlus className="h-3.5 w-3.5" /><span>Follow up</span></button>}
      </div>
      <p role="status" aria-live="polite" className="sr-only">{status}</p>
    </header>
  );
}

function EvidenceQualifiers({ artifact }: { artifact: ChatArtifact }) {
  const evidence = artifact.evidence;
  const basis = evidence?.basis ?? "unknown";
  const labels = [basis.charAt(0).toUpperCase() + basis.slice(1)];
  if (evidence?.coverage !== undefined) {
    const coverage = evidence.coverage;
    const available = typeof coverage === "boolean" ? coverage : coverage && typeof coverage === "object" ? (coverage as Record<string, unknown>).available : undefined;
    labels.push(available === true ? "Coverage available" : available === false ? "Coverage unavailable" : "Coverage unknown");
  }
  if (evidence?.confidence !== undefined) labels.push(typeof evidence.confidence === "number" ? `${Math.round(evidence.confidence * (evidence.confidence <= 1 ? 100 : 1))}% confidence` : `${evidence.confidence} confidence`);
  const limits = evidence?.limits;
  if (limits) {
    const emitted = limits.emitted ?? limits.shown;
    const total = limits.total;
    const cap = limits.cap ?? limits.limit;
    if (emitted !== undefined && total !== undefined) labels.push(`${String(emitted)} of ${String(total)} shown`);
    else if (cap !== undefined) labels.push(`Cap ${String(cap)}`);
    const collections = Array.isArray(limits.collections) ? limits.collections : [];
    for (const item of collections.slice(0, 4)) {
      if (!item || typeof item !== "object") continue;
      const row = item as Record<string, unknown>;
      if (row.name && row.emitted !== undefined && row.total !== undefined) labels.push(`${String(row.name)}: ${String(row.emitted)} of ${String(row.total)} shown`);
    }
  }
  if (evidence?.truncated) labels.push("Truncated");
  return <div className="mb-4" aria-label="Evidence qualifications"><div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]">{labels.map((label) => <span key={label}>{label}</span>)}</div>{evidence?.stale && <p className="mt-2 text-xs text-[var(--color-warning)]">{evidence.stale}</p>}</div>;
}

const ArtifactRenderer = memo(function ArtifactRenderer({ artifact }: { artifact: ChatArtifact }) {
  const data = artifact.data;
  if (!data || typeof data !== "object" || Array.isArray(data)) return <p role="alert" className="text-xs text-[var(--color-error)]">This artifact is malformed and cannot be rendered safely.</p>;
  const record = data as unknown as Record<string, unknown>;
  if (typeof record.error === "string") return <p role="alert" className="text-xs text-[var(--color-error)]">{record.error}</p>;
  switch (artifact.type) {
    case "overview": return <OverviewRenderer data={data as unknown as OverviewArtifactData} />;
    case "context": return <ContextRenderer data={data as unknown as ContextArtifactData} />;
    case "source": return <SourceRenderer data={record} />;
    case "risk": case "change_risk": case "risk_report": return <RiskReportRenderer data={data as unknown as RiskReportArtifactData} />;
    case "health": return <HealthRenderer data={record} />;
    case "search_results": return <SearchResultsRenderer data={data as unknown as SearchResultsArtifactData} />;
    case "dependency_path": return <DependencyPathRenderer data={record} />;
    case "call_path": return <CallPathRenderer data={record} />;
    case "graph": return <GraphPathRenderer data={data as unknown as GraphPathArtifactData} />;
    case "decisions": return <DecisionsRenderer data={data as unknown as DecisionsArtifactData} />;
    case "dead_code": return <DeadCodeRenderer data={data as unknown as DeadCodeArtifactData} />;
    case "diagram": return <DiagramRenderer data={data as unknown as DiagramArtifactData} />;
    case "wiki_page": { const content = (record.content_md as string) ?? (record.content as string) ?? ""; return content ? <Markdown content={content} density="compact" /> : <ArtifactUnavailable />; }
    default: return <ArtifactUnavailable />;
  }
});

function ArtifactUnavailable() { return <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">A focused view is not available for this result. Use “Inspect raw result” below to examine its structured data.</p>; }

class ArtifactRenderBoundary extends Component<{ artifactId: string; children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  override componentDidCatch(_error: Error, _info: ErrorInfo) {}
  override componentDidUpdate(previous: { artifactId: string }) { if (previous.artifactId !== this.props.artifactId && this.state.failed) this.setState({ failed: false }); }
  override render() { return this.state.failed ? <p role="alert" className="text-xs text-[var(--color-error)]">This artifact is incomplete or malformed and cannot be rendered safely.</p> : this.props.children; }
}
