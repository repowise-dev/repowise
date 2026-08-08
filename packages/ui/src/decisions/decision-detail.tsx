"use client";

/**
 * Decision detail view — the full record: markdown body (context / decision /
 * rationale / alternatives / consequences), evolution lineage, a writable
 * module-link editor, the evidence drawer, the AI verification prompt, and the
 * confirm/undo status actions (confirm-proposed, deprecate-active).
 *
 * Presentation + orchestration only: the host injects data fetching,
 * mutations, links, and an optional linked-issues panel through a
 * {@link DecisionDetailAdapter}, so web and hosted render the same view.
 */

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  FileSearch,
  Flame,
  GitCommitHorizontal,
} from "lucide-react";
import type {
  DecisionRecord,
  DecisionStatus,
} from "@repowise-dev/types/decisions";

import { Markdown } from "../shared/markdown";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { formatDate, stripMarkdown } from "../lib/format";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AiPromptModal } from "../health/ai-prompt-modal";
import {
  buildDecisionAiPrompt,
  buildDecisionEnforcementAiPrompt,
} from "../health/ai-prompt-builder";

import { ModuleLinkEditor } from "./module-link-editor";
import { VerificationBadge } from "./verification-badge";
import { DecisionStatusMark } from "./decision-status-mark";
import { DecisionEvidenceDrawer } from "./decision-evidence-drawer";
import { DecisionLineage } from "./decision-lineage";
import { describeRecordStaleness } from "./decision-staleness";
import type {
  DecisionDetailAdapter,
  DecisionLinkComponent,
} from "./decision-detail-adapter";
import { toFriendlyMessage } from "../lib/errors";

const SOURCE_LABEL: Record<string, string> = {
  inline_marker: "Marker",
  git_archaeology: "Git history",
  readme_mining: "Docs",
  cli: "Manual",
  comment: "Comment",
  pr: "Pull request",
  adr: "ADR",
  changelog: "Changelog",
  session: "Session",
};

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

const CONFIRM_COPY: Record<
  string,
  { title: string; description: string; confirmLabel: string; destructive: boolean }
> = {
  active: {
    title: "Confirm decision?",
    description: "Mark this decision as active.",
    confirmLabel: "Confirm",
    destructive: false,
  },
  deprecated: {
    title: "Deprecate decision?",
    description:
      "This will mark the decision as deprecated. Existing references remain but it will no longer be considered current.",
    confirmLabel: "Deprecate",
    destructive: true,
  },
};

export interface DecisionDetailProps {
  decision: DecisionRecord;
  adapter: DecisionDetailAdapter;
}

