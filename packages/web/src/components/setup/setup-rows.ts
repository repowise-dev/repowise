/**
 * The Setup page's rows, derived from one `SetupResponse`. Pure, so the page,
 * the sidebar pill and the settings groups all read the same answer.
 *
 * Copy is returned as message keys under `setup.row` (or as server text for
 * doctor checks); the agent prompt is English, since it is written for the
 * agent, not the reader.
 */

import type { SetupPromptRow } from "@repowise-dev/ui/health/ai-prompt-builder";
import type { SetupAgent } from "@repowise-dev/types/generated/http";
import type { SetupResponse } from "@/lib/api/setup";

export type SetupMsg =
  | { key: string; values?: Record<string, string | number> }
  | { text: string };

export type SetupTone = "done" | "waiting" | "attention" | "off" | "found" | "model";

export type SetupHook = "post-commit" | "rewrite";

export type SetupAction =
  | { kind: "connect"; agentId: string; agentName: string }
  | { kind: "disconnect"; agentId: string; agentName: string }
  | { kind: "hook"; hook: SetupHook; enabled: boolean; unavailable?: boolean }
  | { kind: "coverage"; path: string }
  | { kind: "model"; hasModel: boolean }
  /** Needs attention: the prompt is the primary action. */
  | { kind: "prompt" };

export interface SetupRow {
  id: string;
  tone: SetupTone;
  status: SetupMsg;
  title: SetupMsg;
  detail: SetupMsg;
  action: SetupAction | null;
  command: string;
  prompt: SetupPromptRow;
  /** Paths the row can list on request, e.g. files that failed to parse. */
  files?: string[];
}

export interface SetupView {
  attention: SetupRow[];
  /** Doctor could not run, so an empty "Needs attention" proves nothing. */
  checksUnreadable: boolean;
  essentials: SetupRow[];
  optional: SetupRow[];
  /** Index up to date, an agent has called, sync on. */
  doneCount: number;
  allDone: boolean;
}

export interface DeriveOptions {
  repoName: string;
  /** Epoch ms; injected so the 24h rule is testable. */
  now: number;
  /** "4 minutes ago" for an ISO time, in the reader's locale. */
  relative: (iso: string) => string;
  /** When this browser first saw each agent connected, by agent id. */
  agentSeenAt?: Record<string, string>;
}

export const ESSENTIALS_TOTAL = 3;

const DAY_MS = 24 * 60 * 60 * 1000;

/** Doctor rows another Setup row already shows. */
const CHECKS_SHOWN_AS_ROWS = new Set(["post-commit-hook"]);

const msg = (key: string, values?: Record<string, string | number>): SetupMsg =>
  values ? { key, values } : { key };

/** A section the server could not read: say so, never show its empty state. */
const unreadable = (command: string): Pick<SetupRow, "tone" | "status" | "detail"> => ({
  tone: "off",
  status: msg("unreadable.status"),
  detail: msg("unreadable.detail", { command }),
});

const shortSha = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : "?");

// -- index -------------------------------------------------------------------

function indexBehindRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const values = { indexed: shortSha(s.index.indexed_commit), live: shortSha(s.index.live_head) };
  return {
    id: "index-behind",
    tone: "attention",
    status: msg("index.statusBehind"),
    title: msg("index.title"),
    detail: msg("index.detailBehind", values),
    action: { kind: "prompt" },
    command: "repowise update",
    prompt: {
      repoName: o.repoName,
      title: "Index is behind",
      state: `The index is at commit ${values.indexed}, and HEAD has moved on to ${values.live}.`,
      command: "repowise update",
      verify: "status",
      fix: true,
    },
  };
}

function indexParseRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const count = s.index.parse_error_count ?? 0;
  const files = s.index.parse_error_files ?? [];
  return {
    id: "index-parse",
    tone: "attention",
    status: msg("index.statusSkipped", { count }),
    title: msg("index.title"),
    detail: msg("index.detailSkipped", { count }),
    action: { kind: "prompt" },
    command: "repowise doctor --format json",
    files,
    prompt: {
      repoName: o.repoName,
      title: "Files that could not be parsed",
      state: `repowise could not parse ${count} file${count === 1 ? "" : "s"} while indexing, so their symbols are missing from the graph. For each file, tell me whether it should be excluded (exclude_patterns in .repowise/config.yaml) or whether its syntax needs a fix.`,
      verify: "doctor",
      fix: true,
      items: files,
    },
  };
}

function indexRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const incomplete = s.index.state === "incomplete";
  const command = incomplete ? "repowise init" : "repowise status";
  return {
    id: "index",
    tone: incomplete ? "off" : "done",
    status: msg(incomplete ? "index.statusIncomplete" : "index.statusUpToDate"),
    title: msg("index.title"),
    detail: incomplete
      ? msg("index.detailIncomplete")
      : msg("index.detailUpToDate", {
          files: s.index.file_count ?? 0,
          commit: shortSha(s.index.indexed_commit),
        }),
    action: null,
    command,
    prompt: {
      repoName: o.repoName,
      title: "Index",
      state: incomplete
        ? "There is no complete index for this repository yet."
        : `The index is up to date at commit ${shortSha(s.index.indexed_commit)}.`,
      command,
      verify: "status",
      fix: incomplete,
    },
  };
}

// -- agent -------------------------------------------------------------------

/** The agent that called first, or null when no call names one. */
function firstCaller(agents: SetupAgent[]): SetupAgent | null {
  const called = agents.filter((a) => a.first_call_at);
  called.sort((a, b) => String(a.first_call_at).localeCompare(String(b.first_call_at)));
  return called[0] ?? null;
}

function connectCommand(agentId: string | null): string {
  return agentId ? `repowise agents add --target=${agentId}` : "repowise agents add";
}

/** Registered agents that have not called for a day since they connected. */
function silentAgents(s: SetupResponse, o: DeriveOptions): SetupAgent[] {
  if (!s.calls_available || s.calls.first_call_at || s.calls.unattributed) return [];
  // The API has no connect time, so "since" is when this browser first saw
  // the agent connected, else when the repo was indexed (init registers
  // agents then). Upgrade path: a registration time in the setup payload.
  return (s.agents ?? []).filter((a) => {
    if (!a.connected || a.first_call_at) return false;
    const since = o.agentSeenAt?.[a.id] ?? s.index.indexed_at;
    return since ? o.now - Date.parse(since) >= DAY_MS : false;
  });
}

function agentSilentRow(agent: SetupAgent, o: DeriveOptions): SetupRow {
  const command = "repowise doctor --format json";
  return {
    id: `agent-silent-${agent.id}`,
    tone: "attention",
    status: msg("agent.statusNoCalls"),
    title: msg("agent.title"),
    detail: msg("agent.detailNoCalls", { agent: agent.display_name, repo: o.repoName }),
    action: { kind: "prompt" },
    command,
    prompt: {
      repoName: o.repoName,
      title: "Coding agent has not reached the index",
      state: `${agent.display_name} is registered but has not called this repository's index in the day since it connected. It may have opened a different folder: if so, open ${o.repoName} in it, or run \`repowise init\` in the folder it opened.`,
      verify: "doctor",
      fix: true,
    },
  };
}

function agentRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const agents = s.agents ?? [];
  const connected = agents.filter((a) => a.connected);
  const offer = agents.find((a) => a.installed && !a.connected) ?? null;
  const connectAction: SetupAction | null = offer
    ? { kind: "connect", agentId: offer.id, agentName: offer.display_name }
    : null;
  const base = { id: "agent", title: msg("agent.title"), command: connectCommand(offer?.id ?? null) };
  const prompt = (state: string, fix = false): SetupPromptRow => ({
    repoName: o.repoName,
    title: "Coding agent",
    state,
    command: base.command,
    verify: "doctor",
    fix,
  });

  if (!s.agents_available) {
    return {
      ...base,
      ...unreadable("repowise agents"),
      action: null,
      prompt: prompt("repowise could not list the coding agents on this machine.", true),
    };
  }
  if (!s.calls_available && connected.length) {
    // Calls unreadable: a registered agent is not counted as failing.
    return {
      ...base,
      command: "repowise status",
      tone: "done",
      status: msg("agent.statusConnected"),
      detail: msg("unreadable.detail", { command: "repowise status" }),
      action: null,
      prompt: { ...prompt("An agent is registered; its calls could not be read here."), command: "repowise status", verify: "status" },
    };
  }
  if (s.calls.first_call_at) {
    const caller = firstCaller(agents);
    const when = o.relative(s.calls.first_call_at);
    return {
      ...base,
      command: "repowise status",
      tone: "done",
      status: msg("agent.statusConnected"),
      detail: caller
        ? msg("agent.detailCalled", { agent: caller.display_name, when })
        : msg("agent.detailCalledAnon", { when }),
      action: null,
      prompt: { ...prompt("An agent has called this repository's index."), command: "repowise status", verify: "status" },
    };
  }
  if (connected.length) {
    const names = connected.map((a) => a.display_name).join(", ");
    return {
      ...base,
      tone: "waiting",
      status: msg("agent.statusWaiting"),
      detail: offer
        ? msg("agent.detailWaitingOther", { agents: names, count: connected.length, other: offer.display_name })
        : msg("agent.detailWaiting", { agents: names, count: connected.length }),
      action: connectAction,
      prompt: prompt(`${names} is registered and has not called the index yet.`),
    };
  }
  let detail = msg("agent.detailNone");
  if (offer) detail = msg("agent.detailInstalled", { agent: offer.display_name });
  return {
    ...base,
    tone: "off",
    status: msg("agent.statusOff"),
    detail,
    action: connectAction,
    prompt: prompt(
      offer
        ? `${offer.display_name} is installed but not connected to repowise.`
        : "No coding agent is connected to repowise.",
    ),
  };
}

