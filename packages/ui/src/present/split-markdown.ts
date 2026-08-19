// Small, dependency-free markdown helpers for turning a wiki page into slide
// material. All fence-aware so a `## ` or ``` inside a fenced code block is
// never mistaken for a heading or a block boundary (same care as
// reader-persona's filter).

export interface MarkdownSection {
  heading: string;
  body: string;
}

export interface SplitMarkdown {
  /** Content before the first H2 (typically the lead paragraph). */
  lead: string;
  sections: MarkdownSection[];
}

const FENCE = /^\s*```/;
const H1 = /^#\s+(.+?)\s*$/;
const H2 = /^##\s+(.+?)\s*$/;

/** Split markdown on `## ` headings, ignoring headings inside code fences. */
export function splitOnH2(markdown: string): SplitMarkdown {
  const lines = markdown.split("\n");
  const leadLines: string[] = [];
  const sections: MarkdownSection[] = [];
  let current: MarkdownSection | null = null;
  let inFence = false;

  const push = (line: string) => {
    if (current) current.body += (current.body ? "\n" : "") + line;
    else leadLines.push(line);
  };

  for (const line of lines) {
    if (FENCE.test(line)) {
      inFence = !inFence;
      push(line);
      continue;
    }
    const h2 = !inFence ? H2.exec(line) : null;
    if (h2) {
      current = { heading: h2[1] ?? "", body: "" };
      sections.push(current);
      continue;
    }
    push(line);
  }

  return {
    lead: leadLines.join("\n").trim(),
    sections: sections.map((s) => ({ heading: s.heading, body: s.body.trim() })),
  };
}

/** Drop a single leading `# Title` line (the slide shows the title itself). */
export function stripLeadingH1(markdown: string): string {
  const lines = markdown.split("\n");
  let i = 0;
  while (i < lines.length && (lines[i] ?? "").trim() === "") i++;
  if (i < lines.length && H1.test(lines[i] ?? "")) {
    return lines.slice(i + 1).join("\n").trimStart();
  }
  return markdown;
}

/** Extract the source of every ```mermaid fenced block, in order. */
export function extractMermaidBlocks(markdown: string): string[] {
  const blocks: string[] = [];
  const lines = markdown.split("\n");
  let inBlock = false;
  let buf: string[] = [];
  for (const line of lines) {
    if (!inBlock && /^\s*```mermaid\b/.test(line)) {
      inBlock = true;
      buf = [];
      continue;
    }
    if (inBlock && /^\s*```\s*$/.test(line)) {
      inBlock = false;
      const chart = buf.join("\n").trim();
      if (chart) blocks.push(chart);
      continue;
    }
    if (inBlock) buf.push(line);
  }
  return blocks;
}

/** Remove fenced code and mermaid blocks (used only for word counting). */
function stripFences(markdown: string): string {
  return markdown.replace(/```[\s\S]*?```/g, " ");
}

export function countWords(markdown: string): number {
  const text = stripFences(markdown).replace(/[#>*`_\-|]/g, " ");
  const words = text.trim().split(/\s+/).filter(Boolean);
  return words.length;
}

/**
 * Trim prose to a slide-sized excerpt on a paragraph boundary. Keeps whole
 * paragraphs up to `maxChars`, and always returns at least the first paragraph
 * so a slide is never blank when the first block is long.
 */
export function clampProse(markdown: string, maxChars: number): string {
  const trimmed = markdown.trim();
  if (trimmed.length <= maxChars) return trimmed;
  const paras = trimmed.split(/\n{2,}/);
  const out: string[] = [];
  let len = 0;
  for (const p of paras) {
    if (out.length > 0 && len + p.length > maxChars) break;
    out.push(p);
    len += p.length + 2;
    if (len >= maxChars) break;
  }
  return out.join("\n\n").trim();
}
