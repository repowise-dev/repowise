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
  compact?: boolean;
}

export function CodeFrame({ code, language, children, compact = false }: CodeFrameProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="group my-4 min-w-0 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-inset)]">
      <div className="flex min-h-9 items-center justify-between border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
          {language || "text"}
        </span>
        <button
          type="button"
          onClick={() => void copy()}
          className="inline-flex min-h-8 items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          aria-label="Copy code"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className={cn("min-w-0 overflow-x-auto", compact ? "text-[11px]" : "text-sm")}>{children}</div>
    </div>
  );
}

interface HighlightedCodeBlockProps {
  code: string;
  language: string;
  compact?: boolean;
  streaming?: boolean;
}

export function HighlightedCodeBlock({
  code,
  language,
  compact = false,
  streaming = false,
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
    <CodeFrame code={code} language={language} compact={compact}>
      {!streaming && html ? (
        <div
          className="[&>pre]:m-0 [&>pre]:bg-transparent! [&>pre]:p-4"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="m-0 min-w-max p-4">
          <code className="font-mono text-[var(--color-text-primary)]">{code}</code>
        </pre>
      )}
    </CodeFrame>
  );
}
