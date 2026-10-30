"use client";

import { Fragment, useCallback, useMemo, useState, type ElementType, type ReactNode } from "react";
import { BellOff, Check, PanelRight, X } from "lucide-react";
import { toast } from "sonner";
import {
  ACTION_TIER_LABELS,
  ACTION_TIER_ORDER,
  type ActionHorizonKey,
  type ActionsResponse,
  type ActionStateValue,
  type ActionTier,
  type NextAction,
} from "@repowise-dev/types/actions";

import { useAiPromptAction } from "../health/ai-prompt-action";
import type { AiPromptFlavor } from "../health/ai-prompt-builder";
import { EFFORT_LABEL } from "../health/labels";
import type { Severity } from "../health/tokens";
import { RowPromptAction } from "../health/row-prompt-action";
import { SeverityMark } from "../health/severity-mark";
import { CLICKABLE_ROW_CLS, clickableRowProps } from "../shared/responsive-table";
import { RowOverflow } from "../shared/row-overflow";
import { Segmented } from "../shared/segmented";
import { ActionDrawer, type ActionDrawerProps } from "./action-drawer";
import { OverviewSection } from "./section";
import { formatNumber } from "../lib/format";

/** Rows shown before "Show all"; the response carries up to 20 per horizon. */
const PREVIEW = 5;

export interface NextActionsProps {
  /** `null` when the server predates actions; the section then renders nothing. */
  data: ActionsResponse | null;
  hrefFor: (action: NextAction) => string | null;
  /** A file's own page, for the files an opened action names. */
  fileHref?: ((path: string) => string | null) | undefined;
  /**
   * Persist a dismissal, snooze, done, or (null) an undo. Omit to hide those verbs.
   * The sentence over the list is the server's, so pass fresh `data` after a write.
   */
  onSetState?: (action: NextAction, state: ActionStateValue | null) => Promise<void>;
  /** An action's agent prompt as core renders it. Omit to hide the prompt. */
  loadPrompt?: ActionDrawerProps["loadPrompt"];
  /**
   * Replaces the sentence after the "Not checked" list. Omit for the default
   * `repowise update` hint; pass `null` to show only the list.
   */
  unavailableHint?: ReactNode | null;
  /** Show each command's CLI line in the drawer. Set false for a host with no CLI. */
  showCliCommands?: boolean;
  /**
   * Set false to start folded behind its heading, for a page where the list
   * would push everything else below the fold. Defaults to open.
   */
  defaultOpen?: boolean;
  LinkComponent?: ElementType | undefined;
  /** The list's own words. English by default; the actions' text is the server's. */
  copy?: Partial<NextActionsCopy> | undefined;
}

export interface NextActionsCopy {
  title: string;
  /** The folded heading, which carries the count. */
  titleCount: (count: number) => string;
  timeFrame: string;
  week: string;
  weekHint: string;
  quarter: string;
  quarterHint: string;
  tier: (tier: ActionTier) => string;
  allAnswered: string;
  showFewer: string;
  showAll: (count: number) => string;
  listing: (shown: number, total: number) => string;
  answered: (count: number) => string;
  /** `checks` is the comma-joined list of checks that did not run. */
  notChecked: (checks: string) => string;
  emptyChecks: (count: number) => string;
  undo: string;
  undoFailed: string;
  saveFailed: string;
  openDetails: string;
  markDone: string;
  markedDone: string;
  snooze: string;
  snoozed: string;
  dismiss: string;
  dismissed: string;
  effort: (effort: NextAction["effort"]) => string;
  worthCheck: string;
  inferred: string;
  openRow: (title: string) => string;
  moreFor: (title: string) => string;
  /** Undefined keeps SeverityMark's own word. */
  severity: (severity: Severity) => string | undefined;
}

