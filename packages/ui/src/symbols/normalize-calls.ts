import type {
  SymbolBodyCall,
  SymbolRelationGroup,
} from "@repowise-dev/types/symbols";

/**
 * The wire shape shared by every endpoint that returns a graph edge as a row:
 * `/symbols/detail`'s `SymbolCallEntry` and `/callers-callees`'s
 * `CallerCalleeEntry` are the same eight fields, built by one server-side
 * helper. Typed structurally so both satisfy it without either importing the
 * other's alias.
 */
type CallEntryWire = Pick<SymbolBodyCall, "symbol_id" | "name" | "file" | "edge_type"> & {
  confidence?: number | null;
  resolution_origin?: SymbolBodyCall["resolution_origin"];
};

/**
 * Map one wire row onto the body's row.
 *
 * Extracted because the route normaliser and the drawer's web wrapper each
 * carried this map, field for field, for two surfaces that render the same
 * component — the duplication that let the two disagree about which edge
 * kinds a "caller" may be.
 */
export function toSymbolBodyCall(c: CallEntryWire): SymbolBodyCall {
  return {
    symbol_id: c.symbol_id,
    name: c.name,
    file: c.file,
    edge_type: c.edge_type,
    // Normalised to null rather than left undefined: `exactOptionalPropertyTypes`
    // treats an explicit undefined as a different type from an absent key.
    confidence: c.confidence ?? null,
    resolution_origin: c.resolution_origin ?? null,
  };
}

/** Map the relation groups, preserving server order (inbound first, then by
 *  descending total). Absent on a backend that predates the split. */
export function toSymbolBodyRelations(
  relations: SymbolRelationGroup<CallEntryWire>[] | undefined,
): SymbolRelationGroup<SymbolBodyCall>[] | undefined {
  return relations?.map((r) => ({ ...r, rows: r.rows.map(toSymbolBodyCall) }));
}
