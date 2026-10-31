import type {
  AffectedSignal,
  BlastRadiusResponse,
  ChangeCandidate,
  ChangeHealthCheckResponse,
} from "@repowise-dev/types/blast-radius";
import type { BlastPromptChange, BlastPromptInput } from "../health/ai-prompts/blast-radius-prompt";
import { describeMarks, riskySpots, type Reach } from "./blast-map-model";
import type { ReviewStatus } from "./review-status";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };

/** An owner's display name: the git name when known, else the email's local part. */
export function ownerName(r: { email: string; name?: string | null }): string {
  return r.name?.trim() || r.email.split("@")[0] || r.email;
}

/** Whether the test list is a floor: coverage missing, partial, degraded or stale. */
export function testsLimited(impact: Result["test_impact"]): boolean {
  if (!impact) return true;
  return ["unavailable", "degraded"].includes(impact.coverage.status) || impact.analysis.partial || impact.analysis.stale;
}

/** Each file that usually changes with a changed one and is left out, once. */
export function missingPartners(result: Result): { path: string; partnerOf: string }[] {
  const seen = new Set<string>();
  return result.cochange_warnings
    .filter((w) => !seen.has(w.missing_partner) && seen.add(w.missing_partner))
    .map((w) => ({ path: w.missing_partner, partnerOf: w.changed }));
}

function promptChange(change: ChangeCandidate | undefined): BlastPromptChange {
  if (!change) return { kind: "files", label: "selected files" };
  if (change.kind === "working_tree") return { kind: "working_tree", label: change.label };
  if (change.kind === "branch")
    return { kind: "branch", label: change.label, base: change.base ?? "origin/HEAD", head_sha: change.head_sha };
  return { kind: "commit", label: change.label, sha: change.head_sha ?? change.label, title: change.title };
}

function promptOwners(result: Result, changed: number): BlastPromptInput["owners"] {
  if (result.changed_owners)
    return result.changed_owners.map((o) => ({ name: ownerName(o), email: o.email, files: o.files, of: changed }));
  return result.recommended_reviewers.slice(0, 3).map((r) => ({ name: ownerName(r), email: r.email, files: r.files }));
}

/** The health check's rows a reviewer acts on, labelled by what the change did. */
function checkFindings(check: ChangeHealthCheckResponse) {
  return check.findings
    .map((f) => {
      const group =
        f.source === "diff-risk"
          ? `diff risk (${f.rule})`
          : f.change_kind === "resolved" || f.change_kind === "improved"
            ? "fixed"
            : f.change_kind;
      return { group: group ?? "", severity: f.severity ?? f.level, path: f.file_path, line: f.line_number, message: f.message };
    })
    .filter((f) => ["introduced", "worsened", "fixed"].includes(f.group) || f.group.startsWith("diff risk"))
    // What the change made worse leads; fixes come last so a cap never hides a regression.
    .sort((a, b) => Number(a.group === "fixed") - Number(b.group === "fixed"));
}

/** Everything the review prompt says, read from one analysis. Pure. */
export function blastPromptInput(
  result: Result,
  reach: Reach,
  status: ReviewStatus,
  changedFiles: string[],
  change: ChangeCandidate | undefined,
  changedTotal?: number,
  healthCheck?: ChangeHealthCheckResponse,
): BlastPromptInput {
  const impact = result.test_impact;
  return {
    change: promptChange(change),
    changedFiles,
    changedTotal,
    status: `${status.word}: ${status.reason}`,
    reach: {
      files: reach.files.length,
      modules: reach.modules,
      direct: reach.direct,
      tests: reach.tests.length,
      band: result.structural_impact_band,
    },
    lookFirst: riskySpots(reach, 8).map((n) => ({ path: n.path, changed: n.changed, signals: describeMarks(n) })),
    tests: (impact?.recommendations ?? []).map((t) => ({ name: t.test_file ?? t.test_id, basis: t.basis })),
    testsTotal: impact?.recommendations_total ?? 0,
    testsLimited: testsLimited(impact),
    testCommands: result.test_commands ?? [],
    missingPartners: missingPartners(result),
    owners: promptOwners(result, changedFiles.length),
    healthCheck: healthCheck
      ? { revspec: healthCheck.revspec, verdict: healthCheck.gate.verdict, findings: checkFindings(healthCheck) }
      : undefined,
  };
}
