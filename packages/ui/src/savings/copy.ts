/**
 * Every word the Usage & savings body says. English defaults here; a host
 * passes its own translations. Figures arrive already formatted, so a
 * translation never has to know how a token count is abbreviated.
 */
import type { SavingsJobId } from "./jobs";
import type { SpendGrouping } from "./types";

/**
 * How many fewer files agents opened with Repowise in the paired benchmark.
 * Published in docs/BENCHMARKS.md ("Up to 99% fewer files opened"); change it
 * there first. A benchmark figure, never computed from this repository's data.
 */
export const BENCHMARK_FEWER_FILES_PCT = 99;

export interface SavingsCopy {
  // Hero
  heroSince: (date: string) => string;
  heroWindow: (days: number) => string;
  heroUnit: string;
  /** Unit beside the token figure when no call had a baseline to compare. */
  heroTokensUnit: string;
  heroLede: (calls: string, tokens: string, files: string | null) => string;
  heroBasis: string;
  stripBase: string;
  stripWith: string;
  stripTokens: (tokens: string) => string;
  stripCoverage: (calls: string) => string;

  // Proof row
  tokensLabel: string;
  tokensSub: string;
  tokensSubOutput: (outputTokens: string) => string;
  filesLabel: string;
  filesSub: (benchmarkPct: number) => string;
  billLabel: string;
  billSub: string;
  billSubOutput: (outputUsd: string) => string;
  /** Said in place of a dollar figure when nothing carried a rate. */
  billNone: string;

  // Over time
  overTimeTitle: string;
  lensLabel: string;
  lens: { usd: string; tokens: string; files: string };
  lensUsdOff: string;
  lensFilesOff: string;
  filesSince: (date: string) => string;
  tooFewDays: string;
  chartAria: (lens: string, days: number) => string;

  // Where it came from
  jobsTitle: string;
  jobsSub: string;
  jobs: Record<SavingsJobId, { name: string; detail: string }>;
  jobTokens: (tokens: string) => string;
  jobNotSetUp: string;
  jobNone: string;

  // What Repowise used
  usageTitle: string;
  usageSub: string;
  usageGroupLabel: string;
  usageGroups: Record<SpendGrouping, string>;
  spent: string;
  calls: string;
  inputTokens: string;
  outputTokens: string;
  cost: string;
  total: string;
  noApiCharges: string;
  onSubscription: string;
  onMachine: string;
  noModelCalls: string;
  daysCapped: (shown: number) => string;
  activity: (operation: string) => string;

  // How we count
  methodTitle: string;
  measuredTitle: (tokens: string) => string;
  measuredBody: string;
  inferredTitle: (tokens: string) => string;
  inferredBody: string;
  pricedTitle: string;
  pricedBody: string;
  benchmarkLink: string;
  methodLink: string;

  // Empty states
  freshMicro: string;
  freshTitle: string;
  freshBody: string;
  costOnlyMicro: string;
  costOnlyTitle: string;
  costOnlyBody: string;
  emptyBase: string;

  // Nudge
  nudgeDismiss: string;
}

const ACTIVITY_LABELS: Record<string, string> = {
  doc_generation: "Writing docs",
  answer_synthesis: "Answering questions",
  decision_extraction: "Finding decisions",
  outline_planning: "Planning the docs",
};

