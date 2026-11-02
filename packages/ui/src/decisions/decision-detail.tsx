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
import type { ChatContext } from "@repowise-dev/types/chat";
import {
  decisionAcceptanceBlockers,
  decisionSourceLabel,
  type DecisionRecord,
  type DecisionStatus,
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
import { AskAboutThis } from "../chat/ask-about-this";
import { DecisionStatusMark } from "./decision-status-mark";
import { DecisionEvidenceDrawer } from "./decision-evidence-drawer";
import { DecisionLineage } from "./decision-lineage";
import { describeRecordStaleness } from "./decision-staleness";
import type {
  DecisionDetailAdapter,
  DecisionLinkComponent,
} from "./decision-detail-adapter";
import { toFriendlyMessage } from "../lib/errors";
import { GapList, ReasonField, isKnownGap, isReasonGap, refusedGaps } from "./decision-confirm";
import { DecisionAcceptedLine, DecisionAppliedLine, DecisionOrigin } from "./decision-origin";

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

// Confirm has no dialog: it is not destructive, and the reason field already
// asks for the one thing a second click would. Its toast offers the undo.
const CONFIRM_COPY: Record<
  string,
  { title: string; description: string; confirmLabel: string; destructive: boolean }
> = {
  deprecated: {
    title: "Deprecate decision?",
    description:
      "This will mark the decision as deprecated. Existing references remain but it will no longer be considered current.",
    confirmLabel: "Deprecate",
    destructive: true,
  },
  dismissed: {
    title: "Dismiss decision?",
    description:
      "It leaves the list, stops guiding reviews and won't be suggested again. Nothing in your code changes.",
    confirmLabel: "Dismiss",
    destructive: true,
  },
};

/** What the success toast says happened, in the bot's words. */
const DONE_COPY: Partial<Record<DecisionStatus, string>> = {
  active: "Decision accepted",
  dismissed: "Decision dismissed",
  deprecated: "Decision deprecated",
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
  // The reason typed in place, and what the server named when it refused a
  // confirm. The second wins over the local reading until the next attempt.
  const [reason, setReason] = React.useState("");
  const [refused, setRefused] = React.useState<string[] | null>(null);
  // Which AI-prompt flavor the modal shows: verification ("is this decision
  // still true?") or enforcement ("make the code conform to it").
  const [promptMode, setPromptMode] = React.useState<"verify" | "enforce" | null>(null);
  // One subject for both the chat control and the prompt modal's "Ask in chat".
  const chatContext: ChatContext = {
    kind: "decision",
    label: stripMarkdown(decision.title),
    target: decision.id,
    targetKind: "decision",
  };

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

  // The same check the review queue and its drawer run, over the scope as
  // edited here, so adding files below clears that gap without a reload.
  const gaps = React.useMemo(
    () =>
      refused ??
      decisionAcceptanceBlockers({
        ...decision,
        affected_files: linkedFiles,
        affected_modules: linkedModules,
      }),
    [refused, decision, linkedFiles, linkedModules],
  );
  // A refusal reads against the scope it was sent with; editing the scope
  // hands the reading back to the local check.
  React.useEffect(() => setRefused(null), [linkedFiles, linkedModules]);
  const needsReason = gaps.some(isReasonGap);
  const otherGaps = gaps.filter((g) => !isReasonGap(g));
  const confirmable = otherGaps.length === 0 && (!needsReason || reason.trim() !== "");

  const applyStatusChange = async (newStatus: DecisionStatus) => {
    const previous = status;
    const sentReason = newStatus === "active" ? reason.trim() : "";
    setLoading(true);
    try {
      await adapter.patchDecision({
        status: newStatus,
        ...(sentReason ? { reason: sentReason } : {}),
      });
      setStatus(newStatus);
      setPendingStatus(null);
      setRefused(null);
      // Where the host can reverse a review, Undo puts the decision back where
      // it was. Otherwise accepting has no inverse on the server: taking
      // authority back is a dismissal, so its undo opens that, with the dialog
      // saying what it does.
      const undoReview = adapter.undoReview;
      const reversible = undoReview && (newStatus === "active" || newStatus === "dismissed");
      const undo = reversible
        ? async () => {
            try {
              await undoReview();
              setStatus(previous);
              toast.success("Undone. It is back where it was.");
            } catch (err) {
              toast.error(`Couldn't undo: ${toFriendlyMessage(err)}`);
            }
          }
        : newStatus === "active"
          ? () => setPendingStatus("dismissed")
          : async () => {
              try {
                await adapter.patchDecision({ status: previous });
                setStatus(previous);
              } catch (err) {
                toast.error(`Couldn't undo: ${toFriendlyMessage(err)}`);
              }
            };
      toast.success(DONE_COPY[newStatus] ?? `Decision marked ${newStatus.replace(/_/g, " ")}`, {
        action: { label: "Undo", onClick: () => void undo() },
        duration: 6000,
      });
    } catch (err) {
      // Only gaps the page can explain replace the local reading. Any other
      // refusal is a one-off sentence, said once in the toast, so it never
      // locks Confirm with no way out.
      const named = refusedGaps(err);
      if (named.length > 0 && named.every(isKnownGap)) {
        setRefused(named);
        toast.error("Couldn't accept this decision", {
          description: "It still needs what's listed under Confirm.",
        });
      } else {
        toast.error(
          err instanceof Error
            ? `Couldn't update decision: ${toFriendlyMessage(err)}`
            : "Couldn't update decision",
        );
      }
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
            {/* Who accepted it is said beside Undo, below. */}
            <DecisionOrigin decision={{ ...decision, status }} accepter={false} />
            {decision.verification && (
              <VerificationBadge verification={decision.verification} />
            )}
            <AskAboutThis
              context={chatContext}
              question={`Why was this decision made, what evidence supports it, and has later work conflicted with it?`}
              label="Ask about this decision"
            />
          </div>
        </div>

        {/* One mono ribbon, tabular, instead of four sans sentences.
            Staleness left the ribbon: a proportion has no reading at a glance,
            and the thing a reader wants from it is a sentence, which is now
            below. Confidence left with it, being source rank times
            verification, so it restated the Source cell beside it as a
            percentage and every record from one source carried one number. */}
        <dl className="flex flex-wrap gap-x-8 gap-y-2">
          <MetaItem label="Source" value={decisionSourceLabel(decision.source)} />
          <MetaItem label="Recorded" value={formatDate(decision.created_at)} />
        </dl>
        <DecisionAppliedLine decision={decision} />

        <div className="flex flex-wrap items-center gap-2">
          {/* One agent action per page: a candidate the host can write a
              review prompt for gets that, copied in one click; otherwise the
              shared verification prompt. */}
          {status === "proposed" && adapter.reviewPrompt ? (
            // The button's own label for a copy: "Copy prompt".
            <AiPromptButton copyText={() => adapter.reviewPrompt!(decision)} />
          ) : (
            <AiPromptButton
              label={status === "proposed" ? "Verify & confirm with AI" : "Verify with AI"}
              onClick={() => setPromptMode("verify")}
            />
          )}
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
                Affected files last changed {formatDate(decision.last_code_change)}.
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
      <div className="space-y-3 border-t border-[var(--color-border-default)] pt-4">
        {status === "proposed" && needsReason && otherGaps.length === 0 && (
          <ReasonField
            id={`decision-reason-${decision.id}`}
            value={reason}
            onChange={(v) => {
              setReason(v);
              setRefused(null);
            }}
            disabled={loading}
          />
        )}
        <div className="flex flex-wrap items-center gap-2">
          {status === "proposed" && (
            <>
              <button
                onClick={() => handleStatusChange("active")}
                disabled={loading || !confirmable}
                // Ink, not a filled green: green/amber/red are reserved for
                // health readouts where they carry a band. This is the page's
                // one primary action, and primary actions are ink.
                className="rounded-md bg-[var(--color-action-primary)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-on-action)] hover:bg-[var(--color-action-primary-hover)] disabled:opacity-50"
              >
                Confirm
              </button>
              {/* Dismissing a proposal and deprecating a decision are not the
                  same act, and this button used to send `deprecated` for both.
                  A dismissal says the proposal was never right, so the engine
                  never raises it again; deprecation says a real decision has
                  been retired, and the record keeps being re-derived and keeps
                  its row. Sending the wrong one meant a dismissed proposal came
                  back on the next index. */}
              <button
                onClick={() => handleStatusChange("dismissed")}
                disabled={loading}
                className="rounded-md border border-[var(--color-border-default)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] disabled:opacity-50"
              >
                Dismiss
              </button>
            </>
          )}
          {status === "active" && (
            <>
              {/* Undo is a dismissal: there is no un-accept, and a decision
                  accepted by mistake was never right, which is what dismiss
                  records. Deprecate is for one that was right and is retired. */}
              <button
                onClick={() => handleStatusChange("dismissed")}
                disabled={loading}
                className="rounded-md border border-[var(--color-border-default)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] disabled:opacity-50"
              >
                Undo
              </button>
              <button
                onClick={() => handleStatusChange("deprecated")}
                disabled={loading}
                className="rounded-md border border-[var(--color-error)]/40 px-3 py-1.5 text-sm text-[var(--color-error)] hover:bg-[var(--color-error)]/10 disabled:opacity-50"
              >
                Deprecate
              </button>
              <DecisionAcceptedLine decision={{ ...decision, status }} className="ml-1" />
            </>
          )}
        </div>
        {status === "proposed" && <GapList gaps={otherGaps} id={decision.id} />}
        {status === "proposed" && needsReason && otherGaps.length === 0 && !reason.trim() && (
          <p className="text-xs text-[var(--color-text-secondary)]">Add a reason above to confirm.</p>
        )}
        {status === "proposed" && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Confirm saves this as a decision your team follows. Dismiss hides it
            and it won&apos;t be suggested again. Neither changes any code.
          </p>
        )}
        {status === "active" && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Undo dismisses a decision accepted by mistake. Deprecate retires one
            that no longer holds: it stays for history but stops guiding work.
            Neither changes any code.
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
        chatContext={chatContext}
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
