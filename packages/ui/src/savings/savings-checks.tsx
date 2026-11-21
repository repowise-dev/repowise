"use client";

import * as React from "react";

import { AiPromptButton } from "../health/ai-prompt-button";
import { AGENT_PROMPT_LABEL } from "../health/ai-prompt-action";
import { formatNumber, type RelativeTimeWords } from "../lib/format";
import { RelativeTime } from "../shared/relative-time";
import { Button } from "../ui/button";
import { CHECK_PROMPTS, switchProfilePrompt } from "./check-prompts";
import type { SavingsCheckView } from "./types";

/** The check tools, in the order the server lists them. */
export const CHECK_TOOL_IDS = [
  "get_change_risk",
  "get_why",
  "get_risk",
  "get_health",
  "get_dead_code",
] as const;
export type CheckToolId = (typeof CHECK_TOOL_IDS)[number];

/** One connected agent and the tools its server lists, as the host resolved them. */
export interface CheckAgentView {
  id: string;
  name: string;
  /** The tool profile it is listed under; null when the tools are an exact list. */
  profile: string | null;
  tools: readonly string[];
  lastCallAt?: string | null;
}

export interface SavingsChecksCopy {
  title: string;
  sub: string;
  riskyLabel: string;
  riskySub: (checked: string) => string;
  decisionsLabel: string;
  decisionsSub: string;
  problemsLabel: string;
  problemsSub: string;
  /** Per tool: what the agent asked for, what the tool does, and the unit of a call. */
  tools: Record<CheckToolId, { name: string; does: string; unit: string }>;
  levels: { high: string; medium: string; low: string };
  changeHigh: (n: string) => string;
  unranked: (n: string) => string;
  whyAccepted: (n: string) => string;
  whyCandidate: (n: string) => string;
  whyNone: (n: string) => string;
  riskHigh: (n: string) => string;
  healthFindings: (n: string) => string;
  healthFiles: (n: string) => string;
  deadItems: (n: string) => string;
  deadHigh: (n: string) => string;
  links: Partial<Record<CheckToolId, string>>;
  notRecorded: string;
  notUsed: string;
  /** "Cursor doesn't have this tool." for one agent or several. */
  missingFor: (agents: string, count: number) => string;
  hasIt: (agent: string) => string;
  lacksIt: (agent: string) => string;
  addFor: (agent: string) => string;
  notListedTo: (agents: string) => string;
  addInSettings: string;
  /** "Cursor", "Cursor and Codex", "Cursor, Codex and Gemini". */
  agentList: (names: readonly string[]) => string;
  note: string;
  oldNote: string;
  empty: string;
  connect: string;
  agentsTitle: string;
  tradeoff: (coreCount: number, standardCount: number) => string;
  colAgent: string;
  colProfile: string;
  colTools: string;
  colLastCall: string;
  profilePill: (profile: string, count: number) => string;
  customProfile: string;
  noCalls: string;
  changeTools: string;
  changeHint: string;
  switchHint: string;
  /** Wording for "3d ago"; English when unset. */
  ago?: Partial<RelativeTimeWords>;
}

export interface SavingsChecksProps {
  /** `savings.checks`: one row per check tool. */
  rows: readonly SavingsCheckView[];
  /** Connected agents with their listed tools; undefined when the host cannot tell. */
  agents?: readonly CheckAgentView[] | undefined;
  /** Each profile's tools, for the tools table and the trade-off sentence. */
  profiles?: Partial<Record<string, readonly string[]>> | undefined;
  /** Existing places an outcome row links to. Never a new list of findings. */
  links?: Partial<Record<CheckToolId, string>> | undefined;
  /** Where tool profiles are changed (Agents & editors). */
  agentsHref?: string | undefined;
  /** Where an agent is connected, for the no-agent state. */
  connectHref?: string | undefined;
  copy: SavingsChecksCopy;
  LinkComponent?: React.ElementType | undefined;
}

const MICRO = "font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]";
const LINK = "text-xs text-[var(--color-accent-primary)] hover:underline";
const LEVEL_BG: Record<"high" | "medium" | "low", string> = {
  high: "bg-[var(--color-risk-high)]",
  medium: "bg-[var(--color-risk-medium)]",
  low: "bg-[var(--color-risk-low)]",
};

/** Whether any row recorded an outcome; the summary shows only then. */
export function checksHaveOutcomes(rows: readonly SavingsCheckView[]): boolean {
  return rows.some((row) => (row.outcome_calls ?? 0) > 0);
}

