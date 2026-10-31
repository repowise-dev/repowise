"use client";

import { useMemo, useState, type ReactNode } from "react";
import { FlaskConical, GitBranch } from "lucide-react";
import type {
  AffectedSignal,
  BlastRadiusResponse,
  ChangeCandidate,
  ChangeHealthCheckResponse,
} from "@repowise-dev/types/blast-radius";
import { buildBlastMap, describeMarks, riskySpots, type BlastMap, type MapFileNode } from "./blast-map-model";
import { blastPromptInput, ownerName, testsLimited } from "./blast-prompt-input";
import { MICRO } from "../health/tokens";
import { HealthBadge } from "../health/health-badge";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildBlastRadiusPrompt } from "../health/ai-prompts/blast-radius-prompt";
import { CopyPathButton, FilePath } from "../shared/file-path";
import { InfoTip } from "../shared/info-tip";

const REACH_WORD: Record<BlastRadiusResponse["structural_impact_band"], string> = {
  localized: "Localized reach",
  moderate: "Moderate reach",
  broad: "Broad reach",
};

const REACH_HINT =
  "How central the changed files are and how far their importers spread, for this repository. An estimate of structural spread, not a chance of breakage.";

type Href = ((path: string) => string) | undefined;
type Result = BlastRadiusVerdictProps["result"];

const NONE = "text-xs text-[var(--color-text-tertiary)]";
const MORE = "text-[11px] text-[var(--color-text-tertiary)]";
const CHIP =
  "inline-flex h-[18px] items-center gap-1 rounded border border-[var(--color-border-default)] px-1.5 text-[10px] text-[var(--color-text-secondary)]";

function n(v: number): string {
  return v.toLocaleString();
}

function Block({ title, count, children }: { title: string; count?: number | undefined; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <p className={MICRO}>
        {title}
        {count !== undefined && count > 0 && <span className="ml-1.5 tabular-nums">{n(count)}</span>}
      </p>
      {children}
    </div>
  );
}

/** A short path (name, then the dim folder) that links, copies, and shows the full path on hover. */
function PathRef({ path, href }: { path: string; href: string | undefined }) {
  const label = <FilePath path={path} mode="name-first" accent={Boolean(href)} className="max-w-full" />;
  return (
    <span className="flex min-w-0 items-center">
      {href ? (
        <a href={href} className="min-w-0 hover:underline">
          {label}
        </a>
      ) : (
        label
      )}
      <CopyPathButton text={path} label="Copy path" copiedLabel="Copied" />
    </span>
  );
}

/** Each signal a file carries as its own chip: health band and score, hotspot, no test evidence. */
function SignalChips({ node }: { node: MapFileNode }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      <HealthBadge score={node.healthScore} />
      {node.isHotspot && (
        <span className={CHIP} title="Hotspot: high churn and activity">
          <svg width="8" height="7" aria-hidden>
            <path d="M4 0 l4 7 l-8 0 z" fill="currentColor" />
          </svg>
          hot
        </span>
      )}
      {node.untested && (
        <span className={`${CHIP} border-dashed`} title="No test evidence: no coverage row, test import or named test">
          no tests
        </span>
      )}
    </span>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <li className="flex min-w-0 items-center justify-between gap-2 text-xs">{children}</li>;
}

function LookFirst({ map, fileHref }: { map: BlastMap; fileHref: Href }) {
  const spots = riskySpots(map);
  if (spots.length === 0) return <p className={NONE}>No changed or direct file is a hotspot, weak, or untested.</p>;
  return (
    <ul className="space-y-1">
      {spots.map((node) => (
        <Row key={node.key}>
          <span className="flex min-w-0 items-center gap-1.5">
            <span
              className="shrink-0 text-[var(--color-text-tertiary)]"
              title={node.hop === 0 ? "Changed in this change" : "Imports a changed file"}
              aria-label={node.hop === 0 ? "changed" : "imports a changed file"}
            >
              {node.hop === 0 ? "●" : "○"}
            </span>
            <PathRef path={node.path} href={fileHref?.(node.path)} />
          </span>
          <SignalChips node={node} />
        </Row>
      ))}
    </ul>
  );
}

function TestsToRun({ impact }: { impact: Result["test_impact"] }) {
  const tests = impact?.recommendations ?? [];
  if (tests.length === 0)
    return (
      <p className={NONE}>
        {testsLimited(impact) ? "Coverage is limited here; none found is not none needed." : "No test runs or imports these files."}
      </p>
    );
  const total = impact?.recommendations_total ?? tests.length;
  return (
    <>
      <ul className="space-y-1">
        {tests.slice(0, 5).map((t) => (
          <Row key={`${t.repository_id}:${t.test_id}`}>
            <PathRef path={t.test_file ?? t.test_id} href={undefined} />
            <span
              className={CHIP}
              title={t.basis === "measured" ? "Coverage shows this test runs the changed code" : "Reaches the changed code through imports"}
            >
              <FlaskConical className="h-2.5 w-2.5" aria-hidden />
              {t.basis === "measured" ? "covers" : "imports"}
            </span>
          </Row>
        ))}
      </ul>
      {total > 5 && <p className={MORE}>{n(total)} in all, under Tests to run below.</p>}
    </>
  );
}

