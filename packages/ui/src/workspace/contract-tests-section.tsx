/**
 * The tests in other repositories that guard one provider contract.
 *
 * The join starts from the contract links, so the rows here are already scoped
 * to the consumers that call this contract; the only narrowing left is to this
 * contract in particular, which is why `contractId` is a prop and not the
 * caller filtering before it hands the result over.
 *
 * Two rules run through the whole section. Every empty block names the state
 * that produced it, because "no tests" between "nothing guards this" and "we
 * could not look" is the one ambiguity this view exists to remove. And the
 * evidence is written in words: a measured row is a coverage map saying the
 * test ran the code, an inferred row is a graph walk saying it could, and a
 * bar or a percentage would put those two on one scale they do not share.
 */

import type { ReactNode } from "react";
import type {
  WorkspaceTestImpactResponse,
  WorkspaceTestRecommendation,
  WorkspaceUnresolvedLink,
} from "@repowise-dev/types/workspace";
import { Badge } from "../ui/badge";

export interface ContractTestsSectionProps {
  /** The test-impact answer for this contract's provider file, or null. */
  result: WorkspaceTestImpactResponse | null;
  /** Only rows naming this contract belong on this page. */
  contractId: string;
  /** A failed lookup, worded by the host. */
  error?: string | null;
}

const VIA_WORDS: Record<string, string> = {
  "coverage-map": "coverage map",
  "call-graph": "call graph",
  "import-graph": "import graph",
};

const REASON_WORDS: Record<string, string> = {
  unbound: "contract never bound to a symbol",
  no_index: "consumer has no index",
  symbol_missing: "bound symbol is not in the index",
  lookup_failed: "lookup failed",
};

const SUMMARY_REASON_WORDS: Record<string, string> = {
  // Only the CLI reports this one: it reads the contract store from disk and
  // finds none. The route answers from an enricher that is already loaded, so
  // it cannot produce it. Worded here anyway so a CLI-shaped payload rendered
  // in the UI still reads.
  no_contract_store:
    "This workspace has no extracted contracts to join tests to. Run a workspace update to build the contract store.",
  no_contract_data:
    "This workspace carries no contract data, so there is no link from this file to a consumer to follow.",
  no_matching_links:
    "No contract link joins this file to a consumer repository, so no test in another repository can be traced back to it.",
  no_changed_files:
    "No provider file was submitted for analysis, so nothing was traced.",
  lookup_failed:
    "The lookup failed before it could answer, so treat these consumers as unknown.",
};

const NONE_WORDS =
  "The consumer repositories were analyzed and nothing reaches the call sites for this contract. That is an answer, not a failure: no test in another repository guards it.";

export function ContractTestsSection({ result, contractId, error }: ContractTestsSectionProps) {
  // Null is "the host did not ask", which is a different fact from "the answer
  // was empty" and gets no section at all.
  if (!result && !error) return null;

  return (
    <section className="mt-10 flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:mt-12 sm:pt-8">
      <h2 className="text-xl font-semibold tracking-tight text-[var(--color-text-primary)]">
        Tests to run
      </h2>
      <Body result={result} contractId={contractId} error={error ?? null} />
    </section>
  );
}

function Body({
  result,
  contractId,
  error,
}: {
  result: WorkspaceTestImpactResponse | null;
  contractId: string;
  error: string | null;
}) {
  if (error) return <Prose>{error}</Prose>;
  if (!result) return null;

  const rows = result.recommendations.filter((r) => r.contract_ids.includes(contractId));
  const unresolved = result.unresolved.filter((u) => u.contract_id === contractId);

  if (rows.length === 0 && unresolved.length === 0) {
    const reason = summaryReason(result);
    // An empty-string reason is still a miss, so index with "" and let the
    // lookup fall through to the analyzed-and-found-nothing wording.
    const words = SUMMARY_REASON_WORDS[reason ?? ""] ?? NONE_WORDS;
    const detail = reason === "lookup_failed" ? summaryDetail(result) : null;
    return <Prose>{detail ? `${words} (${detail})` : words}</Prose>;
  }

  return (
    <>
      <Prose>
        Tests in the repositories that call this contract, found by walking each consumer&apos;s
        own index from the call site. A measured row is a coverage map recording that the test ran
        the code; an inferred row is a graph reaching it.
      </Prose>
      {groupByRepo(rows).map(([repo, repoRows]) => (
        <div key={repo} className="flex flex-col gap-2">
          <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            {repo}
          </h3>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {repoRows.map((row) => (
              // One test id can appear once per provider repo it guards, so
              // the provider files are part of what makes the row unique.
              <li
                key={`${row.consumer_repo}|${row.test_id}|${row.source_files.join(",")}`}
                className="flex flex-col gap-1"
              >
                <span className="font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {row.test_file}
                </span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                  <Badge variant={row.basis === "measured" ? "accent" : "outline"}>
                    {row.basis}
                  </Badge>
                  <span>via the {VIA_WORDS[row.via] ?? row.via}</span>
                  {/* A plain number: confidence here is a rank among rows, and
                      a percentage would read as a chance the test catches it. */}
                  <span className="tabular-nums">confidence {row.confidence.toFixed(2)}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {unresolved.length > 0 && <UnresolvedList rows={unresolved} />}
    </>
  );
}

function UnresolvedList({ rows }: { rows: WorkspaceUnresolvedLink[] }) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
        Could not determine
      </h3>
      <Prose>
        These call sites were not answered either way, so treat them as unknown and not as
        untested.
      </Prose>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {rows.map((row, index) => (
          <li
            key={`${row.consumer_repo}|${row.consumer_file}|${row.contract_id}|${
              row.consumer_symbol_id ?? index
            }`}
          >
            <span className="font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
              {row.consumer_repo} / {row.consumer_file}
            </span>
            <span className="ml-2 text-xs text-[var(--color-text-tertiary)]">
              {REASON_WORDS[row.reason] ?? row.reason}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Prose({ children }: { children: ReactNode }) {
  return (
    <p className="max-w-[68ch] text-base leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
      {children}
    </p>
  );
}

function summaryReason(result: WorkspaceTestImpactResponse): string | null {
  const reason = result.summary?.reason;
  return typeof reason === "string" ? reason : null;
}

function summaryDetail(result: WorkspaceTestImpactResponse): string | null {
  const detail = result.summary?.detail;
  return typeof detail === "string" && detail ? detail : null;
}

function groupByRepo(rows: WorkspaceTestRecommendation[]): [string, WorkspaceTestRecommendation[]][] {
  const byRepo = new Map<string, WorkspaceTestRecommendation[]>();
  for (const row of rows) {
    const bucket = byRepo.get(row.consumer_repo);
    if (bucket) bucket.push(row);
    else byRepo.set(row.consumer_repo, [row]);
  }
  return [...byRepo.entries()].sort(([a], [b]) => a.localeCompare(b));
}
