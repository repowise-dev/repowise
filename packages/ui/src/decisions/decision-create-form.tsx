"use client";

import * as React from "react";
import { toast } from "sonner";
import type { DecisionCreateInput } from "@repowise-dev/types/decisions";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";

/**
 * Record a decision by hand.
 *
 * The page told readers to go and run `repowise decision add` because there
 * was no create form and no POST call in the UI at all — the one thing the
 * decisions surface could not do was start a record.
 *
 * Presentation and form state only, per the convention the rest of this
 * directory follows: the host supplies `onSubmit`, so web and hosted post
 * through their own clients.
 *
 * The comma-separated list fields mirror the CLI's prompts rather than
 * inventing a chip editor. They are the fields people leave blank most often,
 * and a text input that accepts "a, b" needs no keyboard contract explained.
 *
 * Affected files is the one field that changes what the write produces. An
 * acceptance has to name what it governs, so an entry naming nothing is stored
 * as a candidate instead. That is the same outcome `repowise decision add`
 * gives the same input, and the form says which one is coming before the
 * button is pressed rather than reporting it in a failure toast afterwards.
 */

const FIELD = "space-y-1.5";

/** "a, b ,, c" → ["a", "b", "c"]. Blank stays blank rather than becoming [""]. */
function splitList(raw: string): string[] {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

const TEXTAREA_CLASS =
  "box-border min-w-0 flex w-full rounded-md border border-[var(--color-border-default)] " +
  "bg-[var(--color-bg-surface)] px-3 py-2 text-sm text-[var(--color-text-primary)] " +
  "transition-colors placeholder:text-[var(--color-text-tertiary)] focus-visible:outline-none " +
  "focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)] " +
  "disabled:cursor-not-allowed disabled:opacity-50";

export interface DecisionCreateFormProps {
  /** Persists the record. Rejecting is reported to the user and keeps the form open. */
  onSubmit: (input: DecisionCreateInput) => Promise<void>;
  /** Called after a successful write, e.g. to close a dialog and refresh a list. */
  onCreated?: () => void;
  onCancel?: () => void;
}

export function DecisionCreateForm({
  onSubmit,
  onCreated,
  onCancel,
}: DecisionCreateFormProps) {
  const [title, setTitle] = React.useState("");
  const [decision, setDecision] = React.useState("");
  const [context, setContext] = React.useState("");
  const [rationale, setRationale] = React.useState("");
  const [alternatives, setAlternatives] = React.useState("");
  const [consequences, setConsequences] = React.useState("");
  const [affectedFiles, setAffectedFiles] = React.useState("");
  const [tags, setTags] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  // Title and decision are the two the engine cannot default: a record with
  // neither a name nor a choice is not a decision. Everything else is optional,
  // as it is at the prompts.
  const canSubmit = title.trim().length > 0 && decision.trim().length > 0 && !saving;

  // Mirrors the server: scope present means the write records an acceptance.
  const governs = splitList(affectedFiles).length > 0;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    try {
      await onSubmit({
        title: title.trim(),
        decision: decision.trim(),
        context: context.trim(),
        rationale: rationale.trim(),
        alternatives: splitList(alternatives),
        consequences: splitList(consequences),
        affected_files: splitList(affectedFiles),
        tags: splitList(tags),
      });
      toast.success(
        governs
          ? "Decision recorded and confirmed"
          : "Saved as a candidate. Add the files it governs to confirm it.",
      );
      onCreated?.();
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `Couldn't record decision: ${err.message}`
          : "Couldn't record decision",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className={FIELD}>
        <Label htmlFor="decision-title">Title</Label>
        <Input
          id="decision-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Use JWT for authentication"
          disabled={saving}
          required
        />
      </div>

      <div className={FIELD}>
        <Label htmlFor="decision-decision">Decision</Label>
        <textarea
          id="decision-decision"
          value={decision}
          onChange={(e) => setDecision(e.target.value)}
          placeholder="What was chosen?"
          rows={2}
          disabled={saving}
          required
          className={TEXTAREA_CLASS}
        />
      </div>

      <div className={FIELD}>
        <Label htmlFor="decision-context">Context</Label>
        <textarea
          id="decision-context"
          value={context}
          onChange={(e) => setContext(e.target.value)}
          placeholder="What forced this decision?"
          rows={2}
          disabled={saving}
          className={TEXTAREA_CLASS}
        />
      </div>

      <div className={FIELD}>
        <Label htmlFor="decision-rationale">Rationale</Label>
        <textarea
          id="decision-rationale"
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          placeholder="Why this and not something else?"
          rows={2}
          disabled={saving}
          className={TEXTAREA_CLASS}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className={FIELD}>
          <Label htmlFor="decision-alternatives">Rejected alternatives</Label>
          <Input
            id="decision-alternatives"
            value={alternatives}
            onChange={(e) => setAlternatives(e.target.value)}
            placeholder="Comma-separated"
            disabled={saving}
          />
        </div>
        <div className={FIELD}>
          <Label htmlFor="decision-consequences">Tradeoffs</Label>
          <Input
            id="decision-consequences"
            value={consequences}
            onChange={(e) => setConsequences(e.target.value)}
            placeholder="Comma-separated"
            disabled={saving}
          />
        </div>
        <div className={FIELD}>
          <Label htmlFor="decision-tags">Tags</Label>
          <Input
            id="decision-tags"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="auth, security"
            disabled={saving}
          />
        </div>
      </div>

      <div className={FIELD}>
        <Label htmlFor="decision-files">Affected files</Label>
        <Input
          id="decision-files"
          value={affectedFiles}
          onChange={(e) => setAffectedFiles(e.target.value)}
          placeholder="src/auth/service.py, src/auth/middleware.py"
          disabled={saving}
          aria-describedby="decision-outcome"
        />
      </div>

      <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 pt-1">
        <p
          id="decision-outcome"
          aria-live="polite"
          className="min-w-0 flex-1 text-xs text-[var(--color-text-secondary)]"
        >
          {governs
            ? "Recorded as confirmed, because you are the person confirming it. It will govern the files above."
            : "Name the files it governs to confirm it. Without them it is saved as a candidate: nothing checks it against the code, and it does not reach an agent."}
        </p>
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        )}
        <Button type="submit" disabled={!canSubmit}>
          {saving
            ? "Recording…"
            : governs
              ? "Record decision"
              : "Save as candidate"}
        </Button>
      </div>
    </form>
  );
}
