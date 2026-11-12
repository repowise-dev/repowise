"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import type {
  AffectedSignal,
  BlastRadiusResponse,
  ChangeCandidate,
  ChangeHealthCheckResponse,
  ChangeReview,
} from "@repowise-dev/types/blast-radius";
import { isWeak, riskySpots, type FileMarks, type Reach } from "./blast-map-model";
import { blastPromptInput, missingPartners, missingPartnersTotal, ownerName, testsLimited } from "./blast-prompt-input";
import { count, reviewStatus, roughCount, type ReviewStatus } from "./review-status";
import { HEALTH_BAND_LABEL, bandForScore, formatScore } from "@repowise-dev/types/health";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildBlastRadiusPrompt } from "../health/ai-prompts/blast-radius-prompt";
import { OwnerAvatar } from "../owners/owner-avatar";
import { CopyPathButton, FilePath } from "../shared/file-path";
import { InfoTip } from "../shared/info-tip";
import { formatNumber } from "../lib/format";
import { NotChecked, RiskLine } from "./change-review";
import { notMeasured, testsFigure, testsPlan, type HealthLane, type TestsPlan } from "./change-review-model";

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

function Block({ title, hint, step, children }: { title: string; hint?: ReactNode; step?: number; children: ReactNode }) {
  return (
    <section className="min-w-0 space-y-2">
      <h3 className="flex items-baseline gap-2 text-[15px] font-medium text-[var(--color-text-primary)]">
        {/* The list already says the order to a screen reader; the figure is for the eye. */}
        {step != null && (
          <span className="font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]" aria-hidden>
            {step}
          </span>
        )}
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
          {all ? "Show fewer" : `Show all ${formatNumber(tests.length)}`}
        </button>
      )}
      {(impact?.recommendations_omitted ?? 0) > 0 && (
        <p className={NONE}>
          The first {formatNumber(tests.length)} of {formatNumber(impact!.recommendations_total)} are listed here.
        </p>
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

/** The tests the change review selected: a list with its command, the whole suite, or none. */
function ReviewTests({ plan }: { plan: TestsPlan }) {
  if (plan.kind === "none") return <p className={NONE}>No test needs to run for this change. {plan.summary}</p>;
  if (plan.kind === "run_all")
    return (
      <div className="space-y-2">
        <p className="text-xs text-[var(--color-text-primary)]">Run the whole test suite: nothing narrower is known to cover this change.</p>
        {plan.reasons.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-4 text-xs text-[var(--color-text-tertiary)]">
            {plan.reasons.slice(0, 3).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        {plan.startWith.length > 0 && (
          <>
            <p className={NONE}>Start with these:</p>
            <PathList paths={plan.startWith} total={plan.startWith.length} />
          </>
        )}
      </div>
    );
  return (
    <div className="space-y-2">
      {plan.commands.length > 0 && <TestCommand text={plan.commands.join("\n")} />}
      <p className={NONE}>{plan.measured ? "Chosen by coverage of the changed code." : "Inferred from imports and calls; no coverage data decided them."}</p>
      {/* The counted-apart tests are not listed by name; the selection's own summary says why they still run. */}
      {plan.apart > 0 && <p className={NONE}>{plan.summary}</p>}
      <PathList paths={plan.tests} total={plan.total - plan.apart} />
    </div>
  );
}

/** Test paths, five at first; `total` counts past what the server listed. */
function PathList({ paths, total }: { paths: string[]; total: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? paths : paths.slice(0, 5);
  return (
    <>
      <ul className="space-y-1.5">
        {shown.map((p) => (
          <Row key={p}>
            <PathRef path={p} href={undefined} />
          </Row>
        ))}
      </ul>
      {paths.length > 5 && (
        <button type="button" className={MORE} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${formatNumber(paths.length)}`}
        </button>
      )}
      {total > paths.length && <p className={NONE}>{formatNumber(total - paths.length)} more not listed here; the command runs them.</p>}
    </>
  );
}

function Owners({ result, changed }: { result: Result; changed: number }) {
  const ofChanged = result.changed_owners;
  const people = ofChanged
    ? ofChanged.map((o) => ({ email: o.email, name: ownerName(o), why: `owns ${formatNumber(o.files)} of the ${count(changed, "changed file")}` }))
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
          {people.length === 1 ? "That is" : "Together,"} {formatNumber(owned)} of the {count(changed, "changed file")}; the
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
      {partners.length > 3 && <li className={NONE}>{formatNumber(missingPartnersTotal(result))} in all, under the details below.</li>}
    </ul>
  );
}

const TONE_DOT: Record<ReviewStatus["tone"], string> = {
  calm: "var(--color-success)",
  look: "var(--color-warning)",
};

function StatusLine({ status }: { status: ReviewStatus }) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 text-[15px]">
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

function Stats({ reach, total, analyzed, tests }: { reach: Reach; total: number; analyzed: number; tests: ReturnType<typeof testsFigure> }) {
  const files = reach.filesTotal;
  return (
    // The glossary button sits beside the list, not in it: a <dl> holds only terms and definitions.
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1.5">
      <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1.5">
        <Stat figure={formatNumber(total)} label={`${total === 1 ? "file" : "files"} changed${total > analyzed ? ` (first ${formatNumber(analyzed)} analyzed)` : ""}`} />
        {files === 0 ? (
          <Stat figure="0" label="source files depend on it" />
        ) : (
          <Stat figure={roughCount(files)} label={`source ${files === 1 ? "file depends" : "files depend"} on it, ${formatNumber(reach.direct)} directly`} />
        )}
        <Stat figure={tests.figure} label={tests.runAll ? "tests to run, the whole suite" : tests.total === 1 ? "test to run" : "tests to run"} />
      </dl>
      <span className="self-center">
        <InfoTip content={GLOSSARY} label="What these words mean" />
      </span>
    </div>
  );
}

/** What the copy button copies: the review's selection when there is one, else the blast radius's tests. */
function copyableTests(result: Result, plan: TestsPlan | null): { commands: string[]; paths: string[] } {
  // A run-all verdict has nothing narrower to copy.
  if (plan) return plan.kind === "list" ? { commands: plan.commands, paths: plan.tests } : { commands: [], paths: [] };
  return {
    commands: result.test_commands ?? [],
    paths: (result.test_impact?.recommendations ?? []).map((t) => t.test_file ?? t.test_id),
  };
}

/** Copies the test commands, or the test paths when the server gave no command. */
function CopyTests({ result, plan }: { result: Result; plan: TestsPlan | null }) {
  const [copied, setCopied] = useState(false);
  const { commands, paths } = copyableTests(result, plan);
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

type StepKey = "look" | "tests" | "partners" | "ask";

/**
 * What to check, in the order the status line names it: a change that asks
 * for a look starts with the files to read; a calm one with its tests.
 */
export function stepOrder(status: ReviewStatus): StepKey[] {
  return status.tone === "look" ? ["look", "tests", "partners", "ask"] : ["tests", "look", "partners", "ask"];
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
  /**
   * The change's score and tests, as `repowise risk` gives them: undefined for
   * a hand-picked file set, null when the server could not build them.
   */
  review?: ChangeReview | null | undefined;
  /** Why `review` is null, from the server. */
  reviewUnavailable?: string | null | undefined;
  /** Where the health check stands, for the list of what was not checked. */
  health?: HealthLane | undefined;
  fileHref?: Href;
}

/**
 * The judgement first: a one-line status, the change-risk line, the size of
 * the change, and the actions, with the agent's review prompt as the primary
 * one. Then what to check, in the order the status line names it, and what
 * the review could not check. Blocks with nothing to say stay out.
 */
export function BlastRadiusVerdict({
  result,
  reach,
  changedFiles,
  changedTotal,
  change,
  healthCheck,
  extraActions,
  review,
  reviewUnavailable,
  health = "not_a_change",
  fileHref,
}: BlastRadiusVerdictProps) {
  const [promptOpen, setPromptOpen] = useState(false);
  const total = changedTotal ?? changedFiles.length;
  const plan = review ? testsPlan(review) : null;
  const tests = testsFigure(review, result.test_impact?.recommendations_total ?? 0);
  const partners = missingPartnersTotal(result);
  const status = useMemo(
    () =>
      reviewStatus({
        reach,
        total,
        testsTotal: tests.total,
        runAll: tests.runAll,
        missingPartners: partners,
        band: result.structural_impact_band,
      }),
    [reach, total, tests.total, tests.runAll, partners, result.structural_impact_band],
  );
  const lanes = notMeasured({
    review,
    reviewUnavailable,
    health,
    notIndexed: reach.changed.filter((m) => m.notIndexed).length,
  });
  const blocks: Record<StepKey, { title: string; hint?: string | undefined; body: ReactNode } | null> = {
    look: riskySpots(reach).length > 0 ? { title: "Look at first", body: <LookFirst reach={reach} fileHref={fileHref} /> } : null,
    tests: {
      title: "Run these tests",
      hint: tests.total > 0 ? formatNumber(tests.total) : undefined,
      body: plan ? <ReviewTests plan={plan} /> : <TestsToRun result={result} />,
    },
    partners: partners > 0 ? { title: "Usually change with these", body: <MissingPartners result={result} fileHref={fileHref} /> } : null,
    ask: { title: "Who to ask", body: <Owners result={result} changed={changedFiles.length} /> },
  };
  const steps = stepOrder(status).flatMap((key) => {
    const block = blocks[key];
    return block ? [{ key, ...block }] : [];
  });

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <StatusLine status={status} />
        {review && <RiskLine risk={review.risk} />}
        <Stats reach={reach} total={total} analyzed={changedFiles.length} tests={tests} />
        <div className="flex flex-wrap items-center gap-2">
          {/* The page's one primary action: ink fill; everything else stays quiet. */}
          <AiPromptButton
            label="Copy review prompt"
            onClick={() => setPromptOpen(true)}
            className="max-sm:h-10 border-transparent bg-[var(--color-action-primary)] text-[var(--color-text-on-action)] hover:border-transparent hover:bg-[var(--color-action-primary-hover)]"
          />
          {extraActions}
          <CopyTests result={result} plan={plan} />
        </div>
      </div>

      <section aria-label="What to check" className="space-y-5 border-t border-[var(--color-border-default)] pt-5">
        <ol className="grid gap-x-10 gap-y-6 xl:grid-cols-2">
          {steps.map((step, i) => (
            <li key={step.key} className="min-w-0">
              <Block step={i + 1} title={step.title} hint={step.hint}>
                {step.body}
              </Block>
            </li>
          ))}
        </ol>
        <NotChecked lanes={lanes} />
      </section>

      <AiPromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        getPrompt={(flavor) =>
          buildBlastRadiusPrompt({
            input: blastPromptInput(result, reach, status, changedFiles, change, changedTotal, healthCheck, review),
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

