"use client";

import { useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Save, X } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { DEFAULT_WIKI_STYLE, WIKI_STYLES } from "@repowise-dev/types";
import type { RepoSettingsValue, WikiStyle } from "@repowise-dev/types/settings";
import { SettingsRow, SettingsRows, useSaveState } from "./settings-primitives";

const SUGGESTIONS = [
  "vendor/",
  "dist/",
  "build/",
  "node_modules/",
  "*.generated.*",
  "**/fixtures/**",
];

type Field = keyof RepoSettingsValue;
type TextField = "name" | "default_branch";

function arraysEqual(a: string[], b: string[]) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function withStyle(value: RepoSettingsValue): RepoSettingsValue {
  return { ...value, wiki_style: value.wiki_style ?? DEFAULT_WIKI_STYLE };
}

export interface GeneralFormProps {
  /** Initial form value. Also used as the "saved" baseline for dirty-check. */
  value: RepoSettingsValue;
  /**
   * Optional save handler. When omitted, the form renders read-only — useful
   * for hosted, where multi-tenant settings persistence is deferred to the
   * Phase 5 RBAC data-model rewrite. Rejecting rolls an autosaved field back.
   */
  onSubmit?: (next: RepoSettingsValue) => Promise<void>;
  /**
   * Save each field as it is committed (blur or Enter for text, on click for
   * the rest) with a "Saved" tick on its row, instead of one Save button.
   */
  autosave?: boolean;
  /** Read-only fields shown alongside the editable form. */
  localPath?: string;
  remoteUrl?: string;
  /**
   * Copy displayed underneath the disabled save button when `onSubmit` is
   * absent. Defaults to a generic "managed elsewhere" line.
   */
  disabledHint?: string;
}

/** The form's values, the last saved baseline, and both save models. */
function useGeneralFormState(value: RepoSettingsValue, onSubmit: GeneralFormProps["onSubmit"]) {
  const [form, setForm] = useState<RepoSettingsValue>(() => withStyle(value));
  const [saving, setSaving] = useState(false);
  const [field, setField] = useState<Field | null>(null);
  const save = useSaveState();
  // Writes are whole-object, so each builds on the newest intended value;
  // a failure puts its field back to what the server last accepted.
  const saved = useRef<RepoSettingsValue>(withStyle(value));
  const pending = useRef<RepoSettingsValue>(withStyle(value));
  // Which write the baseline came from, so a slower older success never
  // replaces a newer one, and a superseded success still counts as saved.
  const writeSeq = useRef(0);
  const savedSeq = useRef(0);

  const set = <K extends Field>(key: K, next: RepoSettingsValue[K]) =>
    setForm((f) => ({ ...f, [key]: next }));

  async function commit<K extends Field>(key: K, next: RepoSettingsValue[K]) {
    if (!onSubmit) return;
    const nextValue = { ...pending.current, [key]: next };
    pending.current = nextValue;
    set(key, next);
    setField(key);
    const mine = ++writeSeq.current;
    const outcome = await save.track(async () => {
      await onSubmit(nextValue);
      if (mine > savedSeq.current) {
        saved.current = nextValue;
        savedSeq.current = mine;
      }
    });
    // Only the field that failed goes back; an older write still in flight
    // for another field keeps its value and rolls back itself if it fails.
    if (outcome === "failed") {
      const accepted = saved.current[key];
      pending.current = { ...pending.current, [key]: accepted };
      set(key, accepted);
    }
  }

  async function submitAll() {
    if (!onSubmit) return;
    setSaving(true);
    try {
      await onSubmit(form);
      saved.current = form;
      pending.current = form;
    } finally {
      setSaving(false);
    }
  }

  const hasChanges =
    form.name !== saved.current.name ||
    form.default_branch !== saved.current.default_branch ||
    !arraysEqual(form.exclude_patterns, saved.current.exclude_patterns) ||
    form.wiki_style !== saved.current.wiki_style;

  const rowSave = (key: Field) =>
    field === key ? { saveState: save.state, saveError: save.error } : {};

  return { form, set, commit, submitAll, saving, hasChanges, saved, pending, rowSave };
}

