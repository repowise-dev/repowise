import { bulletList, joinSections, pluralS, preamble, repoSuffix, type AiPromptFlavor } from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Change review prompt (blast radius of a branch, commit or working tree)
// ─────────────────────────────────────────────────────────────────────

/** What the change is, enough to read its diff. */
export type BlastPromptChange =
  | { kind: "working_tree"; label: string }
  | { kind: "branch"; label: string; base: string; head_sha?: string | null }
  | { kind: "commit"; label: string; sha: string; title?: string | null }
  | { kind: "files"; label: string };

export interface BlastPromptInput {
  change: BlastPromptChange;
  /** The analyzed paths, full. */
  changedFiles: string[];
  /** Every path the change touches, when the analysis was capped. */
  changedTotal?: number | undefined;
  /** The screen's one-line verdict, e.g. "Look first: 2 changed files change often or have weak health." */
  status?: string | undefined;
  /** Source files that import the change; test files that do are counted apart in `tests`. */
  reach: { files: number; modules: number; direct: number; band: string; tests?: number | undefined };
  /** Changed or directly importing files that carry a signal, with the signals in words. */
  lookFirst: { path: string; changed: boolean; signals: string[] }[];
  tests: { name: string; basis: "measured" | "inferred" }[];
  testsTotal: number;
  /** True when coverage is missing, partial or stale, so an empty or short list proves nothing. */
  testsLimited: boolean;
  /** Commands that run the tests above, one per runner. */
  testCommands?: string[] | undefined;
  /** Files history changes alongside these that the change does not touch. */
  missingPartners: { path: string; partnerOf: string }[];
  /** Owners of the changed files (`of` is how many were analyzed); without `of`, owners of affected files. */
  owners: { name: string; email: string; files: number; of?: number | undefined }[];
  /** A health check run on the change, when the reader ran one. */
  healthCheck?: {
    revspec: string;
    verdict: string;
    findings: { group: string; severity: string; path: string; line: number | null; message: string }[];
  } | undefined;
}

export interface BuildBlastPromptOptions {
  input: BlastPromptInput;
  flavor?: AiPromptFlavor;
  repoName?: string;
}

const FILE_CAP = 15;
const LIST_CAP = 6;

function diffCommand(c: BlastPromptChange, files: string[]): string {
  switch (c.kind) {
    case "working_tree":
      return "git diff HEAD && git status --porcelain (untracked files are part of it)";
    case "branch":
      return `git diff ${c.base}...${c.label}`;
    case "commit":
      return `git show ${c.sha}`;
    case "files":
      return `git diff HEAD -- ${files.slice(0, 5).join(" ")}${files.length > 5 ? " ..." : ""}`;
  }
}

function changeLine(c: BlastPromptChange): string {
  switch (c.kind) {
    case "working_tree":
      return "Uncommitted working tree (staged, unstaged and untracked)";
    case "branch":
      return `Branch \`${c.label}\` against \`${c.base}\` (merge-base diff)${c.head_sha ? `, head \`${c.head_sha.slice(0, 12)}\`` : ""}`;
    case "commit":
      return `Commit \`${c.sha}\`${c.title ? `: ${c.title}` : ""}`;
    case "files":
      return "A hand-picked set of files (not a commit)";
  }
}

function capped<T>(items: T[], cap: number, render: (item: T) => string, rest: (n: number) => string): string {
  const lines = items.slice(0, cap).map(render);
  if (items.length > cap) lines.push(rest(items.length - cap));
  return bulletList(lines);
}

/**
 * The review prompt for one change's blast radius: who it is for is a capable
 * agent, so it carries every fact the screen has (full paths) and the outcome
 * wanted, not a procedure.
 */
