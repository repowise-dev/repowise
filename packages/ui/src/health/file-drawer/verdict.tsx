"use client";

/**
 * The verdict at the top of the file drawer: how bad the file is (score, band,
 * trend), why in one sentence, what fixing the top issue gives back when that
 * can be said honestly, and the one thing to do. Plus the prompt that one
 * action opens.
 */

import { ExternalLink, Wrench } from "lucide-react";
import { bandForScore, formatScore, HEALTH_BAND_LABEL } from "@repowise-dev/types/health";
import type { FileHealthTrend, FileSignals } from "@repowise-dev/types/health";
import { AiPromptButton } from "../ai-prompt-button";
import { AiPromptModal, fileChatContext } from "../ai-prompt-modal";
import { buildFileHealthAiPrompt } from "../ai-prompt-builder";
import { ScoreSplit } from "../score-split";
import { Sparkline } from "../sparkline";
import { HEALTH_UNSUPPORTED_LABEL } from "../map/lens";
import { deltaColor, formatDelta, healthBandColor } from "../tokens";
import type { Verdict } from "../plain-language";
import type {
  HealthDrawerMetric,
  HealthFileDrawerProps,
} from "../health-file-drawer";
import type { DrawerModel, StatusOverrides } from "./model";
import { QUIET_LINK } from "./shared";

export function VerdictSection({
  metric,
  trend,
  breakdown,
  signals,
  model,
  planTarget,
  fullPage,
  onNavigate,
  onPrompt,
}: {
  metric: HealthDrawerMetric;
  trend: FileHealthTrend | null | undefined;
  breakdown: HealthFileDrawerProps["breakdown"];
  signals: FileSignals | null | undefined;
  model: DrawerModel;
  planTarget: string | null;
  fullPage: string | undefined;
  onNavigate: ((href: string) => void) | undefined;
  onPrompt: () => void;
}) {
  return (
    <section aria-label="Verdict" className="flex flex-col gap-3">
      <ScoreRow metric={metric} trend={trend} />
      {metric.score != null ? (
        <ScoreSplit categories={breakdown?.categories} signals={signals} />
      ) : null}
      <VerdictSentence
        verdict={model.verdict}
        show={metric.score != null || model.findings.length > 0}
      />
      <RecoveryLine recovers={model.recovers} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
        {/* The prompt is the one primary action, withheld only when the
            verdict is the all-clear and there is nothing for it to fix. */}
        {model.showPrompt ? (
          <AiPromptButton onClick={onPrompt} label="Fix with AI prompt" />
        ) : null}
        <PlanLink href={planTarget} onNavigate={onNavigate} />
        {fullPage ? (
          <a href={fullPage} className={QUIET_LINK}>
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            Open full page
          </a>
        ) : null}
      </div>
    </section>
  );
}