// -- sync, model, coverage, rewrite -------------------------------------------

function syncRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const installed = s.sync.installed;
  const word = installed === true ? "On" : installed === false ? "Off" : "Unknown";
  return {
    id: "sync",
    tone: installed ? "done" : "off",
    status: msg(`sync.status${word}`),
    title: msg("sync.title"),
    detail: msg(`sync.detail${word}`),
    action: { kind: "hook", hook: "post-commit", enabled: installed === true },
    command: "repowise hook install",
    prompt: {
      repoName: o.repoName,
      title: "Keep the index in sync",
      state: installed
        ? "The post-commit hook is installed, so the index updates after each commit."
        : "The post-commit hook is not installed, so the index updates only when `repowise update` runs.",
      command: "repowise hook install",
      verify: "doctor",
    },
  };
}

function modelRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const llm = s.model.docs_mode === "llm";
  const providers = s.model.configured_providers ?? [];
  let detail = msg("model.detailNone");
  if (s.model.active_provider) detail = msg("model.detailActive", { provider: s.model.active_provider });
  else if (providers.length) detail = msg("model.detailReady", { providers: providers.join(", ") });
  if (!s.model_available) detail = msg("unreadable.detail", { command: "repowise status" });
  let status = msg(llm ? "model.statusLlm" : "model.statusBuilt");
  // Provider settings unreadable: only a written-by-model state still holds.
  if (!s.model_available && !llm) status = msg("unreadable.status");
  return {
    id: "model",
    tone: "model",
    status,
    title: msg("model.title"),
    detail,
    action: { kind: "model", hasModel: llm },
    command: "repowise generate",
    prompt: {
      repoName: o.repoName,
      title: "A model for docs, chat and answers",
      state: llm
        ? `The docs are written by a model${s.model.active_provider ? ` (${s.model.active_provider})` : ""}.`
        : "The docs are built from the index, without a model. Pick a model source available on this machine (a signed-in agent CLI, an API key or a local model) and write the docs with it.",
      command: "repowise generate",
      verify: "status",
    },
  };
}

function coverageRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const stored = s.coverage.ingested_at;
  const report = (s.coverage.discovered ?? [])[0];
  const command = `repowise coverage add ${report?.path ?? "<report>"}`;
  const prompt: SetupPromptRow = {
    repoName: o.repoName,
    title: "Test coverage",
    state: "",
    command,
    verify: "status",
  };
  if (!s.coverage_available) {
    return {
      id: "coverage",
      ...unreadable("repowise coverage status"),
      title: msg("coverage.title"),
      action: null,
      command: "repowise coverage status",
      prompt: { ...prompt, command: "repowise coverage status", state: "repowise could not read the stored coverage or search for reports here." },
    };
  }
  if (stored) {
    return {
      id: "coverage",
      tone: "done",
      status: msg("coverage.statusOn"),
      title: msg("coverage.title"),
      detail: msg("coverage.detailOn", { when: o.relative(stored) }),
      action: report ? { kind: "coverage", path: report.path } : null,
      command,
      prompt: { ...prompt, state: "Coverage is stored for this repository. Refresh it from the newest report." },
    };
  }
  if (report) {
    return {
      id: "coverage",
      tone: "found",
      status: msg("coverage.statusFound"),
      title: msg("coverage.title"),
      detail: msg("coverage.detailFound", { path: report.path, age: report.age_days }),
      action: { kind: "coverage", path: report.path },
      command,
      prompt: { ...prompt, state: `A coverage report was found at ${report.path}, ${report.age_days} days old.` },
    };
  }
  return {
    id: "coverage",
    tone: "off",
    status: msg("coverage.statusOff"),
    title: msg("coverage.title"),
    detail: msg("coverage.detailNone"),
    action: null,
    command,
    prompt: {
      ...prompt,
      state: "No coverage report was found. Run the test suite with coverage (lcov, Cobertura or coverage.py JSON), then add the report it writes.",
    },
  };
}