export function buildBlastRadiusPrompt({ input: i, flavor = "generic", repoName }: BuildBlastPromptOptions): string {
  const total = i.changedTotal ?? i.changedFiles.length;
  const lead = preamble(flavor, {
    source:
      "Review this change before it lands. Repowise computed the facts below from the dependency graph, git history, test reach and code health of the indexed repository.",
    open: "the diff and the files named below",
    mcpTail:
      "Use `get_change_risk` on the change, `get_risk(changed_files=[...])` for reach and test gaps, and `get_context([...])` on any file below before reading by hand.",
  });

  const facts = bulletList([
    changeLine(i.change),
    `Diff: \`${diffCommand(i.change, i.changedFiles)}\``,
    i.status ? `Screen verdict: ${i.status}` : null,
    `Reach: ${i.reach.files.toLocaleString()} source file${pluralS(i.reach.files)} in ${i.reach.modules} module${pluralS(i.reach.modules)} import it within the analyzed depth, ${i.reach.direct} directly${
      i.reach.tests ? `; ${i.reach.tests.toLocaleString()} test file${pluralS(i.reach.tests)} also import it (tests to run, not reach)` : ""
    }; structural reach ${i.reach.band} (an estimate of spread, not a breakage probability)`,
  ]);

  const changed = capped(
    i.changedFiles,
    FILE_CAP,
    (p) => `\`${p}\``,
    (n) => `and ${n + (total - i.changedFiles.length)} more (read them from the diff)`,
  );

  const risky = i.lookFirst.length
    ? [
        "## Look at first",
        "",
        bulletList(i.lookFirst.slice(0, LIST_CAP).map((f) => `\`${f.path}\` (${f.changed ? "changed" : "imports a changed file"}): ${f.signals.join(", ")}`)),
        "",
      ]
    : [];

  const listedTests = i.tests.slice(0, LIST_CAP);
  const testsBody = listedTests.length
    ? bulletList([
        ...listedTests.map(
          (t) => `\`${t.name}\` (${t.basis === "measured" ? "coverage shows it runs this code" : "reaches it through imports"})`,
        ),
        // The total can exceed what the server sent, so count against it.
        i.testsTotal > listedTests.length ? `${i.testsTotal - listedTests.length} more of ${i.testsTotal} in all` : null,
      ])
    : "- None identified.";
  const commands = i.testCommands?.length
    ? ["", "", "Run them with:", "", "```", ...i.testCommands, "```"].join("\n")
    : "";
  const testsNote = i.testsLimited
    ? "\nCoverage evidence is missing, partial or stale here, so this list is a floor, not the full set."
    : "";

  const partners = i.missingPartners.length
    ? [
        "## Usually changes with these, not in this change",
        "",
        capped(
          i.missingPartners,
          LIST_CAP,
          (m) => `\`${m.path}\` (with \`${m.partnerOf}\`)`,
          (n) => `${n} more`,
        ),
        "",
      ]
    : [];

  const ofChanged = i.owners.some((o) => o.of !== undefined);
  const owners = i.owners.length
    ? [
        ofChanged ? "## Owners of the changed files" : "## Owners of the affected files",
        "",
        bulletList(
          i.owners
            .slice(0, 3)
            .map((o) => `${o.name} <${o.email}>, ${o.of !== undefined ? `owns ${o.files} of the ${o.of} changed` : `${o.files}`} file${pluralS(o.of ?? o.files)}`),
        ),
        "",
      ]
    : [];

  // `repowise health check` with no revspec judges committed work against the
  // base, not uncommitted changes, so a working-tree check names no command.
  const checkSubject = !i.healthCheck
    ? ""
    : i.healthCheck.revspec === "working tree"
      ? "the uncommitted changes against HEAD"
      : `\`repowise health check ${i.healthCheck.revspec}\``;
  const check = i.healthCheck
    ? [
        `## Health check: ${i.healthCheck.verdict} (${checkSubject})`,
        "",
        i.healthCheck.findings.length
          ? capped(
              i.healthCheck.findings,
              10,
              (f) => `${f.group} ${f.severity}: \`${f.path}${f.line ? `:${f.line}` : ""}\` ${f.message}`,
              (n) => `${n} more`,
            )
          : "- No finding introduced, worsened or fixed.",
        "",
      ]
    : [];

  return joinSections([
    lead,
    "",
    `## The change${repoSuffix(repoName)}`,
    "",
    facts,
    "",
    `## Changed files (${total})`,
    "",
    changed,
    "",
    ...risky,
    "## Tests to run",
    "",
    testsBody + commands + testsNote,
    "",
    ...partners,
    ...owners,
    ...check,
    "## What I want back",
    "",
    "1. Risks: concrete defects or regressions this diff can cause in the files that import it, each with file:line and why.",
    "2. Missed co-changes: which of the partners above (or others you find) need edits too.",
    "3. Tests: which to run, and the tests that should exist but do not.",
    "4. Verdict: ship, ship after the listed fixes, or rework, in one line.",
    "",
    "Review only; do not edit files unless asked.",
  ]);
}
