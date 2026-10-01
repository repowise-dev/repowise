/**
 * The parts every agent prompt shares: the per-flavor opening and closing
 * instructions and the formatters the sections are written in.
 */

export type AiPromptFlavor =
  | "generic"
  | "claude-code"
  | "claude-code-mcp"
  | "cursor";

const FLAVORS: AiPromptFlavor[] = ["generic", "claude-code", "claude-code-mcp", "cursor"];

/** Who the agent is, per flavor: the first sentence of every prompt. */
export type PromptRole = Record<AiPromptFlavor, string>;

export const REPO_ROLE: PromptRole = {
  generic: "You are a senior engineer in this repository.",
  "claude-code": "You are Claude Code in this repository.",
  "claude-code-mcp":
    "You are Claude Code in this repository, which Repowise indexes and serves over MCP.",
  cursor: "Work in this repository.",
};

const FILE_ROLE: PromptRole = {
  generic: "You are a senior engineer working on one file in this repository.",
  "claude-code": "You are Claude Code working in this repository.",
  "claude-code-mcp":
    "You are Claude Code working in this repository, which is indexed by repowise and exposes its MCP tools.",
  cursor: "Work on the file referenced below.",
};

export const WORKSPACE_ROLE: PromptRole = {
  generic: "You are a senior engineer working across the repositories of one workspace.",
  "claude-code": "You are Claude Code working across the repositories of one workspace.",
  "claude-code-mcp":
    "You are Claude Code working across the repositories of one workspace indexed by repowise, with its MCP tools available.",
  cursor: "Work across the repositories referenced below.",
};

const LEAD = "Treat each item as a lead, not ground truth.";

/** How each non-MCP flavor checks the evidence; `open` is what it names. */
const READ_FIRST: Record<Exclude<AiPromptFlavor, "claude-code-mcp">, (open: string) => string> = {
  generic: (open) =>
    `Open ${open}, then confirm each item in the code before you act. If one is a false positive, say so and skip it.`,
  "claude-code": (open) =>
    `Use Read, Grep and Glob on ${open} before planning edits, and flag any false positive. Use TodoWrite for non-trivial steps.`,
  cursor: (open) =>
    `Use @file and @codebase to read ${open} before editing, and call out any false positive.`,
};

const MCP_FALLBACK =
  "Fall back to Read / Grep only for what the index cannot serve, and flag any false positive. Use TodoWrite for non-trivial steps.";

export type PreambleParts =
  | {
      role?: PromptRole;
      /** What the evidence is and where it came from. */
      source: string;
      /** What the agent should read first, e.g. "the files it names". */
      open: string;
      /** The repowise MCP calls worth making before reading by hand. */
      mcpTail: string;
    }
  | {
      role?: PromptRole;
      /** Per-flavor text after the role, for a prompt whose wording is pinned
       *  elsewhere (the core renderer is parity-tested against it). */
      body: Record<AiPromptFlavor, string>;
    };

/** The opening every agent prompt starts on: who the agent is, what the
 *  evidence is, that it is a lead, and how this flavor checks it. */
export function preamble(flavor: AiPromptFlavor, parts: PreambleParts): string {
  const role = (parts.role ?? REPO_ROLE)[flavor];
  if ("body" in parts) return `${role} ${parts.body[flavor]}`;
  const check =
    flavor === "claude-code-mcp" ? `${parts.mcpTail} ${MCP_FALLBACK}` : READ_FIRST[flavor](parts.open);
  return `${role} ${parts.source} ${LEAD} ${check}`;
}

/** The opening of every single-file prompt. */
export const FLAVOR_PREAMBLE = Object.fromEntries(
  FLAVORS.map((flavor) => [
    flavor,
    preamble(flavor, {
      role: FILE_ROLE,
      source: "The findings below were detected by repowise's static analyzer.",
      open: "the file, its callers, its tests and its neighbors",
      mcpTail:
        "Before re-reading files by hand, pull the context repowise already computed: call `get_context([...])` for the file skeleton (every signature + the bodies of the most central symbols, ~37% of a full Read), `get_symbol(\"file::Name\")` for the exact bytes of one function, `get_risk([...])` before editing to see blast radius, co-change partners, and test gaps, and `get_why(...)` for the decision behind the current shape.",
    }),
  ]),
) as Record<AiPromptFlavor, string>;

/** The surface a closer is written for; each names its own tools and risks. */
type CloserKind = "refactor" | "coverage" | "security" | "hotspot" | "file-health";

const CLOSER_CONFIG: Record<
  CloserKind,
  { mcpSecond: (f: string) => string; mcpInto: string; verb: string; readFirst: string }
