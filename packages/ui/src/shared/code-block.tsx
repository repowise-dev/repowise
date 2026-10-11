"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "../lib/cn";

const highlightedHtml = new Map<string, Promise<string>>();

function highlight(code: string, language: string) {
  const key = `${language}\u0000${code}`;
  const cached = highlightedHtml.get(key);
  if (cached) return cached;

  const pending = import("shiki").then(({ codeToHtml }) =>
    codeToHtml(code, {
      lang: language as never,
      themes: { light: "github-light", dark: "vesper" },
      defaultColor: false,
    }),
  );
  highlightedHtml.set(key, pending);
  pending.catch(() => highlightedHtml.delete(key));
  return pending;
}

interface CodeFrameProps {
  code: string;
  language: string;
  children: ReactNode;
  /** 12px code for narrow columns (dock, tool output); 14px otherwise. */
  compact?: boolean;
  /** Header label. Defaults to the language. */
  label?: string;
  className?: string;
}

export function CodeFrame({ code, language, children, compact = false, label, className }: CodeFrameProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className={cn("group/code my-4 min-w-0 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-inset)]", className)}>
      <div className="flex min-h-7 items-center justify-between pl-3 pr-1 pointer-coarse:min-h-8">
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
          {label ?? (language || "text")}
        </span>
        {/* Revealed on hover or focus with a mouse; always shown on touch. */}
        <button
          type="button"
          onClick={() => void copy()}
          className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-[var(--color-text-tertiary)] transition-opacity hover:text-[var(--color-text-primary)] focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] pointer-fine:opacity-0 pointer-fine:group-hover/code:opacity-100 pointer-coarse:h-8 motion-reduce:transition-none"
          aria-label="Copy code"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {/* Selectable region for the chat selection control. No line numbers:
          a fenced snippet's third line is not the third line of any file, and
          a wrong range is worse than none. The pre rules apply to both the
          plain streaming pre and Shiki's, so completion swaps colour only and
          never the block's height. */}
      <div
        data-chat-selection=""
        className={cn(
          "min-w-0 overflow-x-auto [&_code]:font-mono [&_pre]:m-0 [&_pre]:min-w-max [&_pre]:bg-transparent! [&_pre]:font-mono [&_pre]:leading-relaxed [&_pre]:text-[var(--color-text-primary)]",
          compact ? "text-xs [&_pre]:px-3 [&_pre]:pb-3 [&_pre]:pt-1" : "text-sm [&_pre]:px-4 [&_pre]:pb-4 [&_pre]:pt-1",
        )}
      >
        {children}
      </div>
    </div>
  );
}

interface HighlightedCodeBlockProps {
  code: string;
  language: string;
  compact?: boolean;
  streaming?: boolean;
  label?: string;
  className?: string;
}

export function HighlightedCodeBlock({
  code,
  language,
  compact = false,
  streaming = false,
  label,
  className,
}: HighlightedCodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    if (streaming || !language) return;
    let active = true;
    void highlight(code, language)
      .then((value) => {
        if (active) setHtml(value);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [code, language, streaming]);

  return (
    <CodeFrame
      code={code}
      language={language}
      compact={compact}
      {...(label ? { label } : {})}
      {...(className ? { className } : {})}
    >
      {!streaming && html ? (
        <div dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <pre>
          <code>{code}</code>
        </pre>
      )}
    </CodeFrame>
  );
}
