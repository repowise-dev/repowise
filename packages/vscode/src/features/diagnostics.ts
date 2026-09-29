import * as vscode from "vscode";
import { CONFIG_SECTION } from "../constants";
import type { RepowiseContext } from "../core/context";
import { getFileFindings } from "../core/fileSignals";
import type { HealthDimension, HealthFinding, HealthSeverity } from "@repowise-dev/types/health";
import { registerEditorDiagnostics } from "./editorDiagnostics";

/** Severity ordering, high number = more severe, for the minSeverity floor. */
const SEVERITY_RANK: Record<HealthSeverity, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

const DEFAULT_DIMENSIONS: HealthDimension[] = [
  "defect",
  "maintainability",
  "performance",
];

/** Config keys this feature reacts to, for the configuration-change filter. */
const CONFIG_PREFIX = `${CONFIG_SECTION}.diagnostics`;

/** Maps a finding severity to a diagnostic severity, capped at Warning. */
function toDiagnosticSeverity(severity: HealthSeverity): vscode.DiagnosticSeverity {
  switch (severity) {
    case "critical":
    case "high":
      return vscode.DiagnosticSeverity.Warning;
    case "medium":
      return vscode.DiagnosticSeverity.Information;
    case "low":
      return vscode.DiagnosticSeverity.Hint;
  }
}

/** Line span (1-based, inclusive) to a 0-based range; null lines anchor at top. */
function rangeFor(finding: HealthFinding): vscode.Range {
  if (finding.line_start == null) return new vscode.Range(0, 0, 0, 0);
  const start = Math.max(0, finding.line_start - 1);
  const end = Math.max(start, (finding.line_end ?? finding.line_start) - 1);
  return new vscode.Range(start, 0, end, Number.MAX_SAFE_INTEGER);
}

function toDiagnostic(_doc: vscode.TextDocument, f: HealthFinding): vscode.Diagnostic {
  const diagnostic = new vscode.Diagnostic(
    rangeFor(f),
    f.reason,
    toDiagnosticSeverity(f.severity),
  );
  diagnostic.source = "repowise";
  diagnostic.code = f.biomarker_type;
  return diagnostic;
}

/**
 * Publishes health findings for visible editors into the Problems panel. Only
 * findings at or above the configured severity floor and within the configured
 * dimensions surface; the deeper set stays in the gutter.
 */
export function registerDiagnostics(ctx: RepowiseContext): vscode.Disposable {
  const cfg = () => vscode.workspace.getConfiguration(CONFIG_SECTION);
  const minSeverity = (): HealthSeverity =>
    cfg().get<HealthSeverity>("diagnostics.minSeverity", "high");
  const dimensions = (): Set<string> =>
    new Set(cfg().get<string[]>("diagnostics.dimensions", DEFAULT_DIMENSIONS));

  return registerEditorDiagnostics<HealthFinding>(ctx, {
    name: "repowise",
    configPrefix: CONFIG_PREFIX,
    enabled: () => cfg().get<boolean>("diagnostics.enabled", true),
    load: async (c, rel) => {
      const findings = await getFileFindings(c, rel);
      // Filters are read after the fetch, so a config change mid-fetch applies.
      const floor = SEVERITY_RANK[minSeverity()];
      const dims = dimensions();
      return findings.filter(
        (f) => SEVERITY_RANK[f.severity] >= floor && dims.has(f.dimension ?? "defect"),
      );
    },
    signature: (f) => `${f.id}:${f.line_start}:${f.line_end}:${f.severity}`,
    toDiagnostic,
  });
}