export const NEXT_ACTIONS_COPY: NextActionsCopy = {
  title: "Do next",
  titleCount: (count) => `Do next · ${count.toLocaleString("en-US")}`,
  timeFrame: "Time frame",
  week: "This week",
  weekHint: "What the last 7 days of commits changed",
  quarter: "This quarter",
  quarterHint: "What the last 90 days say is worth planning",
  tier: (tier) => ACTION_TIER_LABELS[tier] ?? tier,
  allAnswered: "You have answered everything listed here. Answers are kept until the facts change.",
  showFewer: "Show fewer",
  showAll: (count) => `Show all ${formatNumber(count)}`,
  listing: (shown, total) => `Listing ${formatNumber(shown)} of ${formatNumber(total)}.`,
  answered: (count) => `${plural(count, "action")} answered (done, snoozed or dismissed).`,
  notChecked: (checks) => `Not checked: ${checks}.`,
  emptyChecks: (count) =>
    `Repowise ran ${plural(count, "check")} over this index and none of them produced work for this time frame.`,
  undo: "Undo",
  undoFailed: "Could not undo",
  saveFailed: "Could not save that; the action is back in the list",
  openDetails: "Open details",
  markDone: "Mark done",
  markedDone: "Marked done. It returns if the facts change.",
  snooze: "Snooze for 14 days",
  snoozed: "Snoozed for 14 days",
  dismiss: "Dismiss",
  dismissed: "Dismissed. It returns if the facts change.",
  effort: (effort) => `${EFFORT_LABEL[effort]} effort`,
  worthCheck: "worth a check",
  inferred: "(inferred)",
  openRow: (title) => `Open ${title}`,
  moreFor: (title) => `More for ${title}`,
  severity: () => undefined,
};

type ActionPromptLoader = (action: NextAction, flavor: AiPromptFlavor) => Promise<string>;

/**
 * The row-level "Copy prompt for your agent": opens the shared prompt modal on
 * one action's prompt. Render `promptModal` once beside the rows.
 */
