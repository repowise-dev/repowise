"use client";

import { useState } from "react";
import { cn } from "../lib/cn";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { ChatMarkdown } from "./chat-markdown";
import {
  ContextRenderer,
  DeadCodeRenderer,
  DecisionsRenderer,
  DiagramRenderer,
  GenericJsonRenderer,
  GraphPathRenderer,
  OverviewRenderer,
  RiskReportRenderer,
  SearchResultsRenderer,
} from "./artifacts";
import type {
  ChatArtifact,
  ContextArtifactData,
  DeadCodeArtifactData,
  DecisionsArtifactData,
  DiagramArtifactData,
  GraphPathArtifactData,
  OverviewArtifactData,
  RiskReportArtifactData,
  SearchResultsArtifactData,
} from "@repowise-dev/types/chat";

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

  if (artifacts.length === 0) return null;

  const active = artifacts[Math.min(activeIdx, artifacts.length - 1)];
  if (!active) return null;

  return (
    <AdaptivePanel
      open={open}
      onOpenChange={(o) => (!o ? onClose() : undefined)}
      title="Artifacts"
      widthClassName="md:max-w-[480px] lg:max-w-[420px]"
      modal={false}
    >
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

      <div className="flex-1 overflow-y-auto p-4">
        <ArtifactRenderer artifact={active} />
      </div>
    </AdaptivePanel>
  );
}

/**
 * Dispatcher: switches on the artifact `.type` discriminant. Every
 * `KnownChatArtifact` variant has a dedicated renderer; the `default`
 * branch handles `wiki_page` (legacy markdown-shaped overview pages from
 * older indexer outputs) and falls through to `GenericJsonRenderer` for
 * unknown shapes.
 */
function ArtifactRenderer({ artifact }: { artifact: Artifact | ChatArtifact }) {
  const { type, data } = artifact;

  switch (type) {
    case "overview":
      return <OverviewRenderer data={data as unknown as OverviewArtifactData} />;
    case "context":
      return <ContextRenderer data={data as unknown as ContextArtifactData} />;
    case "risk_report":
      return <RiskReportRenderer data={data as unknown as RiskReportArtifactData} />;
    case "search_results":
      return <SearchResultsRenderer data={data as unknown as SearchResultsArtifactData} />;
    case "graph":
      return <GraphPathRenderer data={data as unknown as GraphPathArtifactData} />;
    case "decisions":
      return <DecisionsRenderer data={data as unknown as DecisionsArtifactData} />;
    case "dead_code":
      return <DeadCodeRenderer data={data as unknown as DeadCodeArtifactData} />;
    case "diagram":
      return <DiagramRenderer data={data as unknown as DiagramArtifactData} />;

    case "wiki_page": {
      // Legacy markdown-shaped artifact, pre-dating the typed union.
      const content =
        ((data as Record<string, unknown>).content_md as string) ??
        ((data as Record<string, unknown>).content as string) ??
        "";
      return content ? (
        <ChatMarkdown content={content} />
      ) : (
        <GenericJsonRenderer data={data as Record<string, unknown>} />
      );
    }

    default:
      return <GenericJsonRenderer data={data as Record<string, unknown>} />;
  }
}