> = {
  refactor: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` for the blast radius, co-change partners, and test gaps`,
    mcpInto: "functions below",
    verb: "propose a fix",
    readFirst:
      "Start by reading the file end-to-end, then explore its callers, tests, and any related helpers. The findings below describe symptoms — the actual root cause may live elsewhere. Don't propose a fix until you've grounded each one in the real code.",
  },
  coverage: {
    mcpSecond: (f) =>
      `\`get_context(['${f}'], include=['callers'])\` to see who exercises it`,
    mcpInto: "functions you'll test",
    verb: "write a test",
    readFirst:
      "Start by reading the file end-to-end, then explore its callers, the existing tests directory, and any sibling files that test similar code. The coverage numbers below come from a static report — verify them by looking at the real test files and the real source. Don't write a test before you've seen the code it's exercising and the project's existing test conventions.",
  },
  security: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` to see who depends on this code before you touch it`,
    mcpInto: "flagged lines",
    verb: "change anything",
    readFirst:
      "Start by reading the file and the exact lines flagged, then trace how the value flows in and out. The scanner matches patterns — confirm this is actually exploitable in context before you change anything. If it's a false positive (test fixture, sample data, already-sanitized), say so and stop.",
  },
  hotspot: {
    mcpSecond: (f) =>
      `\`get_risk(['${f}'])\` for the co-change partners and test gaps that make this file risky to touch`,
    mcpInto: "most-churned functions",
    verb: "propose changes",
    readFirst:
      "Start by reading the file end-to-end, then look at what it co-changes with and how well it's tested. High churn is a symptom — the goal is to make this file safer and cheaper to change, not to rewrite it. Don't propose changes until you understand why it churns.",
  },
  "file-health": {
    mcpSecond: (f) =>
      `\`get_health(['${f}'])\` for the scored findings behind the numbers below, then \`get_risk(['${f}'])\` for blast radius, co-change partners and test gaps`,
    mcpInto: "functions the findings name",
    verb: "change anything",
    readFirst:
      "Start by reading the file end-to-end, then its callers, its tests, and whatever it changes alongside. The report above spans several independent signals, and they do not all point at the same fix. Work out which ones share a root cause before you touch anything.",
  },
};

/** The MCP flavor points at repowise tools; every other flavor reads first. */
export function explorationCloser(
  flavor: AiPromptFlavor,
  filePath: string,
  kind: CloserKind,
): string {
  const cfg = CLOSER_CONFIG[kind];
  if (flavor === "claude-code-mcp") {
    return `Start with \`get_context(['${filePath}'])\` for the skeleton and ${cfg.mcpSecond(
      filePath,
    )}, then \`get_symbol\` into the specific ${cfg.mcpInto}. repowise already indexed this repo — lean on it before falling back to Read/Grep. Don't ${cfg.verb} until you've grounded each finding in the actual code.`;
  }
  return cfg.readFirst;
}

export function bulletList(items: (string | null | undefined | false)[]): string {
  return items.filter(Boolean).map((s) => `- ${s}`).join("\n");
}

/** The `` (`repo`)`` suffix a prompt heading carries when the host named the repo. */
export function repoSuffix(repoName: string | undefined): string {
  return repoName ? ` (\`${repoName}\`)` : "";
}

/** The plural `s` for a count, so "1 file" and "2 files" both read right. */
export function pluralS(count: number): string {
  return count === 1 ? "" : "s";
}

/** The constraints and expected-output sections most prompts end on. */
export function closingSections(
  constraints: (string | null)[],
  expected: string[],
): string[] {
  return [
    "## Hard constraints",
    "",
    bulletList(constraints),
    "",
    "## What I expect back",
    "",
    expected.join("\n"),
    "",
  ];
}

/**
 * Join sections, dropping every "" (absent sections and bare separators alike),
 * so a section that needs a blank line before it carries its own newline.
 */
export function joinSections(sections: string[]): string {
  return sections.filter((s) => s !== "").join("\n");
}

export function numbered(items: string[]): string {
  return items.map((s, i) => `${i + 1}. ${s}`).join("\n");
}

/** The validation fields the Verify block reads; refactoring plans, opportunity
 *  profiles and the performance queue all carry them. */
export interface VerifyValidation {
  total: number;
  tests: string[];
  reasons?: Record<string, string> | null | undefined;
  commands: string[];
}

function codeList(subjects: string[]): string {
  return subjects.map((s) => `\`${s}\``).join(", ");
}

/** The tests to run after the change, most direct first, each with why it is
 *  listed, then the command. With no guarding test, the instruction to add one. */
export function verifyLines(validation: VerifyValidation, subjects: string[]): string[] {
  if (validation.tests.length === 0) {
    return validation.total > 0
      ? [`Guarding tests: ${validation.total}, but none were included in this payload.`]
      : [`No guarding tests found: add a test for ${codeList(subjects)} before changing it.`];
  }
  const rows = validation.tests.map((test) => {
    const reason = validation.reasons?.[test];
    return `- \`${test}\`${reason ? ` (${reason})` : ""}`;
  });
  const shown =
    validation.total > validation.tests.length
      ? [`${validation.tests.length} of ${validation.total} guarding tests shown.`]
      : [];
  const run = validation.commands.length
    ? ["", "Run:", "", "```", ...validation.commands, "```"]
    : [];
  return [
    "Run these after the change. The tests that exercise it most directly come first:",
    "",
    ...rows,
    ...shown,
    ...run,
  ];
}

/** A `## Verify` section. Without test data it still says what to cover, so
 *  every prompt ends its change on a test run. */
export function verifySection(
  validation: VerifyValidation | null | undefined,
  subjects: string[],
): string {
  const lines = validation
    ? verifyLines(validation, subjects)
    : [
        `No test data came with this prompt. Find the tests that exercise ${subjects.length ? codeList(subjects) : "what you change"}, run them before and after the change, and add one that pins today's behaviour if none does.`,
      ];
  return ["## Verify", "", ...lines].join("\n");
}
