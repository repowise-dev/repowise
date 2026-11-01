"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import type {
  AffectedSignal,
  BlastRadiusResponse,
  ChangeCandidate,
  ChangeHealthCheckResponse,
  PrReviewSummary,
} from "@repowise-dev/types/blast-radius";
import { isWeak, riskySpots, type FileMarks, type Reach } from "./blast-map-model";
import { blastPromptInput, missingPartners, ownerName, testsLimited } from "./blast-prompt-input";
import { count, reviewStatus, roughCount, type ReviewStatus } from "./review-status";
import { HEALTH_BAND_LABEL, bandForScore, formatScore } from "@repowise-dev/types/health";
import { scoreTextColor } from "../health/tokens";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildBlastRadiusPrompt } from "../health/ai-prompts/blast-radius-prompt";
import { OwnerAvatar } from "../owners/owner-avatar";
import { CopyPathButton, FilePath } from "../shared/file-path";
import { InfoTip } from "../shared/info-tip";

type Href = ((path: string) => string) | undefined;
type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };

const NONE = "text-xs text-[var(--color-text-tertiary)]";
const MORE =
  "text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] rounded-sm max-sm:min-h-10";
/** A signal as quiet text: no fill and no box, so a dense row stays readable. */
export const CHIP = "inline-flex items-center whitespace-nowrap text-xs text-[var(--color-text-tertiary)]";

/** Weak health is the one signal with a colour: a dot and the score, never a filled badge. */
function PoorHealth({ score }: { score: number }) {
  const band = bandForScore(score);
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap text-xs tabular-nums text-[var(--color-error)]"
      title={`${HEALTH_BAND_LABEL[band]}: health ${formatScore(score)} of 10`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-error)]" aria-hidden />
      health {formatScore(score)}
    </span>
  );
}

/** The words this page uses, once each, for whoever has not met them. */
const GLOSSARY = (
  <dl className="space-y-1 text-xs">
    <div>
      <dt className="inline font-medium">Depends on it:</dt>{" "}
      <dd className="inline">imports a changed file, directly or through other files (each import is a step).</dd>
    </div>
    <div>
      <dt className="inline font-medium">Changed often:</dt>{" "}
      <dd className="inline">among the files with the most recent commits (a hotspot).</dd>
    </div>
    <div>
      <dt className="inline font-medium">Health:</dt>{" "}
      <dd className="inline">the file's code health score out of 10; poor health means the Needs work or At risk band, shown in red.</dd>
    </div>
    <div>
      <dt className="inline font-medium">Usually change together:</dt>{" "}
      <dd className="inline">files that git history shows changing in the same commits.</dd>
    </div>
  </dl>
);

function Block({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <section className="min-w-0 space-y-2">
      <h3 className="flex items-baseline gap-2 text-[15px] font-medium text-[var(--color-text-primary)]">
        {title}
        {hint && <span className="text-xs font-normal text-[var(--color-text-tertiary)]">{hint}</span>}
      </h3>
      {children}
    </section>
  );
}

