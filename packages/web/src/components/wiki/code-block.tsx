"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils/cn";

interface Props {
  code: string;
  language?: string;
  /** Pre-rendered HTML from Shiki — passed through as dangerouslySetInnerHTML */
  html: string;
}

export function CodeBlock({ code, language, html }: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="group relative my-4 rounded-lg border border-[var(--color-border-default)] overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-1.5 bg-[var(--color-bg-elevated)] border-b border-[var(--color-border-default)]">
        {language && (
          <span className="text-xs font-mono text-[var(--color-text-tertiary)]">
            {language}
          </span>
        )}
        <button
          onClick={copy}
          className={cn(
            "ml-auto flex items-center gap-1 text-xs text-[var(--color-text-tertiary)]",
            "hover:text-[var(--color-text-secondary)] transition-colors",
          )}
          aria-label={copied ? "Copied" : "Copy code"}
          aria-live="polite"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5" />
              Copied
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              Copy
            </>
          )}
        </button>
      </div>

      {/* Shiki-rendered HTML */}
      <div
        className="overflow-x-auto text-sm [&>pre]:p-4 [&>pre]:m-0 [&>pre]:bg-transparent!"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