export function useActionPrompt(): {
  openActionPrompt: (action: NextAction, load: ActionPromptLoader) => void;
  promptModal: ReactNode;
} {
  const { openPrompt, promptModal } = useAiPromptAction();
  const openActionPrompt = useCallback(
    (action: NextAction, load: ActionPromptLoader) =>
      openPrompt({
        title: action.title.replace(/`/g, ""),
        filePath: action.target.path || null,
        promptSource: (flavor) => load(action, flavor),
      }),
    [openPrompt],
  );
  return { openActionPrompt, promptModal };
}

/** Split a title on backticks so paths and symbols set in mono. */
export function renderActionTitle(title: string): ReactNode {
  return title.split("`").map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="font-mono text-[0.85em] [overflow-wrap:anywhere]">
        {part}
      </code>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

// en-US grouping, as the server writes `{n:,}`, so a local sentence reads the same.
function plural(n: number, noun: string): string {
  return `${n.toLocaleString("en-US")} ${noun}${n === 1 ? "" : "s"}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Sep 28" from the anchor's own calendar day, never shifted into the viewer's timezone. */
function anchorDay(iso: string | null): string | null {
  const m = iso ? /^\d{4}-(\d{2})-(\d{2})/.exec(iso) : null;
  const month = m ? MONTHS[Number(m[1]) - 1] : undefined;
  return m && month ? `${month} ${Number(m[2])}` : null;
}

const workIn = (h: ActionsResponse["horizons"][ActionHorizonKey]) =>
  (h.by_tier.act_now ?? 0) + (h.by_tier.plan ?? 0);

/**
 * The sentence for a view that carries no `summary` (a server that predates
 * it). Same as the server's wording, from the same counts, so the line never
 * goes blank. A view with `summary` always wins.
 */
function fallbackSummary(data: ActionsResponse, horizon: ActionHorizonKey): string {
  const until = anchorDay(data.anchor);
  const window =
    horizon === "week"
      ? until
        ? `in the week to ${until}, the last indexed commit`
        : "this week"
      : "this quarter";
  const work = workIn(data.horizons[horizon]);
  if (work === 0) {
    const other = workIn(data.horizons[horizon === "week" ? "quarter" : "week"]);
    if (other === 0) return `Nothing stands out ${window}.`;
    const tail =
      horizon === "week"
        ? `${other === 1 ? "is" : "are"} worth planning this quarter`
        : "came up this week";
    return `Nothing needs you ${window}. ${plural(other, "thing")} ${tail}.`;
  }
  const now = data.horizons[horizon].by_tier.act_now ?? 0;
  return `${plural(work, "thing")} worth doing ${window}${now ? `, ${now} of them now` : ""}.`;
}

export function NextActions({
  data,
  hrefFor,
  fileHref,
  onSetState,
  loadPrompt,
  unavailableHint,
  showCliCommands,
  defaultOpen = true,
  LinkComponent,
  copy,
}: NextActionsProps) {
  const c = { ...NEXT_ACTIONS_COPY, ...copy };
  // Open on the week unless it holds no work and the quarter does: a lone
  // "add a coverage report" is not a reason to show an empty week first.
  // Counts come from `data` alone, like the sentence, so the two agree; both
  // move when the host passes the re-read view after an answer.
  const work = (key: ActionHorizonKey) => (data ? workIn(data.horizons[key]) : 0);
  const initial: ActionHorizonKey = work("week") === 0 && work("quarter") > 0 ? "quarter" : "week";
  const [horizon, setHorizon] = useState<ActionHorizonKey>(initial);
  const [expanded, setExpanded] = useState(false);
  // Optimistic: a row the person answered leaves at once and comes back if the
  // write fails. Kept by id with the action, so "answered everything" still
  // knows its time frame once a re-read drops the row.
  const [answered, setAnswered] = useState<ReadonlyMap<string, NextAction>>(new Map());
  const [opened, setOpened] = useState<NextAction | null>(null);
  const { openActionPrompt, promptModal } = useActionPrompt();

  const rows = useMemo(
    () => (data ? data.horizons[horizon].actions.filter((a) => !answered.has(a.id)) : []),
    [data, horizon, answered],
  );
  if (!data) return null;

  const h = data.horizons[horizon];
  const shown = expanded ? rows : rows.slice(0, PREVIEW);
  const total = h.total - (h.actions.length - rows.length);
  const unavailable = Object.keys(data.unavailable);

  const answer = (action: NextAction, state: ActionStateValue, message: string) => {
    if (!onSetState) return;
    setAnswered((s) => new Map(s).set(action.id, action));
    const restore = () =>
      setAnswered((s) => {
        const next = new Map(s);
        next.delete(action.id);
        return next;
      });
    onSetState(action, state).then(
      () =>
        toast.success(message, {
          action: {
            label: c.undo,
            onClick: () => {
              restore();
              void onSetState(action, null).catch(() => toast.error(c.undoFailed));
            },
          },
        }),
      () => {
        restore();
        toast.error(c.saveFailed);
      },
    );
  };

  const timeFrame = (
    <Segmented
      label={c.timeFrame}
      value={horizon}
      options={[
        { value: "week", label: c.week, count: String(work("week")), hint: c.weekHint },
        { value: "quarter", label: c.quarter, count: String(work("quarter")), hint: c.quarterHint },
      ]}
      onChange={(v) => {
        setHorizon(v);
        setExpanded(false);
      }}
    />
  );

  return (
    <OverviewSection
      title={defaultOpen ? c.title : c.titleCount(work(horizon))}
      description={data.summary?.[horizon] ?? fallbackSummary(data, horizon)}
      // A folded section has no header action slot, so the time frame sits
      // under the summary, above the list it filters, and the toggle carries
      // the count.
      action={defaultOpen ? timeFrame : undefined}
      collapsible={!defaultOpen}
      defaultOpen={defaultOpen}
    >
      {!defaultOpen && timeFrame}
      {shown.length === 0 && [...answered.values()].some((a) => a.horizons.includes(horizon)) ? (
        <p className="py-2 text-sm text-[var(--color-text-secondary)]">
          {c.allAnswered}
        </p>
      ) : shown.length === 0 ? (
        <EmptyActions data={data} copy={c} />
      ) : (
        <div className="flex flex-col">
          {groupByTier(shown).map(([tier, items]) => (
            <div key={tier} className="flex flex-col">
              <h3 className="pb-1 pt-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] first:pt-0">
                {c.tier(tier)}
              </h3>
              <ul className="flex flex-col divide-y divide-[var(--color-border-default)]">
                {items.map((action) => (
                  <ActionRow
                    key={action.id}
                    action={action}
                    selected={opened?.id === action.id}
                    onOpen={() => setOpened(action)}
                    onPrompt={loadPrompt ? () => openActionPrompt(action, loadPrompt) : undefined}
                    onAnswer={onSetState ? (state, message) => answer(action, state, message) : undefined}
                    copy={c}
                  />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
        {rows.length > PREVIEW && (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="rounded text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
          >
            {expanded ? c.showFewer : c.showAll(Math.min(rows.length, total))}
          </button>
        )}
        {total > rows.length && (
          <span className="tabular-nums">
            {c.listing(rows.length, total)}
          </span>
        )}
        {h.hidden + (h.actions.length - rows.length) > 0 && (
          <span className="tabular-nums">
            {c.answered(h.hidden + (h.actions.length - rows.length))}
          </span>
        )}
        {unavailable.length > 0 && (
          <span>
            {c.notChecked(unavailable.join(", ").replace(/_/g, " "))}
            {unavailableHint === undefined ? (
              <>
                {" Run "}
                <code className="font-mono text-[0.85em]">repowise update</code>
                {" to include them."}
              </>
            ) : unavailableHint === null ? null : (
              <> {unavailableHint}</>
            )}
          </span>
        )}
      </div>

      <ActionDrawer
        action={opened}
        onClose={() => setOpened(null)}
        evidenceHref={opened ? hrefFor(opened) : null}
        fileHref={fileHref}
        onAnswer={
          onSetState && opened
            ? (state, message) => answer(opened, state, message)
            : undefined
        }
        loadPrompt={loadPrompt}
        showCliCommands={showCliCommands}
        LinkComponent={LinkComponent}
        renderTitle={renderActionTitle}
      />
      {promptModal}
    </OverviewSection>
  );
}

/**
 * `path:line` for an action whose title does not already name its file, so a
 * row like a Fix-first item ("Extract lines 60-122 of quick_repo_scan") says
 * where without opening it. The line is the first piece of evidence in that
 * file; titles that carry the path in backticks keep their own.
 */
export function actionLocation(action: NextAction): string | null {
  const { kind, path } = action.target;
  if ((kind !== "file" && kind !== "symbol") || !path) return null;
  if (action.title.includes(path)) return null;
  const line = action.details.find((d) => d.path === path && d.line)?.line;
  return line ? `${path}:${line}` : path;
}

/** One heading per tier in core's order, rank order kept inside each, as the
 *  CLI groups them. A tier this build does not know trails under its raw name
 *  rather than dropping its rows. */
export function groupByTier(actions: NextAction[]): [ActionTier, NextAction[]][] {
  const groups = new Map<ActionTier, NextAction[]>(ACTION_TIER_ORDER.map((t) => [t, []]));
  for (const a of actions) {
    const group = groups.get(a.tier);
    if (group) group.push(a);
    else groups.set(a.tier, [a]);
  }
  return [...groups].filter(([, items]) => items.length > 0);
}

export function ActionRow({
  action,
  onOpen,
  selected = false,
  onPrompt,
  onAnswer,
  copy,
}: {
  action: NextAction;
  /** Open the action's drawer; the whole row is the trigger. */
  onOpen: () => void;
  selected?: boolean | undefined;
  /** Open the agent prompt for this action. Omit to hide the row's prompt verb. */
  onPrompt?: (() => void) | undefined;
  onAnswer?: ((state: ActionStateValue, message: string) => void) | undefined;
  copy?: Partial<NextActionsCopy> | undefined;
}) {
  const c = { ...NEXT_ACTIONS_COPY, ...copy };
  const plainTitle = action.title.replace(/`/g, "");
  const location = actionLocation(action);
  const items = [
    { label: c.openDetails, icon: PanelRight, onSelect: onOpen },
    ...(onAnswer
      ? [
          {
            label: c.markDone,
            icon: Check,
            onSelect: () => onAnswer("done", c.markedDone),
          },
          {
            label: c.snooze,
            icon: BellOff,
            onSelect: () => onAnswer("snoozed", c.snoozed),
          },
          {
            label: c.dismiss,
            icon: X,
            onSelect: () => onAnswer("dismissed", c.dismissed),
          },
        ]
      : []),
  ];
  return (
    <li
      aria-label={c.openRow(plainTitle)}
      aria-current={selected ? "true" : undefined}
      data-selected={selected ? "true" : undefined}
      className={`${CLICKABLE_ROW_CLS} group group/row flex items-start gap-3 px-1 py-3.5 sm:gap-4 ${
        selected ? "bg-[var(--color-bg-elevated)]" : "hover:bg-[var(--color-bg-elevated)]"
      }`}
      {...clickableRowProps(onOpen)}
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          {action.tier === "act_now" && (
            <SeverityMark
              severity={action.severity}
              label={c.severity(action.severity)}
              className="translate-y-[-1px]"
            />
          )}
          <p className="min-w-0 text-[15px] font-semibold leading-snug text-[var(--color-text-primary)] [text-wrap:pretty] group-hover:text-[var(--color-accent-primary)]">
            {renderActionTitle(action.title)}
          </p>
        </div>
        {location ? (
          <p className="mt-0.5 font-mono text-xs text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
            {location}
          </p>
        ) : null}
        <p className="mt-1 max-w-[72ch] text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {renderActionTitle(action.impact)}
        </p>
        <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
          {action.why.map((w) => (
            <div key={w.label} className="flex min-w-0 items-baseline gap-1.5">
              <dt>{w.label}</dt>
              <dd
                className={`min-w-0 font-mono tabular-nums [overflow-wrap:anywhere] ${
                  w.basis === "unknown"
                    ? "text-[var(--color-text-tertiary)]"
                    : "text-[var(--color-text-secondary)]"
                }`}
              >
                {w.value}
                {w.basis === "inferred" && (
                  <span className="ml-1 font-sans text-[var(--color-text-tertiary)]">{c.inferred}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <span className="text-xs text-[var(--color-text-tertiary)]">
          <span className="hidden sm:inline">{c.effort(action.effort)}</span>
          {action.confidence === "medium" && (
            <>
              <span className="hidden sm:inline">, </span>
              <span>{c.worthCheck}</span>
            </>
          )}
        </span>
        {onPrompt && <RowPromptAction subject={plainTitle} onClick={onPrompt} />}
        <RowOverflow label={c.moreFor(plainTitle)} items={items} />
      </div>
    </li>
  );
}

function EmptyActions({ data, copy }: { data: ActionsResponse; copy: NextActionsCopy }) {
  const checked = data.rules.filter((r) => r.status === "evaluated").length;
  const skipped = data.rules.filter((r) => r.status === "not_applicable");
  return (
    <div className="flex flex-col gap-1 py-2 text-sm text-[var(--color-text-secondary)]">
      <p>{copy.emptyChecks(checked)}</p>
      {skipped.length > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {skipped.map((r) => r.reason).join(" ")}
        </p>
      )}
    </div>
  );
}
