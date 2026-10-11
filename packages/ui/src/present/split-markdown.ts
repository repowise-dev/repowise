// Small, dependency-free markdown helpers for turning a wiki page into slide
// material. All fence-aware so a `## ` or a blank line inside a fenced block is
// never mistaken for a heading or a paragraph boundary (same care as
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

/** Split markdown into blank-line separated blocks, keeping fences whole. */
export function splitBlocks(markdown: string): string[] {
  const blocks: string[] = [];
  let buf: string[] = [];
  let inFence = false;
  const flush = () => {
    const text = buf.join("\n").trim();
    if (text) blocks.push(text);
    buf = [];
  };
  for (const line of markdown.split("\n")) {
    if (FENCE.test(line)) inFence = !inFence;
    if (!inFence && line.trim() === "") flush();
    else buf.push(line);
  }
  flush();
  return blocks;
}

/**
 * Whether a block reads as explanatory prose: not a heading, list, table,
 * fence, quote, rule, an emphasis-only footnote or a `**Label:** value` stat
 * line, and it ends a sentence (or introduces a list with a colon).
 */
export function isProse(block: string): boolean {
  const text = block.trim();
  if (text.length < 40) return false;
  if (/^(#|\||>|```|---|\*\*\*|[-*+]\s|\d+[.)]\s)/.test(text)) return false;
  if (/^\*\*[^*\n]+:\*\*/.test(text)) return false;
  if (/^([*_])[^*_][\s\S]*\1$/.test(text)) return false;
  return /[.!?:][)"'`*_]*$/.test(text);
}

/**
 * Keep whole sentences up to `maxChars`. Always returns at least the first
 * sentence, however long, so a slide never ends mid-thought.
 */
export function wholeSentences(paragraph: string, maxChars: number): string {
  const text = paragraph.replace(/\s*\n\s*/g, " ").trim();
  // A sentence ends at . ! ? followed by a space and an uppercase or markup
  // start, so "e.g. the" and "v1.2" do not split.
  const sentences = text.split(/(?<=[.!?][)"'`*_]*)\s+(?=[A-Z`*_("[])/);
  let out = sentences[0] ?? "";
  for (const s of sentences.slice(1)) {
    if (out.length + 1 + s.length > maxChars) break;
    out += ` ${s}`;
  }
  return out;
}

/**
 * The diagram type a mermaid source declares: the first word after any
 * front matter block, `%%` comments and `%%{init}%%` directives.
 */
export function diagramKind(chart: string): string {
  const body = chart.trimStart().replace(/^---\r?\n[\s\S]*?\n---[ \t]*(\r?\n|$)/, "");
  const line = body
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l !== "" && !l.startsWith("%%"));
  return (line?.split(/\s/, 1)[0] ?? "").toLowerCase();
}

/**
 * Whether a diagram has anything to show. A flowchart with no edges is a row
 * of boxes, which says less than the list it came from; every other kind is
 * taken as drawn.
 */
export function isDrawable(chart: string): boolean {
  const kind = diagramKind(chart);
  if (kind !== "flowchart" && kind !== "graph") return true;
  return chart
    .split("\n")
    .some((line) => /(--|==|-\.|~~~)/.test(line.replace(/"[^"]*"/g, "")));
}