export function GeneralForm({
  value,
  onSubmit,
  autosave = false,
  localPath,
  remoteUrl,
  disabledHint = "Editing repository settings is not yet available on this surface.",
}: GeneralFormProps) {
  const state = useGeneralFormState(value, onSubmit);
  const { form, set, saved, pending, rowSave } = state;
  const readOnly = onSubmit === undefined;
  const live = autosave && !readOnly;

  /** Text fields: autosave commits on blur or Enter; an emptied field reverts. */
  function commitText(key: TextField) {
    if (!live) return;
    const next = form[key].trim();
    if (!next) set(key, saved.current[key]);
    else if (next !== pending.current[key]) void state.commit(key, next);
  }

  function setPatterns(next: string[]) {
    if (live) void state.commit("exclude_patterns", next);
    else set("exclude_patterns", next);
  }

  function setStyle(next: WikiStyle) {
    if (next === form.wiki_style) return;
    if (live) void state.commit("wiki_style", next);
    else set("wiki_style", next);
  }

  const textProps = (key: TextField) => ({
    value: form[key],
    onChange: (e: ChangeEvent<HTMLInputElement>) => set(key, e.target.value),
    onBlur: () => commitText(key),
    onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") commitText(key);
    },
    className: "max-w-sm",
    disabled: readOnly,
  });

  return (
    <div className="space-y-4">
      <SettingsRows>
        <SettingsRow label="Repository name" htmlFor="repo-name" {...rowSave("name")}>
          <Input id="repo-name" placeholder="my-project" {...textProps("name")} />
        </SettingsRow>
        <SettingsRow
          label="Default branch"
          htmlFor="repo-branch"
          hint="The branch that gets indexed."
          {...rowSave("default_branch")}
        >
          <Input id="repo-branch" placeholder="main" {...textProps("default_branch")} />
        </SettingsRow>
        {localPath && (
          <SettingsRow label="Local path" hint="Fixed once the repository is registered.">
            <p className="break-all font-mono text-sm text-[var(--color-text-secondary)]">{localPath}</p>
          </SettingsRow>
        )}
        {remoteUrl && (
          <SettingsRow label="Remote URL">
            <p className="break-all font-mono text-sm text-[var(--color-text-secondary)]">{remoteUrl}</p>
          </SettingsRow>
        )}
        <SettingsRow
          label="Excluded paths"
          htmlFor="exclude-pattern"
          hint="Gitignore-style patterns, relative to the repo root, that indexing and generation skip. .gitignore and .repowiseIgnore always apply."
          {...rowSave("exclude_patterns")}
        >
          <ExcludedPaths patterns={form.exclude_patterns} readOnly={readOnly} onChange={setPatterns} />
        </SettingsRow>
        <SettingsRow
          label="Documentation style"
          hint="The voice and density of generated pages. Changing it offers to regenerate the docs."
          {...rowSave("wiki_style")}
        >
          <StylePicker value={form.wiki_style ?? DEFAULT_WIKI_STYLE} readOnly={readOnly} onChange={setStyle} />
        </SettingsRow>
      </SettingsRows>

      {!live && (
        <div className="space-y-1.5">
          <Button
            onClick={() => void state.submitAll()}
            disabled={readOnly || !state.hasChanges || state.saving}
            size="sm"
            className="gap-2"
          >
            <Save className="h-3.5 w-3.5" />
            {state.saving ? "Saving…" : "Save changes"}
          </Button>
          {readOnly && <p className="text-xs text-[var(--color-text-tertiary)]">{disabledHint}</p>}
        </div>
      )}
    </div>
  );
}

function ExcludedPaths({
  patterns,
  readOnly,
  onChange,
}: {
  patterns: string[];
  readOnly: boolean;
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function add(pattern: string) {
    const trimmed = pattern.trim();
    if (!trimmed || patterns.includes(trimmed)) return;
    onChange([...patterns, trimmed]);
    setDraft("");
  }

  return (
    <div className="space-y-3">
      {patterns.length === 0 ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">No custom patterns.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {patterns.map((p) => (
            <span
              key={p}
              className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-2.5 py-0.5 font-mono text-xs text-[var(--color-text-primary)]"
            >
              {p}
              {!readOnly && (
                <button
                  type="button"
                  onClick={() => onChange(patterns.filter((x) => x !== p))}
                  className="text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
                  aria-label={`Remove ${p}`}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {!readOnly && (
        <>
          <div className="flex max-w-sm gap-2">
            <Input
              id="exclude-pattern"
              aria-label="New excluded pattern"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  add(draft);
                }
              }}
              placeholder="e.g. vendor/, src/generated/**"
              className="font-mono text-xs"
            />
            <Button type="button" variant="outline" size="sm" onClick={() => add(draft)} disabled={!draft.trim()}>
              Add
            </Button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => add(suggestion)}
                disabled={patterns.includes(suggestion)}
                className="rounded-full border border-[var(--color-border-default)] px-2.5 py-0.5 font-mono text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-inset)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
              >
                + {suggestion}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function StylePicker({
  value,
  readOnly,
  onChange,
}: {
  value: WikiStyle;
  readOnly: boolean;
  onChange: (next: WikiStyle) => void;
}) {
  return (
    <div role="radiogroup" aria-label="Documentation style" className="grid gap-2">
      {WIKI_STYLES.map((style) => {
        const selected = value === style.id;
        return (
          <button
            key={style.id}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={readOnly}
            onClick={() => onChange(style.id)}
            className={`rounded-md border px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
              selected
                ? "border-[var(--color-border-accent)] bg-[var(--color-bg-inset)]"
                : "border-[var(--color-border-default)] hover:bg-[var(--color-bg-inset)]"
            }`}
          >
            <span className="block text-sm font-medium text-[var(--color-text-primary)]">{style.label}</span>
            <span className="block text-xs text-[var(--color-text-secondary)]">{style.description}</span>
          </button>
        );
      })}
    </div>
  );
}