const REWRITE_WORD = {
  installed: "On",
  not_installed: "Off",
  not_applicable: "Na",
  unknown: "Unknown",
} as const;

function rewriteRow(s: SetupResponse, o: DeriveOptions): SetupRow {
  const state = s.rewrite_hook.state;
  const on = state === "installed";
  return {
    id: "rewrite",
    tone: on ? "done" : "off",
    status: msg(`rewrite.status${REWRITE_WORD[state]}`),
    title: msg("rewrite.title"),
    detail: msg(state === "not_applicable" ? "rewrite.detailNa" : "rewrite.detail"),
    action: { kind: "hook", hook: "rewrite", enabled: on, unavailable: state === "not_applicable" },
    command: "repowise hook rewrite install",
    prompt: {
      repoName: o.repoName,
      title: "Shorter command output",
      state: on
        ? "The distill rewrite hook is installed, so Claude Code reads test, build and git output through distill."
        : "The distill rewrite hook is not installed. With it, Claude Code reads test, build and git output through distill, which keeps errors and drops noise.",
      command: "repowise hook rewrite install",
      verify: "doctor",
    },
  };
}

/** A trailing "(run X)" or "(run: X)" in doctor text, with X captured. */
const RUN_HINT = /\s*\(run:?\s+`?([^`)]+?)`?\)\s*\.?\s*$/;

/** Doctor text without a closing "(run X)" that repeats the row's command. */
export function withoutRunHint(detail: string, command: string): string {
  const hint = RUN_HINT.exec(detail);
  if (!hint || hint[1]?.trim() !== command.trim()) return detail;
  return detail.slice(0, hint.index).trimEnd();
}

function checkRow(check: NonNullable<SetupResponse["checks"]>[number], o: DeriveOptions): SetupRow {
  const command = check.fix ?? "repowise doctor --format json";
  const detail = withoutRunHint(check.detail ?? "", command);
  return {
    id: `check-${check.id}`,
    tone: "attention",
    status: msg(check.level === "fail" ? "check.statusFail" : "check.statusWarn"),
    title: { text: check.name },
    detail: { text: detail },
    action: { kind: "prompt" },
    command,
    prompt: {
      repoName: o.repoName,
      title: check.name,
      state: detail,
      ...(check.fix ? { command: check.fix } : {}),
      verify: "doctor",
      fix: true,
      fromDoctor: true,
    },
  };
}

// -- the view ------------------------------------------------------------------

/** An agent has called; with calls unreadable, one being registered is enough. */
function agentDone(s: SetupResponse): boolean {
  if (!s.calls_available) return (s.agents ?? []).some((a) => a.connected);
  return Boolean(s.calls.first_call_at);
}

export function deriveSetup(s: SetupResponse, o: DeriveOptions): SetupView {
  const attention: SetupRow[] = [];
  const essentials: SetupRow[] = [];

  // An essential with a problem moves up into "Needs attention".
  const indexProblems = [
    s.index.state === "behind" ? indexBehindRow(s, o) : null,
    (s.index.parse_error_count ?? 0) > 0 ? indexParseRow(s, o) : null,
  ].filter((r): r is SetupRow => r !== null);
  attention.push(...indexProblems);
  if (!indexProblems.length || s.index.state === "incomplete") essentials.push(indexRow(s, o));

  const silent = silentAgents(s, o);
  if (silent.length) attention.push(...silent.map((a) => agentSilentRow(a, o)));
  else essentials.push(agentRow(s, o));

  essentials.push(syncRow(s, o));

  for (const check of s.checks ?? []) {
    if (check.level !== "ok" && !CHECKS_SHOWN_AS_ROWS.has(check.id)) {
      attention.push(checkRow(check, o));
    }
  }

  const done = [
    s.index.state === "up_to_date",
    agentDone(s),
    s.sync.installed === true,
  ].filter(Boolean).length;

  return {
    attention,
    checksUnreadable: !s.checks_available,
    essentials,
    optional: [modelRow(s, o), coverageRow(s, o), rewriteRow(s, o)],
    doneCount: done,
    allDone: done === ESSENTIALS_TOTAL,
  };
}

// -- agents & editors ----------------------------------------------------------

/** The file `repowise agents add` writes for an agent, the repo's own first. */
function configFile(agent: SetupAgent): string | null {
  const files = agent.config_files ?? {};
  return (files.project ?? Object.values(files)[0] ?? [])[0] ?? null;
}

function connectedAgentRow(a: SetupAgent, o: DeriveOptions & { callsReadable: boolean }): SetupRow {
  const path = a.registrations?.[0]?.config_path ?? configFile(a) ?? "";
  let detail = a.last_call_at
    ? msg("agents.detailLastCall", { when: o.relative(a.last_call_at), path })
    : msg("agents.detailNoCall", { path });
  if (!o.callsReadable) detail = msg("unreadable.detail", { command: "repowise status" });
  return {
    id: `agent-${a.id}`,
    tone: "done",
    status: msg("agents.statusConnected"),
    title: { text: a.display_name },
    detail,
    action: { kind: "disconnect", agentId: a.id, agentName: a.display_name },
    command: `repowise agents remove --target=${a.id}`,
    prompt: {
      repoName: o.repoName,
      title: `${a.display_name} and repowise`,
      state: `${a.display_name} is connected to repowise through ${path || "its MCP config"}.`,
      command: "repowise status",
      verify: "status",
    },
  };
}

function installedAgentRow(a: SetupAgent, o: DeriveOptions): SetupRow {
  const file = configFile(a);
  return {
    id: `agent-${a.id}`,
    tone: "off",
    status: msg("agents.statusNotConnected"),
    title: { text: a.display_name },
    detail: file ? msg("agents.detailInstalledWrites", { path: file }) : msg("agents.detailInstalled"),
    action: { kind: "connect", agentId: a.id, agentName: a.display_name },
    command: connectCommand(a.id),
    prompt: {
      repoName: o.repoName,
      title: `Connect ${a.display_name}`,
      state: `${a.display_name} is installed but not connected to repowise.`,
      command: connectCommand(a.id),
      verify: "doctor",
    },
  };
}

/**
 * Settings' "Agents & editors": one row per connected or installed agent,
 * then one row naming the agents that are not installed.
 */
export function agentListRows(s: SetupResponse, o: DeriveOptions): SetupRow[] {
  if (!s.agents_available) {
    return [
      {
        id: "agents-unreadable",
        ...unreadable("repowise agents"),
        title: msg("agent.title"),
        action: null,
        command: "repowise agents",
        prompt: {
          repoName: o.repoName,
          title: "Coding agents",
          state: "repowise could not list the coding agents on this machine. `repowise agents` shows why.",
          verify: "doctor",
          fix: true,
        },
      },
    ];
  }
  const agents = s.agents ?? [];
  const withCalls = { ...o, callsReadable: s.calls_available };
  const rows = [
    ...agents.filter((a) => a.connected).map((a) => connectedAgentRow(a, withCalls)),
    ...agents.filter((a) => a.installed && !a.connected).map((a) => installedAgentRow(a, o)),
  ];
  const absent = agents.filter((a) => !a.installed && !a.connected).map((a) => a.display_name);
  if (absent.length) {
    rows.push({
      id: "agents-absent",
      tone: "off",
      status: msg("agents.statusNotInstalled"),
      title: { text: absent.join(", ") },
      detail: msg("agents.detailNotInstalled"),
      action: null,
      command: "repowise agents add",
      prompt: {
        repoName: o.repoName,
        title: "Connect a coding agent",
        state: `These agents are not installed on this machine: ${absent.join(", ")}.`,
        command: "repowise agents add",
        verify: "doctor",
      },
    });
  }
  return rows;
}

/** The agent that called first, for the "You're set up" line. */
export function firstCallerName(s: SetupResponse): string | null {
  return firstCaller(s.agents ?? [])?.display_name ?? null;
}
