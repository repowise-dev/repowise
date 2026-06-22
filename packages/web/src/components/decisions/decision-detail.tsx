"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { toast } from "sonner";
import Link from "next/link";
import { ChevronLeft, ChevronRight, FileSearch, Flame, GitCommitHorizontal } from "lucide-react";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { stripMarkdown } from "@repowise-dev/ui/lib/format";
import { ConfirmDialog } from "@repowise-dev/ui/ui/confirm-dialog";
import { ModuleLinkEditor } from "@repowise-dev/ui/decisions/module-link-editor";
import { VerificationBadge } from "@repowise-dev/ui/decisions/verification-badge";
import { DecisionEvidenceDrawer } from "@repowise-dev/ui/decisions/decision-evidence-drawer";
import { DecisionLineage } from "@repowise-dev/ui/decisions/decision-lineage";
import { AiPromptButton, AiPromptModal, buildDecisionAiPrompt } from "@repowise-dev/ui/health";
import {
  getDecisionEvidence,
  getDecisionLineage,
  listDecisions,
  patchDecision,
} from "@/lib/api/decisions";
import { listModuleHealth } from "@/lib/api/modules";
import type { DecisionRecordResponse } from "@/lib/api/types";

const STATUS_VARIANT: Record<string, "default" | "fresh" | "stale" | "outdated" | "outline" | "accent"> = {
  active: "fresh",
  proposed: "accent",
  deprecated: "outdated",
  superseded: "outline",
};

interface DecisionDetailProps {
  decision: DecisionRecordResponse;
  repoId: string;
}

const CONFIRM_COPY: Record<string, { title: string; description: string; confirmLabel: string; destructive: boolean }> = {
  active: {
    title: "Confirm decision?",
    description: "Mark this decision as active.",
    confirmLabel: "Confirm",
    destructive: false,
  },
  deprecated: {
    title: "Deprecate decision?",
    description: "This will mark the decision as deprecated. Existing references remain but it will no longer be considered current.",
    confirmLabel: "Deprecate",
    destructive: true,
  },
};

