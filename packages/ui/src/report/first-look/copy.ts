import type { FirstLookItemStatus } from "@repowise-dev/types/first-look";

/**
 * The page's own words: labels, buttons and menu entries. Every sentence about
 * the repository comes from `report.prose`, written in core; this is only the
 * chrome around it, so a host can translate it.
 */
export interface FirstLookCopy {
  eyebrow: string;
  back: string;
  handToAgent: string;
  share: string;
  copyLink: string;
  copyLinkHint: string;
  copyForAgent: string;
  copyForAgentHint: string;
  downloadMarkdown: string;
  downloadMarkdownHint: string;
  savePdf: string;
  savePdfHint: string;
  downloadJson: string;
  downloadJsonHint: string;
  previewExport: string;
  previewExportHint: string;
  exportNote: string;
  exitExport: string;
  copied: string;
  copyFailed: string;
  /** "{n} files". */
  files: (n: string) => string;
  /** "first look Nov 20, 2026". */
  firstLookOn: (date: string) => string;
  /** "written Nov 20, 2026". */
  writtenOn: (date: string) => string;
  resolved: string;
  acknowledged: string;
  newSince: string;
  /** "new since Nov 20, 2026". */
  newSinceDate: (date: string) => string;
  /** Short strip labels per section key. */
  stripTitle: Record<string, string>;
  /** What a strip number counts, per section key. */
  stripUnit: Record<string, string>;
  measured: string;
  startHere: string;
  fixWithAgent: string;
  fixWithAgentHint: string;
  copyItemPrompt: string;
  /** "{n} of {total}", a section header's count out of everything found. */
  countOf: (n: string, total: string) => string;
  /** "{n} more in the app". */
  moreInApp: (n: string) => string;
  linesCovered: string;
  branchesCovered: string;
  /** Compact table headings. */
  table: {
    function: string;
    ccn: string;
    lines: string;
    importers: string;
    changes: string;
    document: string;
    broken: string;
  };
  status: Record<FirstLookItemStatus | "new", string>;
  severity: { act_now: string; plan: string; improve: string };
  /** "{level} confidence". */
  confidence: (level: string) => string;
  /** "effort {e}". */
  effort: (e: string) => string;
  valuesRedacted: string;
  mark: string;
  markDone: string;
  markDoneHint: string;
  markAck: string;
  markAckHint: string;
  markFalsePositive: string;
  markFalsePositiveHint: string;
  markSnooze: string;
  reasonLabel: string;
  save: string;
  cancel: string;
  /** "Report at {sha}". */
  reportAt: (sha: string) => string;
  sinceThen: string;
  /** "Wording by your model, {date}". */
  wordingByModel: (date: string) => string;
  wordingTemplate: string;
  rewrite: string;
  rewriteWithModel: string;
  writing: string;
  /** "~{cost}", what a rewrite costs. */
  approxCost: (cost: string) => string;
  repoReport: string;
  openReport: string;
}

export const FIRST_LOOK_COPY_EN: FirstLookCopy = {
  eyebrow: "Repo report",
  back: "Overview",
  handToAgent: "Hand to agent",
  share: "Share",
  copyLink: "Copy link",
  copyLinkHint: "Opens this report",
  copyForAgent: "Copy for your agent",
  copyForAgentHint: "Every open item as one prompt",
  downloadMarkdown: "Download Markdown",
  downloadMarkdownHint: "repo-report.md, no buttons",
  savePdf: "Save as PDF",
  savePdfHint: "Browser print, clean layout",
  downloadJson: "Download JSON",
  downloadJsonHint: "For scripts and agents",
  previewExport: "Preview export",
  previewExportHint: "See it without controls",
  exportNote: "Export view: this is what Markdown, PDF and a shared link show.",
  exitExport: "Back to the app view",
  copied: "Copied. Paste it into your agent.",
  copyFailed: "Couldn't copy",
  files: (n) => `${n} files`,
  firstLookOn: (d) => `first look ${d}`,
  writtenOn: (d) => `written ${d}`,
  resolved: "resolved",
  acknowledged: "acknowledged",
  newSince: "new since the first look",
  newSinceDate: (d) => `new since ${d}`,
  stripTitle: {
    security: "Security",
    performance: "Performance",
    health: "Health",
    tests: "Tests",
    dead_code: "Dead code",
    docs: "Docs",
    decisions: "Decisions",
    hygiene: "Hygiene",
    ownership: "Ownership",
  },
  stripUnit: {
    security: "live secrets",
    performance: "worth fixing now",
    health: "refactor first",
    tests: "lines covered",
    dead_code: "safe to remove",
    docs: "broken refs",
    decisions: "to review",
    hygiene: "items",
    ownership: "single-owner areas",
  },
  measured: "measured",
  startHere: "Start here",
  fixWithAgent: "Fix with agent",
  fixWithAgentHint: "Copy one prompt for every open item in this section",
  copyItemPrompt: "Copy prompt for this item",
  countOf: (n, total) => `${n} of ${total}`,
  moreInApp: (n) => `${n} more in the app`,
  linesCovered: "lines covered",
  branchesCovered: "branches covered",
  table: {
    function: "File",
    ccn: "Max CCN",
    lines: "Code lines",
    importers: "Importers",
    changes: "Changes 90d",
    document: "Document",
    broken: "Broken",
  },
  status: {
    open: "open",
    done: "done",
    acknowledged: "acknowledged",
    false_positive: "not an issue",
    snoozed: "snoozed",
    resolved: "resolved",
    new: "new",
  },
  severity: { act_now: "do now", plan: "plan", improve: "improve" },
  confidence: (level) => `${level} confidence`,
  effort: (e) => `effort ${e}`,
  valuesRedacted: "values redacted",
  mark: "Mark",
  markDone: "Done",
  markDoneHint: "Fixed; the next update confirms it",
  markAck: "Acknowledge",
  markAckHint: "Real, handled later, with a reason",
  markFalsePositive: "Not an issue",
  markFalsePositiveHint: "False positive, with a reason",
  markSnooze: "Snooze 14 days",
  reasonLabel: "Reason",
  save: "Save",
  cancel: "Cancel",
  reportAt: (sha) => `Report at ${sha}`,
  sinceThen: "Since then:",
  wordingByModel: (date) => `Wording by your model, ${date}`,
  wordingTemplate: "Template wording",
  rewrite: "Rewrite",
  rewriteWithModel: "Rewrite with your model",
  writing: "Writing...",
  approxCost: (cost) => `~${cost}`,
  repoReport: "Repo report",
  openReport: "Open report",
};
