"use client";

import { Fragment, useMemo, useState, type ElementType, type ReactNode } from "react";
import { BellOff, Check, PanelRight, X } from "lucide-react";
import { toast } from "sonner";
import type {
  ActionHorizonKey,
  ActionsResponse,
  ActionStateValue,
  ActionTier,
  NextAction,
} from "@repowise-dev/types/actions";

import { EFFORT_LABEL } from "../health/labels";
import { SeverityMark } from "../health/severity-mark";
import { CLICKABLE_ROW_CLS, clickableRowProps } from "../shared/responsive-table";
import { RowOverflow } from "../shared/row-overflow";
import { Segmented } from "../shared/segmented";
import { ActionDrawer, type ActionDrawerProps } from "./action-drawer";
import { OverviewSection } from "./section";

/** Rows shown before "Show all"; the response carries up to 20 per horizon. */
const PREVIEW = 5;

const TIER_HEADING: Record<ActionTier, string> = {
  act_now: "Now",
  plan: "Worth planning",
  improve_signal: "Improve what Repowise can see",
};

export interface NextActionsProps {
  /** `null` when the server predates actions; the section then renders nothing. */
  data: ActionsResponse | null;
  hrefFor: (action: NextAction) => string | null;
  /** A file's own page, for the files an opened action names. */
  fileHref?: ((path: string) => string | null) | undefined;
  /** Persist a dismissal, snooze, done, or (null) an undo. Omit to hide those verbs. */
  onSetState?: (action: NextAction, state: ActionStateValue | null) => Promise<void>;
  /** An action's agent prompt as core renders it. Omit to hide the prompt. */
  loadPrompt?: ActionDrawerProps["loadPrompt"];
  LinkComponent?: ElementType | undefined;
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

function plural(n: number, noun: string): string {
  return `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;
}

function formatDay(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * The one sentence that says where things stand. Counts the list as the reader
 * sees it, and names the window, because "this week" means the repository's
 * last week of commits, which is not always the calendar's.
 */
export function actionsStatus(
  data: ActionsResponse,
  horizon: ActionHorizonKey,
  answered: ReadonlySet<string> = new Set(),
): string {
  const h = data.horizons[horizon];
  const gone = (tier: ActionTier) =>
    h.actions.filter((a) => a.tier === tier && answered.has(a.id)).length;
  const now = (h.by_tier.act_now ?? 0) - gone("act_now");
  const work = now + (h.by_tier.plan ?? 0) - gone("plan");
  const until = formatDay(data.anchor);
  const window =
    horizon === "week"
      ? until
        ? `in the week to ${until}, the last indexed commit`
        : "this week"
      : "this quarter";
  if (work === 0) {
    const other = horizon === "week" ? data.horizons.quarter : data.horizons.week;
    const otherWork = (other.by_tier.act_now ?? 0) + (other.by_tier.plan ?? 0);
    return otherWork > 0
      ? `Nothing needs you ${window}. ${plural(otherWork, "thing")} ${
          horizon === "week"
            ? `${otherWork === 1 ? "is" : "are"} worth planning this quarter`
            : "came up this week"
        }.`
      : `Nothing stands out ${window}.`;
  }
  return `${plural(work, "thing")} worth doing ${window}${now ? `, ${now} of them now` : ""}.`;
}

export function NextActions({
  data,
  hrefFor,
  fileHref,
  onSetState,
  loadPrompt,
  LinkComponent,
}: NextActionsProps) {
  // Open on the week unless it holds no work and the quarter does: a lone
  // "add a coverage report" is not a reason to show an empty week first.
  const work = (key: ActionHorizonKey, less: ReadonlySet<string> = new Set()) =>
    data
      ? (data.horizons[key].by_tier.act_now ?? 0) +
        (data.horizons[key].by_tier.plan ?? 0) -
        data.horizons[key].actions.filter((a) => a.tier !== "improve_signal" && less.has(a.id))
          .length
      : 0;
  const initial: ActionHorizonKey = work("week") === 0 && work("quarter") > 0 ? "quarter" : "week";
  const [horizon, setHorizon] = useState<ActionHorizonKey>(initial);
  const [expanded, setExpanded] = useState(false);
  // Optimistic: a row the person answered leaves at once and comes back if the
  // write fails.
  const [answered, setAnswered] = useState<ReadonlySet<string>>(new Set());
  const [opened, setOpened] = useState<NextAction | null>(null);

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
    setAnswered((s) => new Set(s).add(action.id));
    const restore = () =>
      setAnswered((s) => {
        const next = new Set(s);
        next.delete(action.id);
        return next;
      });
    onSetState(action, state).then(
      () =>
        toast.success(message, {
          action: {
            label: "Undo",
            onClick: () => {
              restore();
              void onSetState(action, null).catch(() => toast.error("Could not undo"));
            },
          },
        }),
      () => {
        restore();
        toast.error("Could not save that; the action is back in the list");
      },
    );
  };


  return (
    <OverviewSection
      title="Do next"
      description={actionsStatus(data, horizon, answered)}
      action={
        <Segmented
          label="Time frame"
          value={horizon}
          options={[
            { value: "week", label: "This week", count: String(work("week", answered)), hint: "What the last 7 days of commits changed" },
            { value: "quarter", label: "This quarter", count: String(work("quarter", answered)), hint: "What the last 90 days say is worth planning" },
          ]}
          onChange={(v) => {
            setHorizon(v);
            setExpanded(false);
          }}
        />
      }
    >
      {shown.length === 0 && answered.size > 0 && h.actions.length > 0 ? (
        <p className="py-2 text-sm text-[var(--color-text-secondary)]">
          You have answered everything listed here. Answers are kept until the facts change.
        </p>
      ) : shown.length === 0 ? (
        <EmptyActions data={data} />
      ) : (
        <div className="flex flex-col">
          {groupByTier(shown).map(([tier, items]) => (
            <div key={tier} className="flex flex-col">
              <h3 className="pb-1 pt-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] first:pt-0">
                {TIER_HEADING[tier]}
              </h3>
              <ul className="flex flex-col divide-y divide-[var(--color-border-default)]">
                {items.map((action) => (
                  <ActionRow
                    key={action.id}
                    action={action}
                    selected={opened?.id === action.id}
                    onOpen={() => setOpened(action)}
                    onAnswer={onSetState ? (state, message) => answer(action, state, message) : undefined}
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
            className="rounded text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            {expanded ? "Show fewer" : `Show all ${Math.min(rows.length, total).toLocaleString()}`}
          </button>
        )}
        {total > rows.length && (
          <span className="tabular-nums">
            {`Listing ${rows.length.toLocaleString()} of ${total.toLocaleString()}.`}
          </span>
        )}
        {h.hidden + (h.actions.length - rows.length) > 0 && (
          <span className="tabular-nums">{`${plural(
            h.hidden + (h.actions.length - rows.length),
            "action",
          )} answered (done, snoozed or dismissed).`}</span>
        )}
        {unavailable.length > 0 && (
          <span>
            {`Not checked: ${unavailable.join(", ").replace(/_/g, " ")}. Run `}
            <code className="font-mono text-[0.85em]">repowise update</code>
            {" to include them."}
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
        LinkComponent={LinkComponent}
        renderTitle={renderActionTitle}
      />
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

function groupByTier(actions: NextAction[]): [ActionTier, NextAction[]][] {
  const out: [ActionTier, NextAction[]][] = [];
  for (const a of actions) {
    const last = out[out.length - 1];
    if (last && last[0] === a.tier) last[1].push(a);
    else out.push([a.tier, [a]]);
  }
  return out;
}

export function ActionRow({
  action,
  onOpen,
  selected = false,
  onAnswer,
}: {
  action: NextAction;
  /** Open the action's drawer; the whole row is the trigger. */
  onOpen: () => void;
  selected?: boolean | undefined;
  onAnswer?: ((state: ActionStateValue, message: string) => void) | undefined;
}) {
  const plainTitle = action.title.replace(/`/g, "");
  const location = actionLocation(action);
  const items = [
    { label: "Open details", icon: PanelRight, onSelect: onOpen },
    ...(onAnswer
      ? [
          {
            label: "Mark done",
            icon: Check,
            onSelect: () => onAnswer("done", "Marked done. It returns if the facts change."),
          },
          {
            label: "Snooze for 14 days",
            icon: BellOff,
            onSelect: () => onAnswer("snoozed", "Snoozed for 14 days"),
          },
          {
            label: "Dismiss",
            icon: X,
            onSelect: () => onAnswer("dismissed", "Dismissed. It returns if the facts change."),
          },
        ]
      : []),
  ];
  return (
    <li
      aria-label={`Open ${plainTitle}`}
      aria-current={selected ? "true" : undefined}
      className={`${CLICKABLE_ROW_CLS} group flex items-start gap-3 px-1 py-3.5 sm:gap-4 ${
        selected ? "bg-[var(--color-bg-elevated)]" : "hover:bg-[var(--color-bg-elevated)]"
      }`}
      {...clickableRowProps(onOpen)}
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          {action.tier === "act_now" && (
            <SeverityMark severity={action.severity} className="translate-y-[-1px]" />
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
                  <span className="ml-1 font-sans text-[var(--color-text-tertiary)]">(inferred)</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <span className="text-xs text-[var(--color-text-tertiary)]">
          <span className="hidden sm:inline">{`${EFFORT_LABEL[action.effort]} effort`}</span>
          {action.confidence === "medium" && (
            <>
              <span className="hidden sm:inline">, </span>
              <span>worth a check</span>
            </>
          )}
        </span>
        <RowOverflow label={`More for ${plainTitle}`} items={items} />
      </div>
    </li>
  );
}

function EmptyActions({ data }: { data: ActionsResponse }) {
  const checked = data.rules.filter((r) => r.status === "evaluated").length;
  const skipped = data.rules.filter((r) => r.status === "not_applicable");
  return (
    <div className="flex flex-col gap-1 py-2 text-sm text-[var(--color-text-secondary)]">
      <p>{`Repowise ran ${plural(checked, "check")} over this index and none of them produced work for this time frame.`}</p>
      {skipped.length > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {skipped.map((r) => r.reason).join(" ")}
        </p>
      )}
    </div>
  );
}