export function DecisionDetail({ decision, repoId }: DecisionDetailProps) {
  const [status, setStatus] = React.useState(decision.status);
  const [loading, setLoading] = React.useState(false);
  const [pendingStatus, setPendingStatus] = React.useState<string | null>(null);
  const [linkedModules, setLinkedModules] = React.useState(decision.affected_modules);
  const [linkedFiles, setLinkedFiles] = React.useState(decision.affected_files);
  const [linkageSaving, setLinkageSaving] = React.useState(false);
  const [evidenceOpen, setEvidenceOpen] = React.useState(false);
  const [promptOpen, setPromptOpen] = React.useState(false);

  // Lineage: cheap, load eagerly so the Evolution timeline renders when present.
  const { data: lineage } = useSWR(
    `decision-lineage:${repoId}:${decision.id}`,
    () => getDecisionLineage(repoId, decision.id),
    { revalidateOnFocus: false },
  );

  // Evidence: lazy — only fetched once the drawer is opened.
  const {
    data: evidence,
    error: evidenceError,
    isLoading: evidenceLoading,
  } = useSWR(
    evidenceOpen ? `decision-evidence:${repoId}:${decision.id}` : null,
    () => getDecisionEvidence(repoId, decision.id),
    { revalidateOnFocus: false },
  );

  // Sibling list for prev/next navigation — same ordering as the list page.
  const { data: siblings } = useSWR(
    `decisions-siblings:${repoId}`,
    () => listDecisions(repoId, { include_proposed: true, limit: 100 }),
    { revalidateOnFocus: false },
  );
  const { prevId, nextId } = React.useMemo(() => {
    const ids = (siblings ?? []).map((d) => d.id);
    const idx = ids.indexOf(decision.id);
    return {
      prevId: idx > 0 ? ids[idx - 1] : null,
      nextId: idx >= 0 && idx < ids.length - 1 ? ids[idx + 1] : null,
    };
  }, [siblings, decision.id]);

  // Suggestions for the module autocomplete — top-level modules indexed for
  // this repo. Loaded once; cheap to cache.
  const { data: moduleHealth } = useSWR(
    `module-health-suggestions:${repoId}`,
    () => listModuleHealth(repoId, { sort: "file_count", limit: 500 }),
    { revalidateOnFocus: false },
  );
  const moduleSuggestions = React.useMemo(
    () => (moduleHealth?.items ?? []).map((m) => m.module_path),
    [moduleHealth],
  );

  const saveLinkage = async (next: { modules: string[]; files: string[] }) => {
    const previousModules = linkedModules;
    const previousFiles = linkedFiles;
    setLinkageSaving(true);
    try {
      await patchDecision(repoId, decision.id, {
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
              await patchDecision(repoId, decision.id, {
                affected_modules: previousModules,
                affected_files: previousFiles,
              });
              setLinkedModules(previousModules);
              setLinkedFiles(previousFiles);
            } catch (err) {
              toast.error(
                err instanceof Error ? `Couldn't undo: ${err.message}` : "Couldn't undo",
              );
            }
          },
        },
        duration: 6000,
      });
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `Couldn't save linkage: ${err.message}`
          : "Couldn't save linkage",
      );
    } finally {
      setLinkageSaving(false);
    }
  };

  const applyStatusChange = async (newStatus: string) => {
    const previous = status;
    setLoading(true);
    try {
      await patchDecision(repoId, decision.id, { status: newStatus });
      setStatus(newStatus as typeof status);
      setPendingStatus(null);
      toast.success(`Decision marked ${newStatus.replace(/_/g, " ")}`, {
        action: {
          label: "Undo",
          onClick: async () => {
            try {
              await patchDecision(repoId, decision.id, { status: previous });
              setStatus(previous);
            } catch (err) {
              toast.error(
                err instanceof Error ? `Couldn't undo: ${err.message}` : "Couldn't undo",
              );
            }
          },
        },
        duration: 6000,
      });
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `Couldn't update decision: ${err.message}`
          : "Couldn't update decision",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleStatusChange = (newStatus: string) => {
    if (CONFIRM_COPY[newStatus]) {
      setPendingStatus(newStatus);
    } else {
      void applyStatusChange(newStatus);
    }
  };

  const confirmConfig = pendingStatus ? CONFIRM_COPY[pendingStatus] : null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <Link
          href={`/repos/${repoId}/decisions`}
          className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          All decisions
        </Link>
        <div className="flex items-center gap-1">
          <PrevNextLink
            href={prevId ? `/repos/${repoId}/decisions/${prevId}` : null}
            label="Previous decision"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Prev
          </PrevNextLink>
          <PrevNextLink
            href={nextId ? `/repos/${repoId}/decisions/${nextId}` : null}
            label="Next decision"
          >
            Next <ChevronRight className="h-3.5 w-3.5" />
          </PrevNextLink>
        </div>
      </div>
      {/* Header */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
            {stripMarkdown(decision.title)}
          </h1>
          <Badge variant={STATUS_VARIANT[status] ?? "outline"}>{status}</Badge>
          {decision.verification && (
            <VerificationBadge verification={decision.verification} />
          )}
          <AiPromptButton
            label={status === "proposed" ? "Verify & confirm with AI" : "Verify with AI"}
            onClick={() => setPromptOpen(true)}
            className="ml-auto"
          />
          <button
            type="button"
            onClick={() => setEvidenceOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
          >
            <FileSearch className="h-3.5 w-3.5" />
            Evidence
          </button>
        </div>
        <div className="flex gap-4 text-sm text-[var(--color-text-tertiary)]">
          <span>Source: {decision.source}</span>
          <span>Confidence: {Math.round(decision.confidence * 100)}%</span>
          {decision.staleness_score > 0 && (
            <span className={decision.staleness_score > 0.5 ? "text-[var(--color-error)]" : ""}>
              Staleness: {decision.staleness_score.toFixed(2)}
            </span>
          )}
          <span>Created: {new Date(decision.created_at).toLocaleDateString()}</span>
        </div>
      </div>

      {/* Stale warning */}
      {decision.staleness_score > 0.5 && (
        <div className="rounded-md border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 px-4 py-3 text-sm text-[var(--color-warning)]">
          This decision may be stale — affected files have changed significantly since it was recorded.
        </div>
      )}

      {/* Evolution / lineage chain */}
      {lineage && lineage.length > 1 && (
        <DecisionLineage lineage={lineage} repoId={repoId} LinkComponent={Link} />
      )}

      {/* Content sections */}
      <div className="space-y-4">
        {decision.context && (
          <Section title="Context" text={decision.context} />
        )}
        {decision.decision && (
          <Section title="Decision" text={decision.decision} />
        )}
        {decision.rationale && (
          <Section title="Rationale" text={decision.rationale} />
        )}
        {decision.alternatives.length > 0 && (
          <ListSection title="Alternatives Rejected" items={decision.alternatives} />
        )}
        {decision.consequences.length > 0 && (
          <ListSection title="Consequences & Tradeoffs" items={decision.consequences} />
        )}
      </div>

      {/* Governance linkage — writable editor + evidence card */}
      <div className="grid gap-4 sm:grid-cols-2">
        <ModuleLinkEditor
          modules={linkedModules}
          files={linkedFiles}
          suggestions={moduleSuggestions}
          saving={linkageSaving}
          onSave={saveLinkage}
        />

        {/* The full evidence rows live in the drawer (Evidence button); this
            card answers "what happened to the governed code since?". */}
        <div className="rounded-lg border border-[var(--color-border-default)] p-4">
          <h3 className="mb-2 text-sm font-medium text-[var(--color-text-secondary)]">
            Since this decision
          </h3>
          <div className="space-y-2 text-sm">
            {decision.last_code_change && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Affected files last changed{" "}
                {new Date(decision.last_code_change).toLocaleDateString()}.
              </p>
            )}
            <div className="flex flex-col gap-1.5">
              <Link
                href={`/repos/${repoId}/commits?sort=date`}
                className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline"
              >
                <GitCommitHorizontal className="h-3.5 w-3.5" />
                Recent commits to this repo
              </Link>
              <Link
                href={`/repos/${repoId}/code-health?tab=hotspots`}
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
                      href={`/repos/${repoId}/commits?commit=${c}`}
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

      {/* Tags */}
      {decision.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {decision.tags.map((tag) => (
            <span
              key={tag}
              className="inline-block rounded-full bg-[var(--color-bg-elevated)] px-2.5 py-0.5 text-xs font-medium text-[var(--color-text-secondary)]"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 border-t border-[var(--color-border-default)] pt-4">
        {status === "proposed" && (
          <>
            <button
              onClick={() => handleStatusChange("active")}
              disabled={loading}
              className="rounded-md bg-[var(--color-success)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-inverse)] hover:opacity-90 disabled:opacity-50"
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
      {confirmConfig && (
        <ConfirmDialog
          open={pendingStatus !== null}
          onOpenChange={(o) => !o && setPendingStatus(null)}
          title={confirmConfig.title}
          description={confirmConfig.description}
          confirmLabel={confirmConfig.confirmLabel}
          destructive={confirmConfig.destructive}
          loading={loading}
          onConfirm={() => applyStatusChange(pendingStatus!)}
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
        open={promptOpen}
        onOpenChange={setPromptOpen}
        getPrompt={(flavor) =>
          buildDecisionAiPrompt({
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
        title="AI decision verification"
        description="A ready-to-paste prompt that has your AI agent check this decision against the current code and recommend whether to keep, update, or retire it."
      />
    </div>
  );
}

function PrevNextLink({
  href,
  label,
  children,
}: {
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
      <h3 className="mb-1 text-sm font-medium text-[var(--color-text-secondary)]">{title}</h3>
      <div className="text-sm text-[var(--color-text-primary)] leading-relaxed prose prose-sm prose-invert max-w-none [&>p]:mb-2 [&>p:last-child]:mb-0">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
    </div>
  );
}

function ListSection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-[var(--color-text-secondary)]">{title}</h3>
      <ul className="list-disc space-y-0.5 pl-5 text-sm text-[var(--color-text-primary)]">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