/** Which connected agents are listed *tool*, and which are not. */
export function toolAvailability(
  tool: string,
  agents: readonly CheckAgentView[],
): { with: CheckAgentView[]; without: CheckAgentView[] } {
  const listed = agents.filter((agent) => agent.tools.includes(tool));
  return { with: listed, without: agents.filter((agent) => !listed.includes(agent)) };
}

/**
 * Checks your agents ran: the check tools' calls and what Repowise returned,
 * and which tools each connected agent is listed. Props only. Counts come
 * from the server; nothing here ranks findings or adds a to-do list.
 */
export function SavingsChecks({
  rows,
  agents,
  profiles,
  links = {},
  agentsHref,
  connectHref,
  copy,
  LinkComponent,
}: SavingsChecksProps) {
  const A = LinkComponent ?? "a";
  const byTool = new Map(rows.map((row) => [row.tool, row]));
  const ordered = CHECK_TOOL_IDS.map(
    (tool) => byTool.get(tool) ?? ({ tool, calls: 0, outcome_calls: 0 } as SavingsCheckView),
  );
  const totalCalls = ordered.reduce((sum, row) => sum + (row.calls ?? 0), 0);
  // Used tools first; ties keep the server's order.
  const sorted = [...ordered].sort((a, b) => Number((b.calls ?? 0) > 0) - Number((a.calls ?? 0) > 0));

  const header = (
    <div className="border-b border-[var(--color-border-default)] pb-3">
      <h2 className="text-[18px] font-semibold text-[var(--color-text-primary)]">{copy.title}</h2>
      <p className="mt-0.5 max-w-[72ch] text-xs text-[var(--color-text-tertiary)]">{copy.sub}</p>
    </div>
  );

  // Only when the host knows no agent is connected; unknown keeps the rows.
  if (totalCalls === 0 && agents !== undefined && agents.length === 0) {
    return (
      <section className="mt-14">
        {header}
        <p className="mt-5 py-1 text-[15px] text-[var(--color-text-secondary)]">
          {copy.empty}{" "}
          {connectHref && (
            <A href={connectHref} className="text-[var(--color-accent-primary)] hover:underline">
              {copy.connect}
            </A>
          )}
        </p>
      </section>
    );
  }

  const withOutcomes = checksHaveOutcomes(ordered);
  const known = agents && agents.length > 0 ? agents : null;
  const firstAdd = known ? firstAddRow(sorted, known) : undefined;

  return (
    <section className="mt-14">
      {header}
      {withOutcomes && <ChecksSummary rows={ordered} copy={copy} />}
      {/* Nothing recorded an outcome yet: say so once, not on every row. */}
      {!withOutcomes && totalCalls > 0 && (
        <p className="mt-4 text-[15px] text-[var(--color-text-tertiary)]">{copy.notRecorded}</p>
      )}
      <ul className="m-0 mt-6 list-none p-0">
        {sorted.map((row) => (
          <CheckRow
            key={row.tool}
            row={row}
            agents={known}
            link={links[row.tool as CheckToolId]}
            addHref={row.tool === firstAdd ? agentsHref : undefined}
            sayNotRecorded={withOutcomes}
            copy={copy}
            A={A}
          />
        ))}
      </ul>
      <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
        {withOutcomes ? copy.note : copy.oldNote}
      </p>
      {known && <AgentTools agents={known} profiles={profiles} agentsHref={agentsHref} copy={copy} A={A} />}
    </section>
  );
}

/**
 * The row that carries the Agents & editors link: the first where a connected
 * agent lacks the tool. Later rows keep the quiet marks. An unused tool some
 * agent has carries a copy prompt instead, so it never takes the link.
 */
function firstAddRow(rows: readonly SavingsCheckView[], agents: readonly CheckAgentView[]): string | undefined {
  return rows.find((row) => {
    const avail = toolAvailability(row.tool, agents);
    return avail.without.length > 0 && ((row.calls ?? 0) > 0 || avail.with.length === 0);
  })?.tool;
}

