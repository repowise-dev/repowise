"use client";

/**
 * One area, read top-down: its score and how it moved, the one action (hand
 * the area's top findings to an agent), where inside it to look, its worst
 * files, and a single line of facts. Hotspots, people and the findings
 * themselves wait behind one disclosure. The repository is the root area and
 * reads the same way.
 */

import { ChevronRight } from "lucide-react";
import type { AreaDetail } from "@repowise-dev/types/areas";

import { AiPromptButton } from "../ai-prompt-button";
import type { AiPromptRequest } from "../ai-prompt-action";
import { buildAreaAiPrompt } from "../ai-prompts/area-prompt";
import type { AreaIndex } from "../map/areas";
import { areasNeedingAttention, mostImproved } from "../map/area-rank";
import { AreaHeader } from "./area-header";
import { AreaRows, FileRows, FindingRows, RailSection } from "./area-sections";
import type { AreaRailLabels } from "./labels";

/** Where the card's secondary links go; each is optional. */
export interface AreaLinks {
  findings?: string;
  fixFirst?: string;
  contributors?: string;
}

const LIST_CAP = 5;

export interface AreaCardProps {
  detail: AreaDetail;
  index: AreaIndex;
  /** The disc's name as the map shows it. */
  nameOf: (path: string) => string;
  labels: AreaRailLabels;
  selectedPath?: string | null;
  onSelectArea: (path: string) => void;
  onSelectFile: (path: string) => void;
  openPrompt: (request: AiPromptRequest) => void;
  links?: AreaLinks | undefined;
  onNavigate?: ((href: string) => void) | undefined;
  repoName?: string | undefined;
  /** What the figures cover, named in the header. */
  scope?: string | undefined;
}

function Links({ links, labels, onNavigate }: { links: AreaLinks; labels: AreaRailLabels; onNavigate?: ((href: string) => void) | undefined }) {
  const items = [
    { href: links.findings, label: labels.findingsLink },
    { href: links.fixFirst, label: labels.fixFirstLink },
    { href: links.contributors, label: labels.contributorsLink },
  ].filter((l): l is { href: string; label: string } => Boolean(l.href));
  if (items.length === 0) return null;
  return (
    <nav className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {items.map((l) => (
        <a
          key={l.label}
          href={l.href}
          onClick={(e) => {
            if (!onNavigate) return;
            e.preventDefault();
            onNavigate(l.href);
          }}
          className="rounded text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          {l.label}
        </a>
      ))}
    </nav>
  );
}

/** Hotspots, people and tests, as one line of facts with their counts. */
function Facts({ detail, labels }: { detail: AreaDetail; labels: AreaRailLabels }) {
  const a = detail.area;
  const cov = detail.test_reach.line_coverage_pct;
  const parts = [
    labels.hotspots(detail.hotspots.total),
    a.bus_factor_1_count > 0 ? labels.busFactorOne(a.bus_factor_1_count) : null,
    cov != null ? labels.coverageLine(Math.round(cov), detail.test_reach.measured_files) : labels.noCoverage,
  ].filter(Boolean);
  return <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">{parts.join(" · ")}</p>;
}

export function AreaCard(props: AreaCardProps) {
  const { detail, index, nameOf, labels, onSelectArea, onSelectFile } = props;
  const path = detail.area.path;
  const isRoot = path === "";
  const findings = detail.top_findings;
  const attention = areasNeedingAttention(index, path);
  const improved = mostImproved(index, path);
  const owner = detail.area.primary_owner;

  const prompt = () =>
    props.openPrompt({
      title: labels.promptTitle,
      description: labels.promptDescription,
      filePath: isRoot ? null : `${path}/`,
      getPrompt: (flavor) =>
        buildAreaAiPrompt({
          area: path,
          score: detail.area.health,
          fileCount: detail.area.file_count,
          findings,
          worstFiles: detail.worst_files,
          flavor,
          ...(props.repoName ? { repoName: props.repoName } : {}),
        }),
    });

  return (
    <div data-testid="area-card" data-area={path} className="flex flex-col gap-5">
      <AreaHeader
        detail={detail}
        eyebrow={isRoot ? labels.repoEyebrow : labels.areaEyebrow}
        scope={props.scope}
        title={isRoot ? labels.repository : nameOf(path)}
        labels={labels}
      />

      {findings.length > 0 ? (
        <AiPromptButton label={labels.promptAction(findings.length)} onClick={prompt} className="self-start" />
      ) : (
        <p className="text-xs text-[var(--color-success)]">{labels.allClean(detail.area.file_count)}</p>
      )}

      {attention.length > 0 ? (
        <RailSection title={isRoot ? labels.needsAttention : labels.subAreas}>
          <AreaRows areas={attention} nameOf={nameOf} onSelect={onSelectArea} />
        </RailSection>
      ) : null}

      {improved ? (
        <RailSection title={labels.improvedMost}>
          <AreaRows areas={[improved]} nameOf={nameOf} onSelect={onSelectArea} showDelta />
        </RailSection>
      ) : null}

      {detail.worst_files.length > 0 ? (
        <RailSection title={labels.worstFiles}>
          <FileRows files={detail.worst_files.slice(0, LIST_CAP)} selectedPath={props.selectedPath} onSelect={onSelectFile} />
        </RailSection>
      ) : null}

      <Facts detail={detail} labels={labels} />

      <details className="group flex flex-col gap-4">
        <summary className="flex cursor-pointer list-none items-center gap-1 rounded text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] [&::-webkit-details-marker]:hidden">
          <ChevronRight aria-hidden className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
          {labels.hotspots(detail.hotspots.total)} · {labels.owners} · {labels.topFindings}
        </summary>
        <div className="mt-3 flex flex-col gap-4">
          {detail.hotspots.files.length > 0 ? (
            <RailSection title={labels.hotspots(detail.hotspots.total)}>
              <FileRows files={detail.hotspots.files.slice(0, LIST_CAP)} selectedPath={props.selectedPath} onSelect={onSelectFile} />
            </RailSection>
          ) : null}
          <RailSection title={labels.owners}>
            {owner ? (
              <p className="text-xs text-[var(--color-text-secondary)]">{labels.ownerShare(owner.name, Math.round(owner.share * 100))}</p>
            ) : null}
            <ul className="flex flex-col gap-0.5 text-xs text-[var(--color-text-secondary)]">
              {detail.owners.top.slice(0, LIST_CAP).map((o) => (
                <li key={o.key} className="flex justify-between gap-2">
                  <span className="truncate">{o.name}</span>
                  <span className="font-mono tabular-nums text-[var(--color-text-tertiary)]">{labels.files(o.files)}</span>
                </li>
              ))}
            </ul>
          </RailSection>
          <RailSection title={labels.topFindings}>
            {findings.length ? (
              <FindingRows findings={findings} onSelectFile={onSelectFile} />
            ) : (
              <p className="text-xs text-[var(--color-text-tertiary)]">{labels.noFindings}</p>
            )}
          </RailSection>
        </div>
      </details>

      {props.links ? <Links links={props.links} labels={labels} onNavigate={props.onNavigate} /> : null}
    </div>
  );
}
