"use client";

import * as React from "react";

import { ViewTabs, type ViewTab } from "../shared/view-tabs";
import { SavingsSourceTable, type SourceRow } from "./savings-source-table";
import { SavingsTimeline } from "./savings-timeline";
import { agentLabel, modelLabel, type SavingsView } from "./types";

export type UsageDetailTab = "day" | "operation" | "model" | "agent";

export interface UsageDetailsProps {
  data: SavingsView;
  /** Controlled selection. Omit both and the component keeps its own. */
  value?: UsageDetailTab | undefined;
  onValueChange?: ((tab: UsageDetailTab) => void) | undefined;
}

/**
 * The same savings, cut four ways.
 *
 * One tab row on one axis: each tab selects a *dimension of the same
 * dataset*, never a different scope or window. The page's window control is
 * separate and stays separate, which is the rule that keeps a reader from
 * having two controls that both look like they narrow the result.
 *
 * A dimension with no rows still gets a tab, and says what would populate it
 * once opened. Hiding it would make the vocabulary depend on the data and
 * leave a reader unsure whether "by model" exists at all.
 */
export function UsageDetails({ data, value, onValueChange }: UsageDetailsProps) {
  const [internal, setInternal] = React.useState<UsageDetailTab>("day");
  // Controlled by the presence of `value`, not of `onValueChange`. Keying on
  // the callback froze the tabs for a host that only wanted to observe the
  // selection: every click notified and nothing moved.
  const controlled = value !== undefined;
  const active = controlled ? value : internal;
  const select = React.useCallback(
    (id: string) => {
      const tab = id as UsageDetailTab;
      if (!controlled) setInternal(tab);
      // Fires in both modes: an uncontrolled caller may still want to know.
      onValueChange?.(tab);
    },
    [controlled, onValueChange],
  );

  const total = data.saved_input_tokens;

  const operations = toRows(data.per_operation, (g) => g ?? "Unknown");
  const models = toRows(data.per_model, modelLabel);
  const agents: SourceRow[] = data.per_agent.map((row) => ({
    label: agentLabel(row),
    events: row.events,
    savedInputTokens: row.saved_input_tokens,
  }));

  return (
    <ViewTabs
      tabs={[
        { id: "day", label: "By day" },
        tab("operation", "Operations", operations.length),
        tab("model", "Models", models.length),
        tab("agent", "Agents", agents.length),
      ]}
      value={active}
      onValueChange={select}
    >
      {active === "day" && <SavingsTimeline days={data.per_day} />}

      {active === "operation" && (
        <SavingsSourceTable
          rows={operations}
          total={total}
          nameHeader="Operation"
          caption="Savings by the operation that produced them"
          empty={<Empty subject="operation" />}
        />
      )}

      {active === "model" && (
        <SavingsSourceTable
          rows={models}
          total={total}
          nameHeader="Model"
          caption="Savings by the pricing model recorded on each event"
          empty={<Empty subject="model" />}
        />
      )}

      {active === "agent" && (
        <SavingsSourceTable
          rows={agents}
          total={total}
          nameHeader="Agent"
          caption="Savings by the agent that made the call"
          empty={<Empty subject="agent" />}
        />
      )}
    </ViewTabs>
  );
}

/**
 * One tab, carrying its row count only when it has rows.
 *
 * A zero badge is worse than no badge: it reads as a count the tab is
 * reporting rather than as an empty dimension, and the design language says a
 * count belongs on a tab only when it is meaningful before the click.
 */
function tab(id: UsageDetailTab, label: string, count: number): ViewTab {
  return count > 0 ? { id, label, badge: count } : { id, label };
}

function toRows(
  rows: SavingsView["per_operation"],
  label: (group: string | null) => string,
): SourceRow[] {
  return rows.map((row) => ({
    label: label(row.group),
    events: row.events,
    savedInputTokens: row.saved_input_tokens,
  }));
}

/** Says what would populate the view rather than rendering an empty table. */
function Empty({ subject }: { subject: string }) {
  return (
    <p className="py-2 text-sm text-[var(--color-text-secondary)]">
      No savings in this window carry a {subject}.
    </p>
  );
}