/** A short path (name, then the dim folder) that links, copies, and shows the full path on hover. */
export function PathRef({ path, href, nameOnly = false }: { path: string; href: string | undefined; nameOnly?: boolean | undefined }) {
  // Under a folder heading the folder is already said; the name alone reads cleaner.
  const label = <FilePath path={path} mode={nameOnly ? "distinguishing" : "name-first"} accent={Boolean(href)} className="max-w-full" />;
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

/**
 * A file's signals as one trailing line of plain words joined by dots, so
 * every row ends at the same edge and reads the same way whatever it carries.
 */
export function SignalChips({ m, extra }: { m: FileMarks; extra?: ReactNode }) {
  const items: ReactNode[] = [];
  if (extra) items.push(extra);
  if (m.notIndexed)
    items.push(
      <span className={CHIP} title="Added since the last index run: no graph, health or owner yet">
        new, not indexed yet
      </span>,
    );
  if (m.isHotspot)
    items.push(
      <span className={CHIP} title="Among the files with the most recent commits">
        changed often
      </span>,
    );
  if (m.untested)
    items.push(
      <span className={CHIP} title="No coverage row, test import or named test reaches it">
        no test found
      </span>,
    );
  if (isWeak(m) && m.healthScore !== null) items.push(<PoorHealth score={m.healthScore} />);
  return (
    <span className="flex shrink-0 flex-wrap items-center justify-end gap-x-1.5 gap-y-0.5">
      {items.map((item, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          {i > 0 && (
            <span className="text-xs text-[var(--color-text-tertiary)]" aria-hidden>
              ·
            </span>
          )}
          {item}
        </span>
      ))}
    </span>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <li className="flex min-w-0 items-center justify-between gap-x-3 gap-y-0.5 text-xs max-sm:flex-wrap">{children}</li>;
}

/** Shown only when something is marked; a calm change says so once, in the status line. */
function LookFirst({ reach, fileHref }: { reach: Reach; fileHref: Href }) {
  const spots = riskySpots(reach);
  return (
    <ul className="space-y-1.5">
      {spots.map((node) => (
        <Row key={node.path}>
          <PathRef path={node.path} href={fileHref?.(node.path)} />
          <SignalChips m={node} extra={node.changed ? null : <span className={CHIP}>depends on it</span>} />
        </Row>
      ))}
    </ul>
  );
}

function TestList({ impact }: { impact: Result["test_impact"] }) {
  const [all, setAll] = useState(false);
  const tests = impact?.recommendations ?? [];
  const shown = all ? tests : tests.slice(0, 5);
  return (
    <>
      <ul className="space-y-1.5">
        {shown.map((t) => (
          <Row key={`${t.repository_id}:${t.test_id}`}>
            <PathRef path={t.test_file ?? t.test_id} href={undefined} />
            {/* Inferred is the default and said once above the list; only a measured test is marked. */}
            {t.basis === "measured" && (
              <span className={CHIP} title="Coverage shows this test runs the changed code">
                measured
              </span>
            )}
          </Row>
        ))}
      </ul>
      {tests.length > 5 && (
        <button type="button" className={MORE} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${tests.length.toLocaleString()}`}
        </button>
      )}
    </>
  );
}

/** Long enough to wrap past four lines at the narrowest column; shorter ones show whole. */
const LONG_COMMAND = 240;

/** The test command, four whole lines high (never cut through a line), with the rest a click away. */
function TestCommand({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > LONG_COMMAND;
  return (
    <div className="space-y-1">
      <pre
        className={`overflow-hidden whitespace-pre-wrap break-all rounded bg-[var(--color-bg-inset)] px-2.5 py-1.5 font-mono text-xs leading-5 text-[var(--color-text-primary)] ${
          long && !open ? "max-h-[92px] [mask-image:linear-gradient(to_bottom,black_70%,transparent)]" : ""
        }`}
      >
        {text}
      </pre>
      {long && (
        <button type="button" className={MORE} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "Show less" : "Show the whole command"}
        </button>
      )}
    </div>
  );
}

function TestsToRun({ result }: { result: Result }) {
  const impact = result.test_impact;
  const total = impact?.recommendations_total ?? 0;
  if (total === 0)
    return (
      <p className={NONE}>
        {testsLimited(impact)
          ? "No test found for these files; coverage data is limited here, so that does not mean none are needed."
          : "No test runs or imports these files."}
      </p>
    );
  const commands = result.test_commands ?? [];
  const measured = impact?.recommendations_by_primary_basis?.measured ?? 0;
  return (
    <div className="space-y-2">
      {commands.length > 0 && (
        <TestCommand text={commands.join("\n")} />
      )}
      <p className={NONE}>
        {measured > 0 ? `${count(measured, "test")} measured by coverage, the rest inferred from imports.` : "Inferred from imports; no coverage data names them."}
      </p>
      <TestList impact={impact} />
    </div>
  );
}

function Owners({ result, changed }: { result: Result; changed: number }) {
  const ofChanged = result.changed_owners;
  const people = ofChanged
    ? ofChanged.map((o) => ({ email: o.email, name: ownerName(o), why: `owns ${o.files.toLocaleString()} of the ${count(changed, "changed file")}` }))
    : result.recommended_reviewers.slice(0, 3).map((r) => ({ email: r.email, name: ownerName(r), why: "owns code that depends on this" }));
  if (people.length === 0) return <p className={NONE}>No one owns the changed files yet: they are new or have no history.</p>;
  const owned = ofChanged ? ofChanged.slice(0, 3).reduce((n, o) => n + o.files, 0) : 0;
  return (
    <ul className="space-y-1.5">
      {people.slice(0, 3).map((p) => (
        <li key={p.email} className="flex min-w-0 items-center gap-2 text-xs">
          <OwnerAvatar name={p.name} email={p.email} size="sm" />
          <span className="truncate text-[var(--color-text-primary)]" title={p.email}>
            {p.name}
          </span>
          <span className="truncate text-[var(--color-text-tertiary)]">{p.why}</span>
        </li>
      ))}
      {/* Never imply full coverage: say how many changed files these people own between them. */}
      {ofChanged && owned < changed && (
        <li className={NONE}>
          {people.length === 1 ? "That is" : "Together,"} {owned.toLocaleString()} of the {count(changed, "changed file")}; the
          rest have no owner on record.
        </li>
      )}
    </ul>
  );
}

function MissingPartners({ result, fileHref }: { result: Result; fileHref: Href }) {
  const partners = missingPartners(result);
  return (
    <ul className="space-y-1.5">
      {partners.slice(0, 3).map((p) => (
        <Row key={p.path}>
          <PathRef path={p.path} href={fileHref?.(p.path)} />
          <span className={CHIP} title={`Usually changes with ${p.partnerOf}, and this change leaves it out`}>
            not in this change
          </span>
        </Row>
      ))}
      {partners.length > 3 && <li className={NONE}>{partners.length} in all, under the details below.</li>}
    </ul>
  );
}

/** Said once, quietly: changed files the index does not have yet, so what the page knows about them is partial. */
function NotIndexedNote({ reach }: { reach: Reach }) {
  const fresh = reach.changed.filter((m) => m.notIndexed).length;
  if (fresh === 0) return null;
  return (
    <p className={NONE}>
      {count(fresh, "changed file")} {fresh === 1 ? "is" : "are"} new since the last index, so {fresh === 1 ? "its" : "their"}{" "}
      health, owners and importers are not known yet. Reach uses the last index.
    </p>
  );
}

const TONE_DOT: Record<ReviewStatus["tone"], string> = {
  calm: "var(--color-success)",
  look: "var(--color-warning)",
};

function Score({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-[var(--color-text-tertiary)]">not scored</span>;
  return <span className={`font-medium tabular-nums ${scoreTextColor(value)}`}>{formatScore(value)}</span>;
}

/**
 * What the PR bot found on this pull request, as context: health before and
 * after in the band colours, the findings it introduced, and a link to its
 * review. Shown only when the host supplies it.
 */
export function PrReviewLine({ review }: { review: PrReviewSummary }) {
  const { health_before: before, health_after: after, introduced, url } = review;
  const health = before != null || after != null;
  // Only a web link from the host is followed; anything else is left out.
  const link = url && /^https?:\/\//i.test(url) ? url : null;
  if (!health && introduced == null && !link) return null;
  return (
    <p className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-[var(--color-text-secondary)]">
      <span className="text-[var(--color-text-tertiary)]">PR bot:</span>
      {health && (
        <span>
          health <Score value={before} /> <span aria-hidden>→</span>
          <span className="sr-only">to</span> <Score value={after} />
        </span>
      )}
      {introduced != null && (
        <>
          <span className="text-[var(--color-text-tertiary)]" aria-hidden>
            ·
          </span>
          <span className="tabular-nums">
            {introduced === 0 ? "no new findings" : `${count(introduced, "new finding")}`}
          </span>
        </>
      )}
      {link && (
        <>
          <span className="text-[var(--color-text-tertiary)]" aria-hidden>
            ·
          </span>
          <a href={link} target="_blank" rel="noreferrer" className="text-[var(--color-accent-primary)] hover:underline">
            View
          </a>
        </>
      )}
    </p>
  );
}

function StatusLine({ status }: { status: ReviewStatus }) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 text-[15px]" role="status">
      <span className="inline-block h-2.5 w-2.5 shrink-0 self-center rounded-full" style={{ background: TONE_DOT[status.tone] }} aria-hidden />
      <span className="font-semibold text-[var(--color-text-primary)]">{status.word}.</span>
      <span className="text-[var(--color-text-secondary)]">{status.reason}</span>
      <span className="basis-full pl-[18px] text-xs text-[var(--color-text-tertiary)]">{status.next}</span>
    </p>
  );
}

function Stat({ figure, label }: { figure: string; label: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dd className="font-mono text-[15px] font-semibold tabular-nums text-[var(--color-text-primary)]">{figure}</dd>
      <dt className="text-xs text-[var(--color-text-secondary)]">{label}</dt>
    </div>
  );
}

function Stats({ reach, total, analyzed, testsTotal }: { reach: Reach; total: number; analyzed: number; testsTotal: number }) {
  const files = reach.files.length;
  return (
    <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1.5">
      <Stat figure={total.toLocaleString()} label={`${total === 1 ? "file" : "files"} changed${total > analyzed ? ` (first ${analyzed.toLocaleString()} analyzed)` : ""}`} />
      {files === 0 ? (
        <Stat figure="0" label="source files depend on it" />
      ) : (
        <Stat figure={roughCount(files)} label={`source ${files === 1 ? "file depends" : "files depend"} on it, ${reach.direct.toLocaleString()} directly`} />
      )}
      <Stat figure={testsTotal.toLocaleString()} label={testsTotal === 1 ? "test to run" : "tests to run"} />
      <span className="self-center">
        <InfoTip content={GLOSSARY} label="What these words mean" />
      </span>
    </dl>
  );
}

/** Copies the test commands, or the test paths when the server gave no command. */
function CopyTests({ result }: { result: Result }) {
  const [copied, setCopied] = useState(false);
  const commands = result.test_commands ?? [];
  const paths = (result.test_impact?.recommendations ?? []).map((t) => t.test_file ?? t.test_id);
  if (commands.length === 0 && paths.length === 0) return null;
  const text = commands.length > 0 ? commands.join("\n") : [...new Set(paths)].join("\n");
  const label = commands.length > 0 ? "Copy test command" : `Copy ${count(new Set(paths).size, "test path")}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked: the command stays readable below */
    }
  };
  return (
    <button
      type="button"
      onClick={() => void copy()}
      className="inline-flex h-7 max-sm:h-10 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
    >
      {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      {copied ? "Copied" : label}
    </button>
  );
}

export interface BlastRadiusVerdictProps {
  result: Result;
  /** The result read once (see `readReach`), shared with the map and lists below. */
  reach: Reach;
  changedFiles: string[];
  /** Every path the change touches, when more than `changedFiles` were analyzed. */
  changedTotal?: number | undefined;
  /** The picked change, for the review prompt; omit for a hand-picked file set. */
  change?: ChangeCandidate | undefined;
  /** A health check the reader ran on this change; its findings join the prompt. */
  healthCheck?: ChangeHealthCheckResponse | undefined;
  /** Quiet actions after the test command (the health check's run button). */
  extraActions?: ReactNode;
  fileHref?: Href;
}

/**
 * The judgement first: a one-line status, the size of the change, and the
 * actions, with the agent's review prompt as the primary one. Then what to
 * look at, which tests to run, who to ask, and what is missing, each as
 * short rows. Blocks with nothing to say stay out.
 */
export function BlastRadiusVerdict({
  result,
  reach,
  changedFiles,
  changedTotal,
  change,
  healthCheck,
  extraActions,
  fileHref,
}: BlastRadiusVerdictProps) {
  const [promptOpen, setPromptOpen] = useState(false);
  const total = changedTotal ?? changedFiles.length;
  const testsTotal = result.test_impact?.recommendations_total ?? 0;
  const partners = missingPartners(result).length;
  const status = useMemo(
    () => reviewStatus({ reach, total, testsTotal, missingPartners: partners, band: result.structural_impact_band }),
    [reach, total, testsTotal, partners, result.structural_impact_band],
  );

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <StatusLine status={status} />
        <Stats reach={reach} total={total} analyzed={changedFiles.length} testsTotal={testsTotal} />
        {change?.pr_review && <PrReviewLine review={change.pr_review} />}
        <NotIndexedNote reach={reach} />
        <div className="flex flex-wrap items-center gap-2">
          {/* The page's one primary action: ink fill; everything else stays quiet. */}
          <AiPromptButton
            label="Copy review prompt"
            onClick={() => setPromptOpen(true)}
            className="max-sm:h-10 border-transparent bg-[var(--color-action-primary)] text-[var(--color-text-on-action)] hover:border-transparent hover:bg-[var(--color-action-primary-hover)]"
          />
          {extraActions}
          <CopyTests result={result} />
        </div>
      </div>

      <div className="grid gap-x-10 gap-y-6 border-t border-[var(--color-border-default)] pt-5 xl:grid-cols-2">
        {riskySpots(reach).length > 0 && (
          <Block title="Look at first">
            <LookFirst reach={reach} fileHref={fileHref} />
          </Block>
        )}
        <Block title="Run these tests" hint={testsTotal > 0 ? testsTotal.toLocaleString() : undefined}>
          <TestsToRun result={result} />
        </Block>
        <Block title="Who to ask">
          <Owners result={result} changed={changedFiles.length} />
        </Block>
        {partners > 0 && (
          <Block title="Usually change with these">
            <MissingPartners result={result} fileHref={fileHref} />
          </Block>
        )}
      </div>

      <AiPromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        getPrompt={(flavor) =>
          buildBlastRadiusPrompt({
            input: blastPromptInput(result, reach, status, changedFiles, change, changedTotal, healthCheck),
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

