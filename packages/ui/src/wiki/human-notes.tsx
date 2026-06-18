"use client";

import { useState } from "react";
import { Pencil, StickyNote, X } from "lucide-react";
import { Button } from "../ui/button";

/**
 * Human-curated note pinned above a page's generated content, editable in
 * place. Pure view/edit shell — the host supplies ``onSave`` (which persists
 * server-side; notes survive regeneration) and toast feedback.
 */
export function HumanNotes({
  initialNotes,
  onSave,
}: {
  initialNotes: string | null;
  /** Persist the note. Resolves to the stored value (or null when removed). */
  onSave: (value: string | null) => Promise<string | null>;
}) {
  const [notes, setNotes] = useState(initialNotes);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(initialNotes ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const stored = await onSave(draft.trim() || null);
      setNotes(stored);
      setDraft(stored ?? "");
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  if (!editing && !notes) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="mb-5 inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)]"
      >
        <StickyNote className="h-3.5 w-3.5" />
        Add a note for your team — it survives regeneration
      </button>
    );
  }

  return (
    <div className="mb-5 rounded-lg border border-[var(--color-border-accent)] bg-[var(--color-accent-blue)]/5 px-4 py-3">
      <div className="mb-1.5 flex items-center gap-1.5">
        <StickyNote className="h-3.5 w-3.5 text-[var(--color-accent-blue)]" />
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--color-accent-blue)]">
          Human Notes
        </span>
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            aria-label="Edit note"
            className="ml-auto text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
          >
            <Pencil className="h-3 w-3" />
          </button>
        )}
        {editing && (
          <button
            onClick={() => {
              setEditing(false);
              setDraft(notes ?? "");
            }}
            aria-label="Cancel editing"
            className="ml-auto text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={4}
            autoFocus
            aria-label="Human note"
            placeholder="Context the generated docs can't know — gotchas, history, plans…"
            className="w-full resize-y rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
          />
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => void save()} disabled={saving}>
              {saving ? "Saving…" : "Save note"}
            </Button>
            {notes && (
              <Button
                size="sm"
                variant="ghost"
                disabled={saving}
                onClick={() => {
                  setDraft("");
                  void save();
                }}
              >
                Remove note
              </Button>
            )}
          </div>
        </div>
      ) : (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text-secondary)]">
          {notes}
        </p>
      )}
    </div>
  );
}