/** `neighbor_rerank` reads as "Neighbor rerank" when no label is known. */
export function humanizeOperation(operation: string): string {
  const words = operation.replace(/[_-]+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : operation;
}

export const DEFAULT_SAVINGS_COPY: SavingsCopy = {
  heroSince: (date) => `Saved since ${date}`,
  heroWindow: (days) => `Saved in the last ${days} days`,
  heroUnit: "less for your agents to read",
  heroTokensUnit: "tokens your agents didn't read",
  heroLede: (calls, tokens, files) =>
    `Repowise answered ${calls} calls your agents would otherwise have spent searching, opening files and reading long output. They read ${tokens} fewer tokens` +
    (files ? ` and skipped ${files} file opens.` : "."),
  heroBasis: "Measured per call: what the agent would have read, against what Repowise gave it.",
  stripBase: "Without Repowise, same calls",
  stripWith: "What your agents read",
  stripTokens: (tokens) => `${tokens} tokens`,
  stripCoverage: (calls) =>
    `Across ${calls} calls where Repowise stood in for a search, a file read or long command output.`,

  tokensLabel: "Tokens not read",
  tokensSub: "Input your agents didn't have to read.",
  tokensSubOutput: (out) =>
    `Input your agents didn't have to read, plus ${out} output tokens they didn't have to write (estimated).`,
  filesLabel: "Files not opened",
  filesSub: (pct) =>
    `Files your agent got from Repowise and never opened. In our benchmark, agents opened ${pct}% fewer files with Repowise.`,
  billLabel: "Saved on your bill",
  billSub: "At each session's own model rates.",
  billSubOutput: (usd) =>
    `At each session's own model rates. Includes ${usd} of output, estimated from the benchmark.`,
  billNone:
    "No dollar figure: these calls ran on a subscription or carried no model rate. The tokens above still count toward your plan's limits.",

  overTimeTitle: "Over time",
  lensLabel: "Chart measure",
  lens: { usd: "Dollars", tokens: "Tokens", files: "Files" },
  lensUsdOff: "No saving in this range carried a model rate.",
  lensFilesOff: "No file opens were skipped in this range.",
  filesSince: (date) => `Counted from ${date}, when file tracking started.`,
  tooFewDays: "One day of savings so far. The trend shows from the second day.",
  chartAria: (lens, days) => `${lens} saved per day, ${days} days.`,

  jobsTitle: "Where it came from",
  jobsSub: "What Repowise did in place of your agent's searches, reads and commands.",
  jobs: {
    trim: {
      name: "Trimmed long command output",
      detail: "git diffs, test runs, file listings, shell output",
    },
    search: { name: "Answered searches", detail: "search digests and results in place of grep" },
    checks: { name: "Ran risk and review checks", detail: "change risk, file risk, why, health" },
    answers: {
      name: "Answered code questions",
      detail: "answers, file context, symbols, overview",
    },
    rereads: {
      name: "Skipped re-reads",
      detail: "files already read this session, served as a note or skeleton",
    },
  },
  jobTokens: (tokens) => `${tokens} tokens`,
  jobNotSetUp: "Not set up",
  jobNone: "None yet",

  usageTitle: "What Repowise used",
  usageSub:
    "Model calls Repowise made on your key to write docs, answer questions and mine decisions. Shown beside savings, never subtracted from them.",
  usageGroupLabel: "Group usage by",
  usageGroups: { operation: "Activity", model: "Model", day: "Day" },
  spent: "Spent",
  calls: "Calls",
  inputTokens: "Input tokens",
  outputTokens: "Output tokens",
  cost: "Cost",
  total: "Total",
  noApiCharges: "No API charges",
  onSubscription: "On your subscription",
  onMachine: "On your machine",
  noModelCalls: "No model calls in this range, so Repowise has cost nothing to run.",
  daysCapped: (shown) => `Latest ${shown} days with model calls.`,
  activity: (op) => ACTIVITY_LABELS[op] ?? humanizeOperation(op),

  methodTitle: "How we count",
  measuredTitle: (tokens) => `Measured, ${tokens} tokens`,
  measuredBody:
    "Command output and file reads Repowise trimmed, where both the full and the delivered text were seen.",
  inferredTitle: (tokens) => `Inferred, ${tokens} tokens`,
  inferredBody:
    "For answers: the reads and searches the agent would have needed, sized from the real files where possible.",
  pricedTitle: "Priced per session",
  pricedBody:
    "At each session's own model input rate, recorded when it happened. Output is estimated per question from the paired benchmark.",
  benchmarkLink: "The benchmark",
  methodLink: "Full accounting",

  freshMicro: "Nothing recorded yet",
  freshTitle: "Savings start with your agent's first session",
  freshBody:
    "Each time your agent asks Repowise instead of searching, opening files or reading long command output, the difference is counted here.",
  costOnlyMicro: "Savings not recorded here yet",
  costOnlyTitle: "Savings appear once your agents call Repowise",
  costOnlyBody:
    "They're counted from your agents' calls to Repowise. This repository has model spend but no agent calls yet. Connect an agent and the first numbers show after its next session.",
  emptyBase: "Without Repowise",

  nudgeDismiss: "Hide this suggestion",
};
