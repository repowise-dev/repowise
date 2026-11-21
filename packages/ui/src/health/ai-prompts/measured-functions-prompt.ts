import type { MeasuredRow, MeasuredRun } from "@repowise-dev/types/runtime-evidence";

import {
  intervalText,
  rowLocation,
  shareText,
  workloadClassLabel,
} from "../performance/measured-format";
import { symbolSubject } from "../performance/presentation";
import { formatNumber } from "../../lib/format";
import {
  bulletList,
  closingSections,
  joinSections,
  pluralS,
  preamble,
  repoSuffix,
  verifySection,
  type AiPromptFlavor,
} from "./shared";

// ─────────────────────────────────────────────────────────────────────
// Functions a stored profile run measured (the measured table's handoff)
// ─────────────────────────────────────────────────────────────────────

const PARTS = {
  source:
    "Repowise ranked the functions below by the self time a stored profile run measured: what each function costs itself, with the libraries it calls and without waits.",
  open: "each function, its callers and its tests",
  mcpTail:
    "For each function, call `get_symbol(\"...\")` with its id for the body, then `get_risk([file])` before editing.",
};

const CONSTRAINTS = [
  "Take the functions in rank order; the rank is by measured self time.",
  "Read each function before changing it. A function can cost a lot because its callers run it often, and changing the call pattern can beat tuning the body.",
  "One function per change, each independently revertible.",
  "Preserve results exactly.",
  "Run the tests that reach each function before and after its change.",
];

const EXPECTED = [
  "1. For each function: where its time goes, and whether a change is worth making.",
  "2. For each change: the edit, the tests you ran, and why the result is unchanged.",
  "3. How you will re-measure the workload, and what is left.",
];

function entry(row: MeasuredRow, index: number, orderingOnly: boolean): string {
  const interval = intervalText(row.share_low, row.share_high);
  const cost =
    orderingOnly || row.share == null
      ? `rank ${row.rank}`
      : `${shareText(row.share)} of the run${interval ? ` (${interval})` : ""}, rank ${row.rank}`;
  const lines = [
    `${index + 1}. \`${symbolSubject(row.symbol_id)}\` in \`${rowLocation(row)}\`: ${cost}`,
    `   - Symbol id: \`${row.symbol_id}\``,
  ];
  if (row.callers.length > 0) {
    lines.push(`   - Callers outside tests: ${row.callers.map((c) => `\`${c}\``).join(", ")}`);
  }
  if (row.tests && row.tests.length > 0) {
    lines.push(`   - Nearest tests: ${row.tests.map((t) => `\`${t}\``).join(", ")}`);
  }
  if (row.on_static_findings && row.on_static_findings.length > 0) {
    lines.push(
      `   - Static performance causes on it: ${row.on_static_findings.map((id) => `\`${id}\``).join(", ")}`,
    );
  }
  return lines.join("\n");
}

export interface BuildMeasuredFunctionsPromptOptions {
  run: MeasuredRun;
  rows: MeasuredRow[];
  flavor?: AiPromptFlavor;
  repoName?: string;
}

export function buildMeasuredFunctionsPrompt({
  run,
  rows,
  flavor = "generic",
  repoName,
}: BuildMeasuredFunctionsPromptOptions): string {
  const files = Array.from(new Set(rows.map((r) => r.file_path)));
  const commit = run.commit ? ` at commit \`${run.commit.slice(0, 7)}\`` : "";
  return joinSections([
    preamble(flavor, PARTS),
    "",
    `## Measured in \`${run.workload_name}\`${repoSuffix(repoName)}`,
    "",
    bulletList([
      `Run: the ${workloadClassLabel(run.workload_class)} workload \`${run.workload_name}\`${commit}${
        run.samples != null ? `, ${formatNumber(run.samples)} samples` : ""
      }.`,
      run.repo_self_share != null
        ? `Repo code itself explains ${shareText(run.repo_self_share)} of the run; the rest is libraries and waits, so a change here moves at most that share.`
        : null,
      run.ordering_only
        ? "A deterministic tracer ran, so the rank orders the functions and no share is given."
        : null,
      `Functions: **${rows.length}** in ${files.length} file${pluralS(files.length)}.`,
    ]),
    "",
    rows.map((row, i) => entry(row, i, run.ordering_only)).join("\n\n"),
    "",
    verifySection(null, files.slice(0, 5)),
    `Then run \`${run.workload_name}\` the way it was profiled and add the new run, so the next ranking measures the change.`,
    "",
    ...closingSections(CONSTRAINTS, EXPECTED),
  ]);
}
