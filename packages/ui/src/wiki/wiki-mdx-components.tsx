import type { ReactNode } from "react";

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");
}

function extractText(node: unknown): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: unknown } }).props;
    return extractText(props?.children);
  }
  return "";
}

export const wikiMdxComponents = {
  h1: (props: React.HTMLAttributes<HTMLHeadingElement>) => {
    const id = slugify(extractText(props.children));
    return (
      <h2
        id={id}
        className="mt-8 mb-4 text-xl font-semibold text-[var(--color-text-primary)] first:mt-0 scroll-mt-16"
        {...props}
      />
    );
  },
  h2: (props: React.HTMLAttributes<HTMLHeadingElement>) => {
    const id = slugify(extractText(props.children));
    return (
      <h2
        id={id}
        className="mt-6 mb-3 text-lg font-semibold text-[var(--color-text-primary)] scroll-mt-16"
        {...props}
      />
    );
  },
  h3: (props: React.HTMLAttributes<HTMLHeadingElement>) => {
    const id = slugify(extractText(props.children));
    return (
      <h3
        id={id}
        className="mt-5 mb-2 text-base font-semibold text-[var(--color-text-primary)] scroll-mt-16"
        {...props}
      />
    );
  },
  p: (props: React.HTMLAttributes<HTMLParagraphElement>) => (
    <p className="mb-4 text-sm leading-7 text-[var(--color-text-secondary)]" {...props} />
  ),
  ul: (props: React.HTMLAttributes<HTMLUListElement>) => (
    <ul className="mb-4 ml-4 space-y-1 list-disc text-sm text-[var(--color-text-secondary)]" {...props} />
  ),
  ol: (props: React.HTMLAttributes<HTMLOListElement>) => (
    <ol className="mb-4 ml-4 space-y-1 list-decimal text-sm text-[var(--color-text-secondary)]" {...props} />
  ),
  li: (props: React.HTMLAttributes<HTMLLIElement>) => (
    <li className="leading-6" {...props} />
  ),
  blockquote: (props: React.HTMLAttributes<HTMLQuoteElement>) => (
    <blockquote
      className="my-4 border-l-2 border-[var(--color-accent-primary)] pl-4 text-sm italic text-[var(--color-text-tertiary)]"
      {...props}
    />
  ),
  table: (props: React.HTMLAttributes<HTMLTableElement>) => (
    <div className="my-4 overflow-x-auto rounded border border-[var(--color-border-default)]">
      <table className="w-full text-sm" {...props} />
    </div>
  ),
  thead: (props: React.HTMLAttributes<HTMLTableSectionElement>) => (
    <thead className="bg-[var(--color-bg-elevated)]" {...props} />
  ),
  th: (props: React.HTMLAttributes<HTMLTableCellElement>) => (
    <th
      className="px-4 py-2 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider"
      {...props}
    />
  ),
  td: (props: React.HTMLAttributes<HTMLTableCellElement>) => (
    <td
      className="px-4 py-2 text-sm text-[var(--color-text-secondary)] border-t border-[var(--color-border-default)]"
      {...props}
    />
  ),
  hr: () => <hr className="my-6 border-[var(--color-border-default)]" />,
  a: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      className="text-[var(--color-accent-primary)] underline underline-offset-2 hover:opacity-80 transition-opacity"
      {...props}
    />
  ),
  strong: (props: React.HTMLAttributes<HTMLElement>) => (
    <strong className="font-semibold text-[var(--color-text-primary)]" {...props} />
  ),
  code: (props: React.HTMLAttributes<HTMLElement>) => (
    <code
      className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs font-mono text-[var(--color-accent-primary)]"
      {...props}
    />
  ),
};

export interface ShikiBlockProps {
  html: string;
  code: string;
  lang: string;
}

export interface MermaidBlockProps {
  chart: string;
}

export type WikiMdxOverrides = {
  ShikiBlock: (p: ShikiBlockProps) => ReactNode;
  MermaidBlock: (p: MermaidBlockProps) => ReactNode;
};
