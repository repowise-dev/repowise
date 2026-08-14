if (typeof kind !== 'string' || !['unused_exports', 'unreachable_file', 'dead_code'].includes(kind)) {
  return { summary: { total_findings: 0, filtered_findings: 0, by_kind: {} } };
}