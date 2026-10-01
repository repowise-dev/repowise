import * as vscode from "vscode";
import { CONFIG_SECTION } from "../constants";
import type { RepowiseContext } from "../core/context";
import { getDocDriftFindings } from "../core/fileSignals";
import { DOC_DRIFT_CONFIDENCE, type DocDriftFinding } from "@repowise-dev/types/doc-drift";
import { registerEditorDiagnostics } from "./editorDiagnostics";

const CONFIG_PREFIX = `${CONFIG_SECTION}.docDrift`;
const MARKDOWN_EXT = /\.(md|mdx|markdown)$/i;
import { locateReference, type LocatedReference } from "./locateReference";

export { locateReference, type LocatedReference };

function isMarkdown(doc: vscode.TextDocument): boolean {
  return doc.languageId === "markdown" || MARKDOWN_EXT.test(doc.uri.path);
}

/** The finding's line, narrowed to the reference as written (else the target), relocated in live buffer. */
export function rangeFor(doc: vscode.TextDocument, f: DocDriftFinding): vscode.Range | null {
  if (doc.lineCount === 0) return null;
  const lines: string[] = [];
  for (let i = 0; i < doc.lineCount; i++) {
    lines.push(doc.lineAt(i).text);
  }
  const recordedLine = Math.max(0, f.line_number - 1);
  const loc = locateReference(lines, f.raw, f.target, recordedLine);
  if (!loc) return null;
  return new vscode.Range(loc.line, loc.start, loc.line, loc.end);
}

export function toDiagnostic(doc: vscode.TextDocument, f: DocDriftFinding): vscode.Diagnostic | null {
  const range = rangeFor(doc, f);
  if (!range) return null;
  const message = f.suggestion ? `${f.reason} Likely now: ${f.suggestion}.` : f.reason;
  const diagnostic = new vscode.Diagnostic(
    range,
    message,
    f.confidence >= DOC_DRIFT_CONFIDENCE.HIGH
      ? vscode.DiagnosticSeverity.Warning
      : vscode.DiagnosticSeverity.Information,
  );
  diagnostic.source = "repowise";
  diagnostic.code = `doc-drift:${f.kind}`;
  return diagnostic;
}

/**
 * Publishes documentation-drift findings for visible markdown editors into the
 * Problems panel, in a collection of its own so it toggles independently of the
 * health diagnostics. Detection only: no code action edits the document.
 */
export function registerDocDriftDiagnostics(ctx: RepowiseContext): vscode.Disposable {
  const cfg = () => vscode.workspace.getConfiguration(CONFIG_PREFIX);
  return registerEditorDiagnostics<DocDriftFinding>(ctx, {
    name: "repowise-doc-drift",
    configPrefix: CONFIG_PREFIX,
    enabled: () => cfg().get<boolean>("diagnostics.enabled", true),
    accept: isMarkdown,
    recomputeOnEdit: true,
    load: (c, rel) =>
      getDocDriftFindings(
        c,
        rel,
        cfg().get<number>("diagnostics.minConfidence", DOC_DRIFT_CONFIDENCE.HIGH),
      ),
    // Keyed on what the diagnostic renders from.
    signature: (f) => `${f.id}:${f.line_number}:${f.confidence}:${f.suggestion ?? ""}`,
    toDiagnostic,
  });
}