function Owners({ reviewers }: { reviewers: Result["recommended_reviewers"] }) {
  if (reviewers.length === 0) return <p className={NONE}>No owner recorded.</p>;
  return (
    <ul className="space-y-1">
      {reviewers.slice(0, 3).map((r) => (
        <Row key={r.email}>
          <span className="min-w-0 truncate text-[var(--color-text-primary)]" title={r.email}>
            {ownerName(r)}
          </span>
          <span className="shrink-0 tabular-nums text-[var(--color-text-tertiary)]" title="Affected files they own">
            {n(r.files)} files
          </span>
        </Row>
      ))}
    </ul>
  );
}

function MissingPartners({ warnings, fileHref }: { warnings: Result["cochange_warnings"]; fileHref: Href }) {
  const partners = [...new Map(warnings.map((w) => [w.missing_partner, w.changed])).entries()];
  if (partners.length === 0) return <p className={NONE}>Nothing history pairs with these is missing.</p>;
  return (
    <>
      <ul className="space-y-1">
        {partners.slice(0, 3).map(([path, partnerOf]) => (
          <Row key={path}>
            <PathRef path={path} href={fileHref?.(path)} />
            <span className={CHIP} title={`Usually changes with ${partnerOf}, and this change leaves it out`}>
              <GitBranch className="h-2.5 w-2.5" aria-hidden />
              missing
            </span>
          </Row>
        ))}
      </ul>
      {partners.length > 3 && <p className={MORE}>{partners.length} in all, under Co-change warnings below.</p>}
    </>
  );
}

export interface BlastRadiusVerdictProps {
  result: BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
  changedFiles: string[];
  /** Every path the change touches, when more than `changedFiles` were analyzed. */
  changedTotal?: number | undefined;
  /** What the change is called: a branch, a short sha, "Uncommitted changes". */
  subject: ReactNode;
  /** The picked change, for the review prompt; omit for a hand-picked file set. */
  change?: ChangeCandidate | undefined;
  /** A health check the reader ran on this change; its findings join the prompt. */
  healthCheck?: ChangeHealthCheckResponse | undefined;
  fileHref?: Href;
}

/**
 * The judgement before the picture: how far the change reaches, then what to
 * do about it, as short rows with signal chips. The primary action hands the
 * whole review to an agent as one prompt with every fact and full path.
 */
export function BlastRadiusVerdict({
  result,
  changedFiles,
  changedTotal,
  subject,
  change,
  healthCheck,
  fileHref,
}: BlastRadiusVerdictProps) {
  const map = useMemo(() => buildBlastMap(result, changedFiles), [result, changedFiles]);
  const [promptOpen, setPromptOpen] = useState(false);
  const total = changedTotal ?? changedFiles.length;
  const direct = map.firstHop.length + map.hiddenFirstHop;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-base text-[var(--color-text-primary)]">{subject}</p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--color-text-secondary)]">
            <span>
              <b className="font-semibold tabular-nums text-[var(--color-text-primary)]">{n(total)}</b> changed
              {total > changedFiles.length && <span title={`The first ${n(changedFiles.length)} are analyzed`}>*</span>}
            </span>
            <span aria-hidden>→</span>
            {map.reachedFiles === 0 ? (
              <span>nothing imports them</span>
            ) : (
              <span>
                <b className="font-semibold tabular-nums text-[var(--color-text-primary)]">{n(map.reachedFiles)}</b> reached in{" "}
                {n(map.reachedModules)} modules, {n(direct)} direct
              </span>
            )}
            <span className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)]">
              {REACH_WORD[result.structural_impact_band]}
              <InfoTip content={REACH_HINT} label="What reach means" />
            </span>
          </p>
        </div>
        <AiPromptButton label="Copy review prompt" onClick={() => setPromptOpen(true)} />
      </div>

      <div className="grid gap-x-8 gap-y-5 md:grid-cols-2">
        <Block title="Look at first">
          <LookFirst map={map} fileHref={fileHref} />
        </Block>
        <Block title="Run these tests" count={result.test_impact?.recommendations_total}>
          <TestsToRun impact={result.test_impact} />
        </Block>
        <Block title="Ask for review">
          <Owners reviewers={result.recommended_reviewers} />
        </Block>
        <Block title="Usually changes with these">
          <MissingPartners warnings={result.cochange_warnings} fileHref={fileHref} />
        </Block>
      </div>

      <AiPromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        getPrompt={(flavor) =>
          buildBlastRadiusPrompt({
            input: blastPromptInput(result, map, changedFiles, change, changedTotal, healthCheck),
            flavor,
          })
        }
        filePath={change?.label ?? null}
        title="Review prompt"
        description="Every fact on this page, with full paths, for your coding agent to review the change."
      />
    </div>
  );
}

export { describeMarks };