export function DecisionDetail({ decision, adapter }: DecisionDetailProps) {
  const Link: DecisionLinkComponent = adapter.LinkComponent ?? "a";

  const [status, setStatus] = React.useState<DecisionStatus>(decision.status);
  const [loading, setLoading] = React.useState(false);
  const [pendingStatus, setPendingStatus] = React.useState<DecisionStatus | null>(null);
  const [linkedModules, setLinkedModules] = React.useState(decision.affected_modules);
  const [linkedFiles, setLinkedFiles] = React.useState(decision.affected_files);
  const [linkageSaving, setLinkageSaving] = React.useState(false);
  const [evidenceOpen, setEvidenceOpen] = React.useState(false);
  // Which AI-prompt flavor the modal shows: verification ("is this decision
  // still true?") or enforcement ("make the code conform to it").
  const [promptMode, setPromptMode] = React.useState<"verify" | "enforce" | null>(null);

  // Read from `decision.affected_files` and not from `linkedFiles`, which the
  // editor below mutates locally. `staleness_score` is a proportion the server
  // computed against the *stored* scope, so pairing it with an unsaved scope
  // would print a count derived from one file set over a fraction derived from
  // another, and the sentence would drift the moment somebody typed.
  const staleness = React.useMemo(
    () => describeRecordStaleness(decision),
    [decision],
  );

  // Lineage: cheap, load eagerly so the Evolution timeline renders when present.
  const { data: lineage } = useSWR(
    `decision-lineage:${adapter.cacheKey}`,
    () => adapter.getLineage(),
    { revalidateOnFocus: false },
  );

  // Evidence: lazy — only fetched once the drawer is opened.
  const {
    data: evidence,
    error: evidenceError,
    isLoading: evidenceLoading,
  } = useSWR(
    evidenceOpen ? `decision-evidence:${adapter.cacheKey}` : null,
    () => adapter.getEvidence(),
    { revalidateOnFocus: false },
  );

  // Sibling list for prev/next navigation — same ordering as the list page.
  const { data: siblings } = useSWR(
    `decisions-siblings:${adapter.repoId}`,
    () => adapter.listSiblingIds(),
    { revalidateOnFocus: false },
  );
  const { prevId, nextId } = React.useMemo(() => {
    const ids = siblings ?? [];
    const idx = ids.indexOf(decision.id);
    return {
      prevId: idx > 0 ? ids[idx - 1] : null,
      nextId: idx >= 0 && idx < ids.length - 1 ? ids[idx + 1] : null,
    };
  }, [siblings, decision.id]);

  // Suggestions for the module autocomplete — top-level modules indexed for
  // this repo. Loaded once; cheap to cache.
  const { data: moduleSuggestions } = useSWR(
    `module-health-suggestions:${adapter.repoId}`,
    () => adapter.listModuleSuggestions(),
    { revalidateOnFocus: false },
  );

  const saveLinkage = async (next: { modules: string[]; files: string[] }) => {
    const previousModules = linkedModules;
    const previousFiles = linkedFiles;
    setLinkageSaving(true);
    try {
      await adapter.patchDecision({
        affected_modules: next.modules,
        affected_files: next.files,
      });
      setLinkedModules(next.modules);
      setLinkedFiles(next.files);
      toast.success("Decision linkage updated", {
        action: {
          label: "Undo",
          onClick: async () => {
            try {
              await adapter.patchDecision({
                affected_modules: previousModules,
                affected_files: previousFiles,
              });
              setLinkedModules(previousModules);
              setLinkedFiles(previousFiles);
            } catch (err) {
              toast.error(
                `Couldn't undo: ${toFriendlyMessage(err)}`,
              );
            }
          },
        },
        duration: 6000,
      });
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `Couldn't save linkage: ${toFriendlyMessage(err)}`
          : "Couldn't save linkage",
      );
    } finally {
      setLinkageSaving(false);
    }
  };

  const applyStatusChange = async (newStatus: DecisionStatus) => {
    const previous = status;
    setLoading(true);
    try {
      await adapter.patchDecision({ status: newStatus });
      setStatus(newStatus);
      setPendingStatus(null);
      toast.success(`Decision marked ${newStatus.replace(/_/g, " ")}`, {
        action: {
          label: "Undo",
          onClick: async () => {
            try {
              await adapter.patchDecision({ status: previous });
              setStatus(previous);
            } catch (err) {
              toast.error(
                `Couldn't undo: ${toFriendlyMessage(err)}`,
              );
            }
          },
        },
        duration: 6000,
      });
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `Couldn't update decision: ${toFriendlyMessage(err)}`
          : "Couldn't update decision",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleStatusChange = (newStatus: DecisionStatus) => {
    if (CONFIRM_COPY[newStatus]) {
      setPendingStatus(newStatus);
    } else {
      void applyStatusChange(newStatus);
    }
  };

  const confirmConfig = pendingStatus ? CONFIRM_COPY[pendingStatus] : null;
  const linkedIssues = adapter.renderLinkedIssues?.();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <Link
          href={adapter.decisionsHref()}
          className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          All decisions
        </Link>
        <div className="flex items-center gap-1">
          <PrevNextLink
            Link={Link}
            href={prevId ? adapter.decisionHref(prevId) : null}
            label="Previous decision"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Prev
          </PrevNextLink>
          <PrevNextLink
            Link={Link}
            href={nextId ? adapter.decisionHref(nextId) : null}
            label="Next decision"
          >
            Next <ChevronRight className="h-3.5 w-3.5" />
          </PrevNextLink>
        </div>
      </div>
      {/* Header. The title is the subject, so it gets the size; status and
          trust are marks beside it, and the verbs sit in one row underneath
          rather than crowding the heading line. */}
      <div className="space-y-4">
        <div className="space-y-2.5">
          <h1 className="text-[26px] font-semibold leading-tight text-[var(--color-text-primary)]">
            {stripMarkdown(decision.title)}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <DecisionStatusMark status={status} />
            {decision.verification && (
              <VerificationBadge verification={decision.verification} />
            )}
          </div>
        </div>

        {/* One mono ribbon, tabular, instead of four sans sentences.
            Staleness left the ribbon: a proportion has no reading at a glance,
            and the thing a reader wants from it is a sentence, which is now
            below. Confidence left with it, being source rank times
            verification, so it restated the Source cell beside it as a
            percentage and every record from one source carried one number. */}
        <dl className="flex flex-wrap gap-x-8 gap-y-2">
          <MetaItem label="Source" value={SOURCE_LABEL[decision.source] ?? decision.source} />
          <MetaItem label="Recorded" value={formatDateOrDash(decision.created_at)} />
        </dl>

        <div className="flex flex-wrap items-center gap-2">
          <AiPromptButton
            label={status === "proposed" ? "Verify & confirm with AI" : "Verify with AI"}
            onClick={() => setPromptMode("verify")}
          />
          {status === "active" && (
            <AiPromptButton
              label="Enforce this decision"
              onClick={() => setPromptMode("enforce")}
            />
          )}
          <button
            type="button"
            onClick={() => setEvidenceOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
          >
            <FileSearch className="h-3.5 w-3.5" />
            Evidence
            {(decision.evidence_count ?? 0) > 0 && (
              <span className="tabular-nums text-[var(--color-text-tertiary)]">
                {decision.evidence_count}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* What has happened to the code underneath this record, as a fact.
          A line, not a filled panel: a tinted ground plus a border plus
          coloured text says the same thing three times, and it outweighed the
          record it was warning about.

          It renders in all three states rather than only above a threshold,
          because the two quiet states are not the same state. A record naming
          no files scored 0.0 and rendered exactly like a record whose code had
          not moved, so the reader who correctly read one was taught a rule
          that made them wrong about the other. Only the moved case is marked,
          and it is marked in warning rather than error: the arithmetic says
          the files changed, which is a reason to re-read a record, not a
          verdict that it expired. Three confirmed working rules on a live
          index sat at a red 100% while being true. */}
      {/* No band colour. The table cell on the list page is deliberately
          uncoloured because a file-change count is not a health reading, and
          amber here is the same category error one hue down. It would also
          make this the third mark on one record drawing from one colour
          vocabulary with no key: the status mark paints `active` green and
          `deprecated` red, and the verification badge paints `fuzzy` amber.
          The icon carries "look at this"; the sentence carries what it is. */}
      <p
        className={`flex items-start gap-2 text-sm ${
          staleness.kind === "moved"
            ? "text-[var(--color-text-secondary)]"
            : "text-[var(--color-text-tertiary)]"
        }`}
      >
        {staleness.kind === "moved" && (
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        )}
        <span>{staleness.sentence}</span>
      </p>

      {/* Evolution / lineage chain */}
      {lineage && lineage.length > 1 && (
        <DecisionLineage
          lineage={lineage}
          repoId={adapter.repoId}
          LinkComponent={Link}
          {...(adapter.linkPrefix ? { linkPrefix: adapter.linkPrefix } : {})}
        />
      )}

      {/* Content sections. This is the part someone came to read. */}
      <div className="space-y-7 border-t border-[var(--color-border-default)] pt-7">
        {decision.context && <Section title="Context" text={decision.context} />}
        {decision.decision && <Section title="Decision" text={decision.decision} />}
        {decision.rationale && <Section title="Rationale" text={decision.rationale} />}
        {decision.alternatives.length > 0 && (
          <ListSection title="Alternatives Rejected" items={decision.alternatives} />
        )}
        {decision.consequences.length > 0 && (
          <ListSection title="Consequences & Tradeoffs" items={decision.consequences} />
        )}
      </div>

      {/* Governance linkage. Was two bordered cards side by side; a hairline
          and vertical rhythm group them at a fraction of the ink, and neither
          is a discrete object you act on repeatedly. */}
      <div className="space-y-7 border-t border-[var(--color-border-default)] pt-7">
        <ModuleLinkEditor
          modules={linkedModules}
          files={linkedFiles}
          suggestions={moduleSuggestions ?? []}
          saving={linkageSaving}
          onSave={saveLinkage}
        />

        {/* The full evidence rows live in the drawer (Evidence button); this
            block answers "what happened to the governed code since?". */}
        <div>
          <h2 className={MICRO_LABEL + " mb-2"}>Since this decision</h2>
          <div className="space-y-2 text-sm">
            {decision.last_code_change && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Affected files last changed {formatDateOrDash(decision.last_code_change)}.
              </p>
            )}
            <div className="flex flex-col gap-1.5">
              <Link
                href={adapter.commitsHref({ sort: "date" })}
                className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline"
              >
                <GitCommitHorizontal className="h-3.5 w-3.5" />
                Recent commits to this repo
              </Link>
              <Link
                href={adapter.hotspotsHref()}
                className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline"
              >
                <Flame className="h-3.5 w-3.5" />
                Hotspots &amp; churn in affected areas
              </Link>
            </div>
            {decision.evidence_commits.length > 0 && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Evidence commits:{" "}
                {decision.evidence_commits.slice(0, 4).map((c, i) => (
                  <React.Fragment key={c}>
                    {i > 0 && ", "}
                    <Link
                      href={adapter.commitsHref({ commit: c })}
                      className="font-mono hover:text-[var(--color-accent-primary)] hover:underline"
                    >
                      {c.slice(0, 8)}
                    </Link>
                  </React.Fragment>
                ))}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Optional linked issues (e.g. Jira) — host supplies or omits. */}
      {linkedIssues}

      {/* Tags */}
      {decision.tags.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className={MICRO_LABEL}>Tags</span>
          {decision.tags.map((tag) => (
            <span
              key={tag}
              className="font-mono text-xs text-[var(--color-text-secondary)]"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="space-y-2 border-t border-[var(--color-border-default)] pt-4">
        <div className="flex gap-2">
          {status === "proposed" && (
            <>
              <button
                onClick={() => handleStatusChange("active")}
                disabled={loading}
                // Accent, not a filled green: green/amber/red are reserved for
                // health readouts where they carry a band. This is the page's
                // one primary action, which is what the accent is for.
                className="rounded-md bg-[var(--color-accent-primary)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-on-accent)] hover:opacity-90 disabled:opacity-50"
              >
                Confirm
              </button>
              <button
                onClick={() => handleStatusChange("deprecated")}
                disabled={loading}
                className="rounded-md border border-[var(--color-border-default)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] disabled:opacity-50"
              >
                Dismiss
              </button>
            </>
          )}
          {status === "active" && (
            <button
              onClick={() => handleStatusChange("deprecated")}
              disabled={loading}
              className="rounded-md border border-[var(--color-error)]/40 px-3 py-1.5 text-sm text-[var(--color-error)] hover:bg-[var(--color-error)]/10 disabled:opacity-50"
            >
              Deprecate
            </button>
          )}
        </div>
        {status === "proposed" && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Confirm marks this as an accurate, current decision for your team.
            Dismiss hides it from the active list as inaccurate or no longer
            relevant. Neither changes any code.
          </p>
        )}
        {status === "active" && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Deprecate marks this decision as no longer current — existing
            references remain, but it stops being treated as the standard. It
            doesn&apos;t change any code.
          </p>
        )}
      </div>
      {confirmConfig && (
        <ConfirmDialog
          open={pendingStatus !== null}
          onOpenChange={(o) => !o && setPendingStatus(null)}
          title={confirmConfig.title}
          description={confirmConfig.description}
          confirmLabel={confirmConfig.confirmLabel}
          destructive={confirmConfig.destructive}
          loading={loading}
          onConfirm={() => void applyStatusChange(pendingStatus!)}
        />
      )}

      <DecisionEvidenceDrawer
        open={evidenceOpen}
        onClose={() => setEvidenceOpen(false)}
        evidence={evidence}
        isLoading={evidenceLoading}
        error={evidenceError}
        decisionTitle={decision.title}
      />

      <AiPromptModal
        open={promptMode !== null}
        onOpenChange={(o) => !o && setPromptMode(null)}
        getPrompt={(flavor) =>
          (promptMode === "enforce"
            ? buildDecisionEnforcementAiPrompt
            : buildDecisionAiPrompt)({
            decision: {
              title: decision.title,
              status,
              context: decision.context,
              decision: decision.decision,
              rationale: decision.rationale,
              alternatives: decision.alternatives,
              consequences: decision.consequences,
              affected_modules: linkedModules,
              affected_files: linkedFiles,
              staleness_score: decision.staleness_score,
              confidence: decision.confidence,
            },
            flavor,
          })
        }
        filePath={stripMarkdown(decision.title)}
        title={
          promptMode === "enforce"
            ? "AI decision enforcement"
            : "AI decision verification"
        }
        description={
          promptMode === "enforce"
            ? "A ready-to-paste prompt that has your AI agent audit the governed code for compliance with this decision and fix every violation it finds."
            : "A ready-to-paste prompt that has your AI agent check this decision against the current code and recommend whether to keep, update, or retire it."
        }
      />
    </div>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  // `valueClassName` went with the Staleness item, its only caller: it existed
  // to paint that percentage red above 0.5, which is the thing this change
  // removed. Module-private, so nothing outside can be relying on it.
  return (
    <div>
      <dt className={MICRO_LABEL}>{label}</dt>
      <dd className="mt-0.5 font-mono text-xs tabular-nums text-[var(--color-text-secondary)]">
        {value}
      </dd>
    </div>
  );
}

/** Older/hosted backends can omit decision timestamps — render "—" instead of
 *  "Invalid Date" for a missing or unparseable value.
 *
 *  Uses `formatDate`, which pins the locale, rather than
 *  `toLocaleDateString()`. The latter formats against the *runtime's* locale,
 *  so the server rendered "23/06/2026" and the browser "23/6/2026" — a
 *  hydration mismatch that made React throw away and re-render the tree on
 *  every visit to a decision. */
function formatDateOrDash(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : formatDate(d);
}

function PrevNextLink({
  Link,
  href,
  label,
  children,
}: {
  Link: DecisionLinkComponent;
  href: string | null;
  label: string;
  children: React.ReactNode;
}) {
  if (!href) {
    return (
      <span className="inline-flex items-center gap-0.5 rounded-md border border-[var(--color-border-default)] px-2 py-1 text-xs text-[var(--color-text-tertiary)] opacity-50">
        {children}
      </span>
    );
  }
  return (
    <Link
      href={href}
      aria-label={label}
      className="inline-flex items-center gap-0.5 rounded-md border border-[var(--color-border-default)] px-2 py-1 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
    >
      {children}
    </Link>
  );
}

function Section({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <h2 className={MICRO_LABEL + " mb-2"}>{title}</h2>
      {/* Not `prose`: Tailwind Typography is banned on our markdown. It printed
          literal backticks through `code::before`, and `prose-invert` is a
          static class that cannot follow the theme, so this block was locked to
          dark styling in light mode. `Markdown` is themed through our tokens. */}
      <Markdown content={text} />
    </div>
  );
}

function ListSection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h2 className={MICRO_LABEL + " mb-2"}>{title}</h2>
      <ul className="list-disc space-y-1.5 pl-5 text-[15.5px] leading-relaxed text-[var(--color-text-primary)]">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