function ScoreRow({
  metric,
  trend,
}: {
  metric: HealthDrawerMetric;
  trend: FileHealthTrend | null | undefined;
}) {
  if (metric.score == null) {
    // Nothing measured this file, so there is no number and no band.
    return (
      <p className="text-[15px] text-[var(--color-text-secondary)]">
        Not analysed. {HEALTH_UNSUPPORTED_LABEL}.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
      <ScoreLede score={metric.score} />
      {/* A flat line says nothing a "no change" would not, so it is left out. */}
      {trend && moves(trend) ? <TrendMark trend={trend} /> : null}
    </div>
  );
}

function moves(trend: FileHealthTrend): boolean {
  const first = trend.points[0];
  return trend.points.length >= 2 && trend.points.some((p) => p.score !== first!.score);
}

function VerdictSentence({ verdict, show }: { verdict: Verdict; show: boolean }) {
  if (!verdict.text || !show) return null;
  return (
    <p className="max-w-[52ch] text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
      {verdict.text}
    </p>
  );
}

function RecoveryLine({ recovers }: { recovers: DrawerModel["recovers"] }) {
  if (recovers == null) return null;
  return (
    <p className="text-xs text-[var(--color-text-tertiary)]">
      Fixing the top issue{recovers.places > 1 ? ` in all ${recovers.places} places` : ""} is
      worth about <span className="tabular-nums">{recovers.points.toFixed(1)}</span>{" "}
      {recovers.points === 1 ? "point" : "points"}.
    </p>
  );
}

function PlanLink({
  href,
  onNavigate,
}: {
  href: string | null;
  onNavigate: ((href: string) => void) | undefined;
}) {
  if (!href) return null;
  return (
    <a
      href={href}
      onClick={
        onNavigate
          ? (e) => {
              e.preventDefault();
              onNavigate(href);
            }
          : undefined
      }
      className={QUIET_LINK}
    >
      <Wrench className="h-3.5 w-3.5" aria-hidden="true" />
      Refactoring plan
    </a>
  );
}

/** The score in ink and its band as a dot and a word: the dot is the health colour. */
function ScoreLede({ score }: { score: number }) {
  const color = healthBandColor(bandForScore(score));
  return (
    <>
      <div className="flex items-baseline gap-x-2">
        <span className="text-[52px] font-semibold leading-none tracking-tight tabular-nums text-[var(--color-text-primary)]">
          {formatScore(score)}
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">out of 10</span>
      </div>
      <span className="inline-flex items-center gap-1.5 pb-1 text-[15px] font-medium text-[var(--color-text-primary)]">
        <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: color }} />
        {HEALTH_BAND_LABEL[bandForScore(score)]}
      </span>
    </>
  );
}

/** Direction, in neutral ink: the delta's sign carries the health colour. */
function TrendMark({ trend }: { trend: FileHealthTrend }) {
  return (
    <div className="flex items-center gap-2 pb-1 text-[var(--color-text-tertiary)]">
      <Sparkline
        values={trend.points.map((p) => p.score)}
        domain={[0, 10]}
        width={72}
        height={20}
        stroke="currentColor"
      />
      {trend.delta != null && trend.delta !== 0 ? (
        <span className={`text-xs font-semibold tabular-nums ${deltaColor(trend.delta)}`}>
          {formatDelta(trend.delta)}
        </span>
      ) : null}
      {trend.declining ? (
        <span className="text-xs text-[var(--color-text-secondary)]">declining</span>
      ) : null}
    </div>
  );
}

/**
 * Everything the drawer knows about this file, as one prompt. The modal
 * renders through a portal, so it sits with the drawer for locality rather
 * than for layout.
 */
export function FilePromptModal({
  open,
  onOpenChange,
  props,
  model,
  overrides,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  props: HealthFileDrawerProps & { metric: HealthDrawerMetric };
  model: DrawerModel;
  overrides: StatusOverrides;
}) {
  const { metric, breakdown, signals, performance, trend, suggestions = {}, repoName } = props;
  return (
    <AiPromptModal
      open={open}
      onOpenChange={onOpenChange}
      filePath={metric.file_path}
      chatContext={fileChatContext(metric.file_path)}
      title="AI prompt for this file"
      description="Every scored finding, category ceiling, open performance cause and change signal this drawer holds, written up so an agent can triage the file before it edits anything."
      getPrompt={(flavor) =>
        buildFileHealthAiPrompt({
          file: metric,
          findings: model.findings.map((f) => ({
            ...f,
            // Triage applied in this session but not yet refetched, so a
            // finding just marked resolved leaves the prompt.
            status: overrides[f.id] ?? f.status,
            details: f.details as Record<string, unknown> | null | undefined,
          })),
          categories: breakdown?.categories ?? [],
          signals: signals ?? null,
          performance: performance ?? null,
          trendDelta: trend?.delta ?? null,
          suggestions,
          flavor,
          ...(repoName ? { repoName } : {}),
        })
      }
    />
  );
}
