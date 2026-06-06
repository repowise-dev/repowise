"use client";

import { useState, useEffect, useCallback } from "react";
import { X, Maximize2, Minimize2, AlertCircle, RefreshCw } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { Badge } from "./panel-atoms";

const EXTENSION_MAP: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  rs: "rust",
  go: "go",
  rb: "ruby",
  java: "java",
  cs: "csharp",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  c: "c",
  h: "c",
  css: "css",
  html: "html",
  htm: "html",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  md: "markdown",
  sh: "shell",
  bash: "shell",
  sql: "sql",
  xml: "xml",
  toml: "toml",
  swift: "swift",
  kt: "kotlin",
  kts: "kotlin",
  r: "r",
  R: "r",
  php: "php",
};

export function getLanguageFromPath(filePath: string): string {
  const parts = filePath.split(".");
  if (parts.length < 2) return "text";
  const ext = parts[parts.length - 1];
  if (ext === undefined) return "text";
  return EXTENSION_MAP[ext] ?? "text";
}

export interface CodeViewerProps {
  fetchContent: (filePath: string) => Promise<string>;
}

export function CodeViewer({ fetchContent }: CodeViewerProps): React.ReactElement | null {
  const codeViewerOpen = useArchitectureStore((s) => s.codeViewerOpen);
  const codeViewerNodeId = useArchitectureStore((s) => s.codeViewerNodeId);
  const codeViewerExpanded = useArchitectureStore((s) => s.codeViewerExpanded);
  const nodesById = useArchitectureStore((s) => s.nodesById);
  const closeCodeViewer = useArchitectureStore((s) => s.closeCodeViewer);
  const toggleCodeViewerExpanded = useArchitectureStore((s) => s.toggleCodeViewerExpanded);

  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const node = codeViewerNodeId != null ? nodesById.get(codeViewerNodeId) ?? null : null;
  const filePath = node?.file_path ?? null;

  const doFetch = useCallback(() => {
    if (filePath == null) return;
    setLoading(true);
    setError(null);
    setContent(null);
    fetchContent(filePath).then(
      (result) => {
        setContent(result);
        setLoading(false);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      },
    );
  }, [filePath, fetchContent]);

  useEffect(() => {
    if (!codeViewerOpen || filePath == null) return;
    doFetch();
  }, [codeViewerOpen, filePath, doFetch]);

  if (!codeViewerOpen || codeViewerNodeId == null) return null;
  if (node == null || filePath == null) return null;

  const lines = content != null ? content.split("\n") : [];
  const language = getLanguageFromPath(filePath);
  const fileName = filePath.split("/").pop() ?? filePath;

  const buttonStyle: React.CSSProperties = {
    background: "none",
    border: "none",
    cursor: "pointer",
    color: "var(--color-text-secondary)",
    padding: 4,
    display: "inline-flex",
    alignItems: "center",
    borderRadius: 4,
  };

  const header = (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 12px",
        borderBottom: "1px solid var(--color-border-default)",
        flexShrink: 0,
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        {!codeViewerExpanded && (
          <div
            style={{
              width: 40,
              height: 4,
              borderRadius: 2,
              background: "var(--color-text-secondary)",
              opacity: 0.4,
              marginRight: 8,
            }}
          />
        )}
        <span
          style={{
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            fontSize: 13,
            fontWeight: 600,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {fileName}
        </span>
        <Badge label={language} />
        {content != null && (
          <span style={{ fontSize: 10, opacity: 0.5 }}>{lines.length} lines</span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <button
          type="button"
          onClick={toggleCodeViewerExpanded}
          aria-label={codeViewerExpanded ? "Collapse code viewer" : "Expand code viewer"}
          style={buttonStyle}
        >
          {codeViewerExpanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
        <button
          type="button"
          onClick={closeCodeViewer}
          aria-label="Close code viewer"
          style={buttonStyle}
        >
          <X size={16} />
        </button>
      </div>
    </header>
  );

  const codeArea = loading ? (
    <div role="status" aria-label="Loading code" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
      {Array.from({ length: 12 }).map((_, i) => (
        <div
          key={i}
          style={{
            height: 14,
            borderRadius: 4,
            background: "var(--color-bg-wash-hover)",
            width: `${60 + (i % 4) * 10}%`,
            animation: "pulse 1.5s ease-in-out infinite",
          }}
        />
      ))}
    </div>
  ) : error != null ? (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: 32,
        gap: 12,
        flex: 1,
      }}
    >
      <AlertCircle size={32} style={{ color: "var(--color-error)" }} />
      <div style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>{error}</div>
      <button
        type="button"
        onClick={doFetch}
        style={{
          padding: "6px 14px",
          background: "var(--color-accent-muted, rgba(245,149,32,0.2))",
          color: "var(--color-accent-primary)",
          border: "1px solid var(--color-accent-primary)",
          borderRadius: 4,
          cursor: "pointer",
          fontSize: 12,
          fontWeight: 500,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <RefreshCw size={12} />
        Retry
      </button>
    </div>
  ) : (
    <div style={{ flex: 1, overflow: "auto", padding: 0, margin: 0 }}>
      <pre
        style={{
          margin: 0,
          padding: "8px 0",
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: 13,
          lineHeight: 1.5,
          tabSize: 4,
        }}
      >
        <code>
          {lines.map((line, index) => {
            const lineNum = index + 1;
            const inRange =
              node.line_range != null &&
              lineNum >= node.line_range[0] &&
              lineNum <= node.line_range[1];
            return (
              <div
                key={lineNum}
                style={{
                  display: "flex",
                  background: inRange ? "rgba(245,149,32,0.1)" : "transparent",
                  borderLeft: inRange
                    ? "3px solid var(--color-accent-primary)"
                    : "3px solid transparent",
                }}
              >
                <span
                  style={{
                    display: "inline-block",
                    width: 50,
                    textAlign: "right",
                    paddingRight: 12,
                    color: "var(--color-text-secondary)",
                    opacity: 0.5,
                    userSelect: "none",
                    flexShrink: 0,
                  }}
                  aria-hidden="true"
                >
                  {lineNum}
                </span>
                <span style={{ flex: 1, paddingRight: 16, whiteSpace: "pre" }}>{line}</span>
              </div>
            );
          })}
        </code>
      </pre>
    </div>
  );

  const panelContent = (
    <>
      <style>{"@keyframes pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }"}</style>
      {header}
      {codeArea}
    </>
  );

  if (codeViewerExpanded) {
    return (
      <div
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 50,
          background: "rgba(0,0,0,0.6)",
          backdropFilter: "blur(8px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            width: "90vw",
            height: "90vh",
            display: "flex",
            flexDirection: "column",
            background: "var(--color-bg-elevated, rgba(17,24,39,0.98))",
            border: "1px solid var(--color-border-default)",
            borderRadius: 8,
            boxShadow: "0 25px 50px rgba(0,0,0,0.5)",
            color: "var(--color-text-primary)",
            fontFamily: "var(--font-sans, system-ui, sans-serif)",
            overflow: "hidden",
          }}
        >
          {panelContent}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        height: "40vh",
        zIndex: 40,
        display: "flex",
        flexDirection: "column",
        background: "var(--color-bg-elevated, rgba(17,24,39,0.98))",
        borderTop: "1px solid var(--color-border-default)",
        boxShadow: "0 -10px 30px rgba(0,0,0,0.3)",
        color: "var(--color-text-primary)",
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
      }}
    >
      {panelContent}
    </div>
  );
}
