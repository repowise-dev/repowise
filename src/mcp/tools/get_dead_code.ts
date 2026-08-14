import { z } from 'zod';
import { DeadCodeFinding, DeadCodeKind } from '../types';

const VALID_KINDS = ['unreachable_file', 'unused_export', 'unused_import'];

const CONFIDENCE_TIERS: Record<string, number> = {
  high: 0.8,
  medium: 0.5,
  low: 0.3,
};

const GetDeadCodeSchema = z.object({
  kind: z.enum(VALID_KINDS).optional(),
  min_confidence: z.union([z.number().min(0).max(1), z.enum(Object.keys(CONFIDENCE_TIERS))]).optional(),
  limit: z.number().int().positive().optional(),
});

export async function get_dead_code(params: unknown) {
  const parsed = GetDeadCodeSchema.safeParse(params);
  
  if (!parsed.success) {
    const errors = parsed.error.errors.map(e => {
      if (e.path[0] === 'kind') {
        return `Invalid kind "${(params as any).kind}". Must be one of: ${VALID_KINDS.join(', ')}`;
      }
      if (e.path[0] === 'min_confidence') {
        return `Invalid min_confidence "${(params as any).min_confidence}". Must be a number between 0-1 or one of: high, medium, low`;
      }
      return e.message;
    });
    throw new Error(`Parameter validation failed: ${errors.join('; ')}`);
  }

  const { kind, min_confidence, limit } = parsed.data;
  
  const confidenceThreshold = typeof min_confidence === 'string'
    ? CONFIDENCE_TIERS[min_confidence]
    : min_confidence ?? 0;

  const findings = await fetchDeadCodeFindings();
  
  let filtered = findings.filter(f => f.confidence >= confidenceThreshold);
  
  if (kind) {
    filtered = filtered.filter(f => f.kind === kind);
  }
  
  if (limit) {
    filtered = filtered.slice(0, limit);
  }

  return {
    summary: {
      total_findings: findings.length,
      filtered_findings: filtered.length,
      by_kind: countByKind(findings),
    },
    findings: filtered,
  };
}

function countByKind(findings: DeadCodeFinding[]) {
  return findings.reduce((acc, f) => {
    acc[f.kind] = (acc[f.kind] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);
}

async function fetchDeadCodeFindings(): Promise<DeadCodeFinding[]> {
  // Implementation remains unchanged
  return [];
}
