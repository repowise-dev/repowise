"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Route, Loader2, X, ArrowRight, Search, ChevronDown } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { getGraphPath, searchNodes } from "@/lib/api/graph";
import { useDebounce } from "@/lib/hooks/use-debounce";
import type { GraphPathResponse, NodeSearchResult } from "@/lib/api/types";

interface PathFinderPanelProps {
  repoId: string;
  onPathFound: (pathNodes: string[]) => void;
  onClear: () => void;
  onClose: () => void;
  initialFrom?: string;
  initialTo?: string;
}

function NodeAutocomplete({
  repoId,
  value,
  onChange,
  placeholder,
  onSubmit,
}: {
  repoId: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  onSubmit?: () => void;
}) {
  const [results, setResults] = useState<NodeSearchResult[]>([]);
  const [topNodes, setTopNodes] = useState<NodeSearchResult[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const debounced = useDebounce(value, 250);
  const topLoaded = useRef(false);

  // Load top nodes by pagerank once for the dropdown
  useEffect(() => {
    if (topLoaded.current) return;
    topLoaded.current = true;
    searchNodes(repoId, "", 20)
      .then(setTopNodes)
      .catch(() => {});
  }, [repoId]);

  // Search as user types
  useEffect(() => {
    if (!debounced.trim() || debounced.length < 2) {
      setResults([]);
      return;
    }
    searchNodes(repoId, debounced, 12)
      .then((r) => {
        setResults(r);
        setShowDropdown(r.length > 0);
      })
      .catch(() => setResults([]));
  }, [debounced, repoId]);

  const displayResults = results.length > 0 ? results : topNodes;

  const handleSelect = useCallback(
    (nodeId: string) => {
      onChange(nodeId);
      setShowDropdown(false);
    },
    [onChange],
  );

  return (
    <div className="relative">
      <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--color-text-tertiary)]" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setShowDropdown(true)}
        onBlur={() => setTimeout(() => setShowDropdown(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            setShowDropdown(false);
            onSubmit?.();
          }
        }}
        placeholder={placeholder}
        className="w-full h-8 pl-8 pr-8 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] text-xs font-mono text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:outline-none focus:border-[var(--color-accent-graph)] focus:ring-1 focus:ring-[var(--color-accent-graph)]/30 transition-all"
      />
      <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-[var(--color-text-tertiary)]" />
      {showDropdown && displayResults.length > 0 && (
        <div className="absolute top-full left-0 mt-1 w-full max-h-52 overflow-auto z-50 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-xl shadow-black/30 py-1">
          {results.length === 0 && topNodes.length > 0 && (
            <div className="px-3 py-1 text-[9px] text-[var(--color-text-tertiary)] uppercase tracking-wider">
              Top files by importance
            </div>
          )}
          {displayResults.map((r) => (
            <button
              key={r.node_id}
              className="w-full px-3 py-1.5 text-left text-xs hover:bg-[var(--color-bg-elevated)] transition-colors flex items-center gap-2"
              onMouseDown={() => handleSelect(r.node_id)}
            >
              <span className="font-mono text-[var(--color-text-primary)] truncate flex-1">
                {r.node_id}
              </span>
              <span className="text-[10px] text-[var(--color-text-tertiary)] capitalize shrink-0 tabular-nums">
                {r.language}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function PathFinderPanel({
  repoId,
  onPathFound,
  onClear,
  onClose,
  initialFrom = "",
  initialTo = "",
}: PathFinderPanelProps) {
  const [from, setFrom] = useState(initialFrom);
  const [to, setTo] = useState(initialTo);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GraphPathResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Sync initial values when they change from context menu
  useEffect(() => {
    if (initialFrom) setFrom(initialFrom);
  }, [initialFrom]);
  useEffect(() => {
    if (initialTo) setTo(initialTo);
  }, [initialTo]);

  const handleFind = useCallback(async () => {
    if (!from.trim() || !to.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    onClear();
    try {
      const res = await getGraphPath(repoId, from.trim(), to.trim());
      setResult(res);
      if (res.path && res.path.length > 0) {
        onPathFound(res.path);
      } else {
        setError("No path found between these nodes.");
      }
    } catch {
      setError("No path found between these nodes.");
    } finally {
      setLoading(false);
    }
  }, [from, to, repoId, onPathFound, onClear]);

  const handleClear = useCallback(() => {
    setFrom("");
    setTo("");
    setResult(null);
    setError(null);
    onClear();
  }, [onClear]);

  return (
    <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)]/90 backdrop-blur-md shadow-xl shadow-black/30 p-3 w-[min(280px,calc(100vw-1.5rem))] space-y-2">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Route className="w-3.5 h-3.5 text-[var(--color-accent-graph)]" />
        <span className="text-xs font-semibold text-[var(--color-text-primary)]">
          Find Path
        </span>
        <div className="ml-auto flex items-center gap-1">
          {(result || error) && (
            <Button
              size="sm"
              variant="ghost"
              onClick={handleClear}
              className="h-5 w-5 p-0"
              aria-label="Clear results"
              title="Clear results"
            >
              <X className="h-3 w-3" />
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            className="h-5 w-5 p-0 text-[var(--color-text-tertiary)]"
            aria-label="Close"
            title="Close"
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {/* Inputs stacked */}
      <div className="space-y-1.5">
        <NodeAutocomplete
          repoId={repoId}
          value={from}
          onChange={setFrom}
          placeholder="From file..."
          onSubmit={handleFind}
        />
        <NodeAutocomplete
          repoId={repoId}
          value={to}
          onChange={setTo}
          placeholder="To file..."
          onSubmit={handleFind}
        />
      </div>

      {/* Find button */}
      <Button
        size="sm"
        onClick={handleFind}
        disabled={!from.trim() || !to.trim() || loading}
        className="w-full h-7 text-xs font-medium gap-1.5"
        style={{
          background: "var(--color-accent-graph)",
          color: "#000",
        }}
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Route className="h-3.5 w-3.5" />
        )}
        Find Path
      </Button>

      {/* Result */}
      {result && result.path && result.path.length > 0 && (
        <div className="rounded-lg bg-[var(--color-bg-inset)] border border-[var(--color-border-default)] p-2 space-y-1">
          <p className="text-[10px] font-medium text-[var(--color-accent-graph)] uppercase tracking-wider">
            {result.distance} {result.distance === 1 ? "hop" : "hops"}
          </p>
          <div className="space-y-0.5 max-h-28 overflow-auto">
            {result.path.map((nodeName, i) => {
              const shortName = nodeName.split("/").pop() ?? nodeName;
              return (
                <div key={i} className="flex items-center gap-1">
                  {i > 0 && (
                    <ArrowRight className="w-2.5 h-2.5 text-[var(--color-accent-graph)] shrink-0" />
                  )}
                  <span
                    className="text-[10px] font-mono text-[var(--color-text-secondary)] truncate"
                    title={nodeName}
                  >
                    {shortName}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {error && (
        <p className="text-[10px] text-[var(--color-risk-high)]">{error}</p>
      )}
    </div>
  );
}