/** One check tool: what it returned, or a quiet line on why it was not used. */
function CheckRow({
  row,
  agents,
  link,
  addHref,
  sayNotRecorded,
  copy,
  A,
}: {
  row: SavingsCheckView;
  agents: readonly CheckAgentView[] | null;
  link: string | undefined;
  addHref: string | undefined;
  /** Whether a row without outcomes says so itself (some other row has outcomes). */
  sayNotRecorded: boolean;
  copy: SavingsChecksCopy;
  A: React.ElementType;
}) {
  const tool = row.tool as CheckToolId;
  const words = copy.tools[tool];
  const calls = row.calls ?? 0;
  const avail = agents ? toolAvailability(tool, agents) : null;
  const missingEverywhere = calls === 0 && avail !== null && avail.with.length === 0;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-7 gap-y-2 border-b border-[var(--color-border-subtle)] py-4 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1.2fr)_6rem]">
      <div className="col-start-1 row-start-1 min-w-0">
        <b className="block text-[15px] font-medium text-[var(--color-text-primary)]">{words.name}</b>
        <code className="mt-0.5 block font-mono text-[11px] text-[var(--color-text-tertiary)]">{tool}</code>
      </div>
      <div className="col-span-2 col-start-1 row-start-2 min-w-0 text-[15px] text-[var(--color-text-primary)] md:col-span-1 md:col-start-2 md:row-start-1">
        {calls > 0 ? (
          <>
            {((row.outcome_calls ?? 0) > 0 || sayNotRecorded) && <Outcome row={row} copy={copy} />}
            {(row.outcome_calls ?? 0) > 0 && link && copy.links[tool] && (
              <div className="mt-1.5">
                <A href={link} className={LINK}>
                  {copy.links[tool]}
                </A>
              </div>
            )}
          </>
        ) : missingEverywhere ? (
          <div className="text-[var(--color-text-secondary)]">
            {copy.missingFor(copy.agentList(avail.without.map((a) => a.name)), avail.without.length)} {words.does}
          </div>
        ) : (
          <UnusedAsk tool={tool} does={words.does} copy={copy} />
        )}
        {avail && avail.without.length > 0 && (
          <Availability
            avail={avail}
            missingEverywhere={missingEverywhere}
            addHref={addHref}
            copy={copy}
            A={A}
          />
        )}
      </div>
      <div className="col-start-2 row-start-1 whitespace-nowrap text-right text-xs tabular-nums text-[var(--color-text-tertiary)] md:col-start-3">
        <b
          className={`block text-[15px] font-normal ${calls > 0 ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-tertiary)]"}`}
        >
          {formatNumber(calls)}
        </b>
        {words.unit}
      </div>
    </li>
  );
}

/** A listed tool nobody called yet: what it does, a sample ask and the copy-prompt control. */
function UnusedAsk({ tool, does, copy }: { tool: CheckToolId; does: string; copy: SavingsChecksCopy }) {
  return (
    <>
      <div className="text-[var(--color-text-secondary)]">
        {copy.notUsed} {does}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
        <q className="rounded-md bg-[var(--color-bg-inset)] px-2 py-1 text-xs text-[var(--color-text-primary)]">
          {CHECK_PROMPTS[tool]}
        </q>
        <AiPromptButton variant="ghost" label={AGENT_PROMPT_LABEL} copyText={() => CHECK_PROMPTS[tool]} />
      </div>
    </>
  );
}

function ChecksSummary({ rows, copy }: { rows: readonly SavingsCheckView[]; copy: SavingsChecksCopy }) {
  const get = (tool: CheckToolId) => rows.find((row) => row.tool === tool);
  const change = get("get_change_risk");
  const problems = (get("get_health")?.findings ?? 0) + (get("get_risk")?.high ?? 0);
  const recorded = (...tools: CheckToolId[]) => tools.some((tool) => (get(tool)?.outcome_calls ?? 0) > 0);
  // A tile shows only when its own tools recorded outcomes, never as "0 of 0".
  const tiles = [
    recorded("get_change_risk") && {
      label: copy.riskyLabel,
      value: change?.high ?? 0,
      sub: copy.riskySub(formatNumber((change?.high ?? 0) + (change?.medium ?? 0) + (change?.low ?? 0))),
    },
    recorded("get_why") && {
      label: copy.decisionsLabel,
      value: get("get_why")?.decisions_accepted ?? 0,
      sub: copy.decisionsSub,
    },
    recorded("get_health", "get_risk") && { label: copy.problemsLabel, value: problems, sub: copy.problemsSub },
  ].filter((tile): tile is { label: string; value: number; sub: string } => Boolean(tile));
  return (
    <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-3 md:gap-0">
      {tiles.map((tile, i) => (
        <div
          key={tile.label}
          className={`min-w-0 ${i > 0 ? "border-t border-[var(--color-border-default)] pt-3.5 md:border-l md:border-t-0 md:pl-7 md:pt-0" : ""} md:pr-7`}
        >
          <div className={MICRO}>{tile.label}</div>
          <div className="mb-0.5 mt-1.5 text-[22px] font-medium tracking-[-0.01em] tabular-nums text-[var(--color-text-primary)]">
            {formatNumber(tile.value)}
          </div>
          <p className="max-w-[36ch] text-xs text-[var(--color-text-secondary)]">{tile.sub}</p>
        </div>
      ))}
    </div>
  );
}

