"use client";

import * as React from "react";
import { Folder, FileText, Plus, X } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { cn } from "../lib/cn";

/**
 * Inline chip editor for the `affected_modules` + `affected_files` linkage
 * on a decision. Presentational only — the parent owns persistence.
 *
 * The host wires `suggestions` (typically module_paths from
 * /api/repos/{id}/modules/health) for inline autocomplete; users can also
 * type any path freehand.
 */
interface ModuleLinkEditorProps {
  modules: string[];
  files: string[];
  suggestions?: string[] | undefined;
  /** True while the parent is persisting a save. */
  saving?: boolean | undefined;
  /** Disables interactivity (e.g. while loading the initial record). */
  disabled?: boolean | undefined;
  onSave: (next: { modules: string[]; files: string[] }) => Promise<void> | void;
  className?: string | undefined;
}

export function ModuleLinkEditor({
  modules,
  files,
  suggestions,
  saving = false,
  disabled = false,
  onSave,
  className,
}: ModuleLinkEditorProps) {
  const [localModules, setLocalModules] = React.useState(modules);
  const [localFiles, setLocalFiles] = React.useState(files);
  const [moduleDraft, setModuleDraft] = React.useState("");
  const [fileDraft, setFileDraft] = React.useState("");

  // Keep local state in sync if the parent reloads the decision.
  React.useEffect(() => setLocalModules(modules), [modules]);
  React.useEffect(() => setLocalFiles(files), [files]);

  const dirty =
    !arraysEqual(localModules, modules) || !arraysEqual(localFiles, files);

  const filteredSuggestions = React.useMemo(() => {
    if (!suggestions || moduleDraft.trim().length === 0) return [];
    const q = moduleDraft.trim().toLowerCase();
    return suggestions
      .filter(
        (s) =>
          s.toLowerCase().includes(q) && !localModules.includes(s),
      )
      .slice(0, 6);
  }, [suggestions, moduleDraft, localModules]);

  const addModule = (value: string) => {
    const v = value.trim();
    if (!v) return;
    if (localModules.includes(v)) return;
    setLocalModules([...localModules, v]);
    setModuleDraft("");
  };

  const addFile = (value: string) => {
    const v = value.trim();
    if (!v) return;
    if (localFiles.includes(v)) return;
    setLocalFiles([...localFiles, v]);
    setFileDraft("");
  };

  const handleSave = async () => {
    await onSave({ modules: localModules, files: localFiles });
  };

  const handleReset = () => {
    setLocalModules(modules);
    setLocalFiles(files);
    setModuleDraft("");
    setFileDraft("");
  };

  return (
    <div
      className={cn(
        "rounded-lg border border-[var(--color-border-default)] p-4 space-y-4",
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-[var(--color-text-secondary)]">
          Governance scope
        </h3>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Link this decision to the modules and files it governs.
        </p>
      </div>

      {/* Modules */}
      <Field
        icon={<Folder className="h-3.5 w-3.5" />}
        label="Modules"
        chips={localModules}
        onRemove={(m) => setLocalModules(localModules.filter((x) => x !== m))}
        disabled={disabled || saving}
      >
        <div className="relative">
          <div className="flex gap-2">
            <Input
              value={moduleDraft}
              onChange={(e) => setModuleDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addModule(moduleDraft);
                }
              }}
              placeholder="packages/server"
              disabled={disabled || saving}
              className="font-mono text-xs"
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => addModule(moduleDraft)}
              disabled={disabled || saving || moduleDraft.trim().length === 0}
            >
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
          {filteredSuggestions.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] shadow-lg">
              {filteredSuggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => addModule(s)}
                  className="block w-full px-3 py-1.5 text-left font-mono text-xs text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>
      </Field>

      {/* Files */}
      <Field
        icon={<FileText className="h-3.5 w-3.5" />}
        label="Files"
        chips={localFiles}
        onRemove={(f) => setLocalFiles(localFiles.filter((x) => x !== f))}
        disabled={disabled || saving}
      >
        <div className="flex gap-2">
          <Input
            value={fileDraft}
            onChange={(e) => setFileDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addFile(fileDraft);
              }
            }}
            placeholder="src/server/app.py"
            disabled={disabled || saving}
            className="font-mono text-xs"
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => addFile(fileDraft)}
            disabled={disabled || saving || fileDraft.trim().length === 0}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </Field>

      {/* Footer actions */}
      <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border-default)] pt-3">
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {dirty ? "Unsaved changes" : "All linkage saved."}
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={handleReset}
            disabled={!dirty || saving}
          >
            Reset
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={handleSave}
            disabled={!dirty || saving}
          >
            {saving ? "Saving…" : "Save linkage"}
          </Button>
        </div>
      </div>
    </div>
  );
}

interface FieldProps {
  icon: React.ReactNode;
  label: string;
  chips: string[];
  onRemove: (value: string) => void;
  disabled?: boolean | undefined;
  children: React.ReactNode;
}

function Field({ icon, label, chips, onRemove, disabled, children }: FieldProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-secondary)]">
        {icon}
        <span>{label}</span>
        <span className="text-[var(--color-text-tertiary)] font-normal">
          ({chips.length})
        </span>
      </div>
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span
              key={c}
              className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-0.5 font-mono text-xs text-[var(--color-text-primary)]"
            >
              <span className="max-w-[260px] truncate" title={c}>
                {c}
              </span>
              <button
                type="button"
                onClick={() => onRemove(c)}
                disabled={disabled}
                className="rounded-full p-0.5 hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                aria-label={`Remove ${c}`}
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </span>
          ))}
        </div>
      )}
      {children}
    </div>
  );
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}
