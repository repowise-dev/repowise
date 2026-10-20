// Generated from repowise.core.analysis.health.biomarker_labels. Do not edit.
//
// Regenerate with:  python scripts/generate_biomarker_labels.py
// tests/unit/health/test_generated_biomarker_labels.py fails when they disagree.

export const BIOMARKER_LABELS = {
  brain_method: "Brain method",
  nested_complexity: "Deep nesting",
  repeated_nesting: "Repeated nesting",
  complex_method: "Complex method",
  large_method: "Long function",
  primitive_obsession: "Long parameter list",
  dry_violation: "DRY violation",
  untested_hotspot: "Untested hotspot",
  coverage_gap: "Coverage gap",
  coverage_gradient: "Coverage gradient",
  developer_congestion: "Many authors",
  knowledge_loss: "Fading ownership",
  hidden_coupling: "Hidden coupling",
  complex_conditional: "Compound condition",
  function_hotspot: "Function hotspot",
  code_age_volatility: "Code age volatility",
  low_cohesion: "Low cohesion",
  god_class: "God class",
  ownership_risk: "Ownership risk",
  churn_risk: "Churn risk",
  change_entropy: "Change entropy",
  co_change_scatter: "Co-change scatter",
  prior_defect: "Prior defects",
  large_assertion_block: "Large assertion block",
  assertion_free_test: "Assertion free test",
  mock_saturated_test: "Mock saturated test",
  duplicated_assertion_block: "Duplicated assertions",
  error_handling: "Error handling",
  ungoverned_hotspot: "Ungoverned hotspot",
  stale_governance: "Stale governance",
  contradictory_decision: "Contradictory decision",
  io_in_loop: "I/O in loop",
  string_concat_in_loop: "String concat in loop",
  blocking_sync_in_async: "Blocking call in async",
  regex_compile_in_loop: "Regex compiled in loop",
  defer_in_loop: "Defer in loop",
  resource_construction_in_loop: "Resource built in loop",
  lock_in_loop: "Lock in loop",
  serial_await_in_loop: "Serial await in loop",
  unbounded_read_reduced_in_memory: "Unbounded read reduced in memory",
  lazy_load_in_loop: "Lazy load in loop",
  membership_test_against_list_in_loop: "List membership in loop",
  nested_loop_with_io: "I/O in nested loop",
  hot_path_sync_io: "Blocking I/O on a hot path",
  blocking_io_under_lock: "Blocking I/O under a lock",
  nested_loop_quadratic: "Quadratic nested loop",
  sql_high_complexity: "Complex SQL routine",
  sql_select_star: "SELECT * in a view",
  sql_update_delete_without_where: "UPDATE/DELETE without WHERE",
  sql_cartesian_join: "Cartesian join",
} as const;

/** Renamed ids (old -> current), so rows stored under an old id get the current label. */
export const LEGACY_BIOMARKER_NAMES: Readonly<Record<string, string>> = {
  bumpy_road: "repeated_nesting",
};

export function canonicalBiomarker(name: string): string {
  return LEGACY_BIOMARKER_NAMES[name] ?? name;
}