function Outcome({ row, copy }: { row: SavingsCheckView; copy: SavingsChecksCopy }) {
  const recorded = row.outcome_calls ?? 0;
  if (recorded === 0) return <div className="text-[var(--color-text-tertiary)]">{copy.notRecorded}</div>;
  const n = (value: number | null | undefined) => formatNumber(value ?? 0);
  const dim = (text: string) => <span className="text-[var(--color-text-tertiary)]"> · {text}</span>;
  switch (row.tool as CheckToolId) {
    case "get_change_risk": {
      const ranked = (row.high ?? 0) + (row.medium ?? 0) + (row.low ?? 0);
      return (
        <>
          {copy.changeHigh(n(row.high))}
          {recorded > ranked && dim(copy.unranked(n(recorded - ranked)))}
          <Levels row={row} copy={copy} />
        </>
      );
    }
    case "get_risk":
      return (
        <>
          {copy.riskHigh(n(row.high))}
          <Levels row={row} copy={copy} />
        </>
      );
    case "get_why":
      return (
        <>
          {copy.whyAccepted(n(row.decisions_accepted))}
          {(row.decisions_candidate ?? 0) > 0 && dim(copy.whyCandidate(n(row.decisions_candidate)))}
          {dim(copy.whyNone(n(row.decisions_none)))}
          <Share part={row.decisions_accepted ?? 0} whole={recorded} />
        </>
      );
    case "get_health":
      return (
        <>
          {copy.healthFindings(n(row.findings))}
          {dim(copy.healthFiles(n(row.files)))}
        </>
      );
    case "get_dead_code":
      return (
        <>
          {copy.deadItems(n(row.items))}
          {dim(copy.deadHigh(n(row.high_confidence)))}
        </>
      );
    default:
      return null;
  }
}

