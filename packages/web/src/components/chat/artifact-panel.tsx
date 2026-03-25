"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { ChatMarkdown } from "./chat-markdown";

export interface Artifact {
  type: string;
  title: string;
  data: Record<string, unknown>;
}

interface ArtifactPanelProps {
  artifacts: Artifact[];
  open: boolean;
  onClose: () => void;
}

export function ArtifactPanel({ artifacts, open, onClose }: ArtifactPanelProps) {
  const [activeIdx, setActiveIdx] = useState(0);

  if (!open || artifacts.length === 0) return null;

  const active = artifacts[Math.min(activeIdx, artifacts.length - 1)];

  return (
    <>
      {/* Mobile: full-screen overlay */}
      <div className="fixed inset-0 z-[var(--z-modal)] bg-black/50 lg:hidden" onClick={onClose} />

      <div
        className={cn(
          "fixed right-0 top-0 z-[var(--z-modal)] h-full bg-[var(--color-bg-surface)] border-l border-[var(--color-border-default)] flex flex-col",
          "w-full sm:w-[480px] lg:w-[420px]",
          "transition-transform duration-300",
          open ? "translate-x-0" : "translate-x-full",
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border-default)] shrink-0">
          <span className="text-xs font-medium text-[var(--color-text-primary)]">
            Artifacts
          </span>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Tabs */}
        {artifacts.length > 1 && (
          <div className="flex gap-0 border-b border-[var(--color-border-default)] shrink-0 overflow-x-auto px-2">
            {artifacts.map((art, idx) => (
              <button
                key={idx}
                onClick={() => setActiveIdx(idx)}
                className={cn(
                  "px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors",
                  idx === activeIdx
                    ? "border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
                    : "border-transparent text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
                )}
              >
                {art.title || art.type}
              </button>
            ))}
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          <ArtifactRenderer artifact={active} />
        </div>
      </div>
    </>
  );
}

function ArtifactRenderer({ artifact }: { artifact: Artifact }) {
  const { type, data } = artifact;

  switch (type) {
    case "overview":
    case "wiki_page": {
      const content =
        (data as Record<string, unknown>).content_md as string ??
        (data as Record<string, unknown>).content as string ??
        "";
      // For get_context with targets
      const targets = data.targets as Record<string, Record<string, unknown>> | undefined;
      if (targets) {
        return (
          <div className="space-y-4">
            {Object.entries(targets).map(([target, info]) => {
              const docs = (info as Record<string, unknown>).docs as Record<string, unknown> | undefined;
              const md = docs?.content_md as string ?? "";
              return (
                <div key={target}>
                  <h3 className="text-xs font-mono text-[var(--color-accent-primary)] mb-2">
                    {target}
                  </h3>
                  {md ? <ChatMarkdown content={md} /> : (
                    <pre className="text-xs font-mono text-[var(--color-text-secondary)] overflow-auto max-h-96">
                      {JSON.stringify(info, null, 2)}
                    </pre>
                  )}
                </div>
              );
            })}
          </div>
        );
      }
      return content ? <ChatMarkdown content={content} /> : <RawJson data={data} />;
    }

    case "diagram": {
      const mermaid = (data.mermaid_syntax as string) ?? "";
      if (mermaid) {
        return <MermaidBlock chart={mermaid} />;
      }
      return <RawJson data={data} />;
    }

    case "search_results": {
      const results = (data.results as Array<Record<string, unknown>>) ?? [];
      return (
        <div className="space-y-2">
          {results.map((r, i) => (
            <div
              key={i}
              className="rounded-lg border border-[var(--color-border-default)] p-3"
            >
              <div className="text-xs font-medium text-[var(--color-text-primary)]">
                {r.title as string}
              </div>
              <div className="text-[10px] text-[var(--color-text-tertiary)] mt-0.5">
                {r.page_type as string} · score: {((r.relevance_score as number) ?? 0).toFixed(2)}
              </div>
              {r.snippet && (
                <p className="text-xs text-[var(--color-text-secondary)] mt-1 line-clamp-3">
                  {r.snippet as string}
                </p>
              )}
            </div>
          ))}
          {results.length === 0 && (
            <p className="text-xs text-[var(--color-text-tertiary)]">
              No results found.
            </p>
          )}
        </div>
      );
    }

    case "risk_report":
    case "decisions":
    case "dead_code":
    case "graph":
    default:
      return <RawJson data={data} />;
  }
}

function RawJson({ data }: { data: Record<string, unknown> }) {
  return (
    <pre className="text-[10px] font-mono text-[var(--color-text-secondary)] overflow-auto max-h-[70vh] whitespace-pre-wrap break-words">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function MermaidBlock({ chart }: { chart: string }) {
  // Use dynamic import via useEffect for Mermaid (same pattern as wiki)
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Lazy load mermaid
  import("mermaid").then((mermaid) => {
    const mermaidInstance = mermaid.default;
    mermaidInstance.initialize({
      startOnLoad: false,
      theme: "dark",
      themeVariables: {
        background: "var(--color-bg-surface)",
        primaryColor: "#5B9CF6",
        primaryTextColor: "#e2e8f0",
        lineColor: "#334155",
      },
    });
    const id = `mermaid-artifact-${Math.random().toString(36).slice(2)}`;
    mermaidInstance
      .render(id, chart)
      .then(({ svg: renderedSvg }) => setSvg(renderedSvg))
      .catch((err: Error) => setError(err.message));
  });

  if (error) {
    return (
      <div className="rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400">
        Diagram error: {error}
      </div>
    );
  }

  if (svg) {
    return (
      <div
        className="flex justify-center overflow-x-auto rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }

  return (
    <div className="flex justify-center p-4">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent-primary)] border-t-transparent" />
    </div>
  );
}
