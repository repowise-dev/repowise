/**
 * An Ask reply as Markdown: the tool's own words, its confidence or basis,
 * and the evidence ids and paths it cites, all as the server sent them.
 */

import { fit } from "../format";
import type { AnswerReply, AskReply, EvidenceRef, WhyCommit, WhyDecision, WhyRationale, WhyReply } from "../model/ask";
import { ASK_CHARS, PANE_COPY, askCut, moreRows } from "./copy";

/** Rows of each list drawn; the rest are counted. */
const LIST_ROWS = 5;
/** Characters of one decision body or rationale comment. */
const ROW_CHARS = 300;

const code = (s: string): string => `\`${s.replace(/`/g, "'")}\``;
/** One line of the tool's prose: Markdown line breaks would split a list row. */
const line = (s: string, chars = ROW_CHARS): string => fit(s.replace(/\s+/g, " ").trim(), chars);

function ids(refs: readonly EvidenceRef[] | undefined): string {
  const found = (refs ?? []).map((r) => r.id).filter((id): id is string => typeof id === "string");
  return found.length === 0 ? "" : ` · ${found.map(code).join(" ")}`;
}

/** Up to LIST_ROWS rows under a bold head, then a count of the rest (`total` when the server stamped one). */
function list<T>(head: string, rows: readonly T[] | undefined, row: (r: T) => string, total?: number): string[] {
  if (rows === undefined || rows.length === 0) return [];
  const shown = rows.slice(0, LIST_ROWS);
  const more = Math.max(total ?? 0, rows.length) - shown.length;
  return [`**${head}**`, ...shown.map((r) => `- ${row(r)}`), ...(more > 0 ? [`- ${moreRows(more)}`] : [])];
}

/** `**get_answer** · confidence: low · retrieval: weak · not synthesized: no-llm-provider`. */
function answerHead(a: AnswerReply): string {
  const parts = ["**get_answer**"];
  if (a.confidence !== undefined) parts.push(`confidence: ${a.confidence}`);
  if (a.retrieval_quality !== undefined) parts.push(`retrieval: ${a.retrieval_quality}`);
  if (a.degraded !== undefined) parts.push(`not synthesized: ${a.degraded}`);
  return parts.join(" · ");
}

function answerMarkdown(a: AnswerReply): string[] {
  const cited = a.citations ?? [];
  const evidence = cited.length === 0 ? PANE_COPY.noEvidence : `cited: ${cited.map(code).join(", ")}`;
  return [answerHead(a), a.answer ?? "", evidence];
}

const decisionRow = (d: WhyDecision): string => {
  const facts = [d.status, d.authority, d.confidence === null || d.confidence === undefined ? undefined : `confidence ${d.confidence}`]
    .filter((f): f is string => f !== undefined)
    .join(" · ");
  const id = d.id === undefined ? "" : ` · ${code(d.id)}`;
  const body = d.decision === undefined ? "" : `: ${line(d.decision)}`;
  return `**${line(d.title, 120)}**${facts === "" ? "" : ` (${facts})`}${id}${body}`;
};

const commitRow = (c: WhyCommit): string =>
  `${code(c.sha ?? "?")} ${line(c.message ?? "", 160)}${c.date === undefined ? "" : ` (${c.date.slice(0, 10)})`}${ids(c.evidence_refs)}`;

const rationaleRow = (r: WhyRationale): string =>
  `${code(r.lines === undefined ? r.path : `${r.path}:${r.lines[0]}`)} ${line(r.comment)}${ids(r.evidence_refs)}`;

function whyMarkdown(w: WhyReply): string[] {
  const head = w.answer_basis === undefined ? "**get_why**" : `**get_why** · basis: ${w.answer_basis}`;
  const commits = w.git_archaeology?.git_log ?? w.git_archaeology?.file_commits;
  return [
    head,
    ...(w.reason === undefined ? [] : [w.reason]),
    ...list("Decisions", w.decisions, decisionRow),
    ...list("Commits", commits, commitRow),
    ...list("Rationale in the code", w.code_rationale, rationaleRow, w.code_rationale_total),
  ];
}

/** The reply under the question, cut at ASK_CHARS with the cut said. */
export function replyMarkdown(question: string, a: AskReply): string {
  const body = a.tool === "get_answer" ? answerMarkdown(a.reply) : whyMarkdown(a.reply);
  const text = [`> ${line(question, 200)}`, ...body].join("\n\n");
  if (text.length <= ASK_CHARS) return text;
  const note = `\n\n_${askCut(ASK_CHARS)}_`;
  return `${text.slice(0, ASK_CHARS - note.length).trimEnd()}${note}`;
}