/** High, medium and low as a bar with each level named and counted beside it. */
function Levels({ row, copy }: { row: SavingsCheckView; copy: SavingsChecksCopy }) {
  const levels = (["high", "medium", "low"] as const).map((level) => ({ level, count: row[level] ?? 0 }));
  const total = levels.reduce((sum, l) => sum + l.count, 0);
  if (total === 0) return null;
  return (
    <>
      <div aria-hidden className="mt-2 flex h-1.5 max-w-[280px] overflow-hidden rounded-sm bg-[var(--color-border-subtle)]">
        {levels.map(({ level, count }) => (
          <i key={level} className={`block h-full ${LEVEL_BG[level]}`} style={{ width: `${(100 * count) / total}%` }} />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-3 font-mono text-[11px] text-[var(--color-text-tertiary)]">
        {levels.map(({ level, count }) => (
          <span key={level} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${LEVEL_BG[level]}`} />
            {copy.levels[level]} {formatNumber(count)}
          </span>
        ))}
      </div>
    </>
  );
}

function Share({ part, whole }: { part: number; whole: number }) {
  if (whole <= 0) return null;
  return (
    <div aria-hidden className="mt-2 h-[3px] max-w-[280px] overflow-hidden rounded-sm bg-[var(--color-border-subtle)]">
      <i className="block h-full bg-[var(--color-text-tertiary)] opacity-60" style={{ width: `${(100 * part) / whole}%` }} />
    </div>
  );
}

function Availability({
  avail,
  missingEverywhere,
  addHref,
  copy,
  A,
}: {
  avail: { with: CheckAgentView[]; without: CheckAgentView[] };
  missingEverywhere: boolean;
  addHref: string | undefined;
  copy: SavingsChecksCopy;
  A: React.ElementType;
}) {
  const dot = "inline-flex items-center gap-1.5 before:inline-block before:h-1.5 before:w-1.5 before:rounded-full before:border";
  const missing = copy.agentList(avail.without.map((a) => a.name));
  return (
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs first:mt-0.5 text-[var(--color-text-tertiary)]">
      {missingEverywhere ? (
        <span className={`${dot} before:border-[var(--color-text-tertiary)]`}>{copy.notListedTo(missing)}</span>
      ) : (
        <>
          {avail.with.map((agent) => (
            <span
              key={agent.id}
              className={`${dot} before:border-[var(--color-text-secondary)] before:bg-[var(--color-text-secondary)]`}
            >
              {copy.hasIt(agent.name)}
            </span>
          ))}
          {avail.without.map((agent) => (
            <span key={agent.id} className={`${dot} before:border-[var(--color-text-tertiary)]`}>
              {copy.lacksIt(agent.name)}
            </span>
          ))}
        </>
      )}
      {addHref && (
        <A href={addHref} className={LINK}>
          {missingEverywhere ? copy.addInSettings : copy.addFor(missing)}
        </A>
      )}
    </div>
  );
}

function AgentTools({
  agents,
  profiles,
  agentsHref,
  copy,
  A,
}: {
  agents: readonly CheckAgentView[];
  profiles: Partial<Record<string, readonly string[]>> | undefined;
  agentsHref: string | undefined;
  copy: SavingsChecksCopy;
  A: React.ElementType;
}) {
  const standard = profiles?.standard ?? [];
  // Every tool any agent is listed, in the standard profile's order first.
  const universe = [...new Set([...standard, ...agents.flatMap((agent) => agent.tools)])];
  const lacking = agents.find((agent) => CHECK_TOOL_IDS.some((tool) => !agent.tools.includes(tool)));
  return (
    <div className="mt-9">
      <h3 className="text-[15px] font-semibold text-[var(--color-text-primary)]">{copy.agentsTitle}</h3>
      <p className="mt-1 max-w-[78ch] text-xs text-[var(--color-text-secondary)]">
        {copy.tradeoff(profiles?.core?.length ?? 0, standard.length)}
      </p>
      <div className="mt-2.5 overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse">
          <thead>
            <tr>
              {[copy.colAgent, copy.colProfile, copy.colTools, copy.colLastCall].map((label) => (
                <th
                  key={label}
                  className="whitespace-nowrap border-b border-[var(--color-border-default)] py-2.5 pr-4 text-left font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => (
              <tr key={agent.id} className="align-top">
                <td className="border-b border-[var(--color-border-subtle)] py-3 pr-4 text-[15px] text-[var(--color-text-primary)]">
                  {agent.name}
                </td>
                <td className="border-b border-[var(--color-border-subtle)] py-3 pr-4">
                  <span className="inline-block whitespace-nowrap rounded-full border border-[var(--color-border-default)] px-2 font-mono text-[11px] font-medium text-[var(--color-text-secondary)]">
                    {copy.profilePill(agent.profile ?? copy.customProfile, agent.tools.length)}
                  </span>
                </td>
                <td className="border-b border-[var(--color-border-subtle)] py-3 pr-4">
                  <div className="flex flex-wrap gap-x-1.5 gap-y-1">
                    {universe.map((tool) => {
                      const on = agent.tools.includes(tool);
                      return (
                        <span
                          key={tool}
                          className={`rounded px-1.5 font-mono text-[11px] ${on ? "bg-[var(--color-bg-inset)] text-[var(--color-text-primary)]" : "bg-[var(--color-border-subtle)] text-[var(--color-text-tertiary)]"}`}
                        >
                          {tool}
                        </span>
                      );
                    })}
                  </div>
                </td>
                <td className="whitespace-nowrap border-b border-[var(--color-border-subtle)] py-3 pr-4 text-[15px] tabular-nums text-[var(--color-text-tertiary)]">
                  <RelativeTime date={agent.lastCallAt} words={copy.ago} fallback={copy.noCalls} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3.5 flex flex-wrap items-center gap-x-4 gap-y-2.5 text-xs text-[var(--color-text-tertiary)]">
        {agentsHref && (
          <Button asChild size="sm">
            <A href={agentsHref}>{copy.changeTools}</A>
          </Button>
        )}
        {lacking && (
          <AiPromptButton
            variant="ghost"
            label={AGENT_PROMPT_LABEL}
            copyText={() => switchProfilePrompt(lacking.name)}
          />
        )}
        <span>{lacking ? copy.switchHint : copy.changeHint}</span>
      </div>
    </div>
  );
}
