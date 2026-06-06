"use client";

import { useRef, useEffect, useCallback } from "react";
import { Search } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { fuzzyMatch } from "../utils/fuzzy-match";
import { getTone } from "../../graph-primitives/tone-styles";
import { Badge } from "./panel-atoms";

export function SearchBar() {
  const inputRef = useRef<HTMLInputElement>(null);

  const view = useArchitectureStore((s) => s.view);
  const searchQuery = useArchitectureStore((s) => s.searchQuery);
  const searchResults = useArchitectureStore((s) => s.searchResults);
  const setSearchQuery = useArchitectureStore((s) => s.setSearchQuery);
  const setSearchResults = useArchitectureStore((s) => s.setSearchResults);
  const clearSearch = useArchitectureStore((s) => s.clearSearch);
  const selectNode = useArchitectureStore((s) => s.selectNode);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setSearchQuery(value);
      if (!value) {
        clearSearch();
        return;
      }
      if (view) {
        const results = fuzzyMatch(value, view.nodes, 5);
        setSearchResults(results);
      }
    },
    [view, setSearchQuery, setSearchResults, clearSearch],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        clearSearch();
        inputRef.current?.blur();
      }
    },
    [clearSearch],
  );

  const handleResultClick = useCallback(
    (nodeId: string) => {
      selectNode(nodeId);
      clearSearch();
    },
    [selectNode, clearSearch],
  );

  useEffect(() => {
    const handler = () => {
      inputRef.current?.focus();
    };
    window.addEventListener("arch:focus-search", handler);
    return () => window.removeEventListener("arch:focus-search", handler);
  }, []);

  const showDropdown = searchResults.length > 0 && searchQuery !== "";

  return (
    <div style={{ position: "relative", width: "100%" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          borderBottom: "1px solid var(--color-border-default)",
        }}
      >
        <Search size={14} style={{ opacity: 0.5, flexShrink: 0 }} />
        <input
          ref={inputRef}
          type="text"
          placeholder="Search nodes... (press /)"
          value={searchQuery}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          style={{
            width: "100%",
            background: "transparent",
            border: "none",
            outline: "none",
            color: "var(--color-text-primary)",
            fontSize: 12,
            fontFamily: "inherit",
          }}
        />
      </div>
      {showDropdown && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
            border: "1px solid var(--color-border-default)",
            borderRadius: 6,
            maxHeight: 240,
            overflowY: "auto",
            zIndex: 10,
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}
        >
          {searchResults.map((result) => {
            const tone = getTone(result.node_type);
            return (
              <button
                key={result.nodeId}
                type="button"
                onClick={() => handleResultClick(result.nodeId)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  width: "100%",
                  padding: "8px 12px",
                  background: "transparent",
                  border: "none",
                  borderBottom: "1px solid var(--color-bg-wash)",
                  cursor: "pointer",
                  textAlign: "left",
                  color: "var(--color-text-primary)",
                }}
              >
                <Badge label={result.node_type} color={tone.text} bg={tone.bg} />
                <span style={{ fontSize: 12, flex: 1 }}>{result.name}</span>
                <div
                  style={{
                    width: 40,
                    height: 4,
                    borderRadius: 2,
                    background: "var(--color-border-default)",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${result.score * 100}%`,
                      height: "100%",
                      background: "var(--color-accent-primary)",
                      borderRadius: 2,
                    }}
                  />
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
