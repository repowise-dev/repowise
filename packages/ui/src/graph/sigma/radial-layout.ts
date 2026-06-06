/**
 * Deterministic radial ("constellation") layout for the community
 * super-graph. NO physics simulation — given the same input it always
 * produces the same positions, so loads are reproducible and snapshot-
 * testable.
 *
 * The composition mirrors Michell Zappa's "Envisioning the near future
 * of technology" infographic: a dark repo-core at the origin, large hub
 * discs ranked onto three concentric rings (inner = most central), and
 * satellites clustered tightly around their own hub in golden-angle
 * spirals.
 *
 * Ranking choice (documented): the /architecture payload does not carry
 * entry-point counts or betweenness, so we rank communities by
 *   (avg_pagerank desc, member_count desc)
 * — avg_pagerank is the closest available proxy for "how central is this
 * cluster to the repo", and member_count breaks ties toward bigger
 * clusters. Ties beyond that are broken by community_id so the ranking
 * (and therefore ring/angle assignment) is stable across loads.
 */

/** Golden angle in radians — the classic phyllotaxis spacing. */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/** A single 2D position. */
export interface RadialPosition {
  x: number;
  y: number;
}

/** Minimal community shape the layout needs. Matches ArchitectureNode. */
export interface RadialCommunityInput {
  community_id: number;
  member_count: number;
  avg_pagerank: number;
}

/** Optional per-community member ids, used to place satellites in G4. */
export type RadialMembersInput = Map<number, string[]>;

export interface RadialLayoutResult {
  /** Hub disc center per community_id. */
  hubs: Map<number, RadialPosition>;
  /** Satellite positions keyed by member node id (empty unless members given). */
  satellites: Map<string, RadialPosition>;
  /** Which ring (1=inner, 2, 3) each community landed on. */
  rings: Map<number, 1 | 2 | 3>;
  /** Repo-core position (always the origin). */
  core: RadialPosition;
  /** Radius of each concentric ring, for the depth-ring underlay. */
  ringRadii: [number, number, number];
}

/** Deterministic non-negative hash (djb2), mirrors graphology-adapter. */
function hashString(str: string): number {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) + hash + (str.charCodeAt(i) ?? 0)) | 0;
  }
  return Math.abs(hash);
}

/**
 * Hub disc radius in *sigma units* from member count. Sigma "size" is
 * roughly a pixel radius; module nodes top out around 16–32, so hubs sit
 * deliberately larger (14–32) to read as anchors. Clamped both ends.
 */
export function hubSizeFromMembers(memberCount: number): number {
  const raw = 14 + Math.sqrt(Math.max(memberCount, 1)) * 3.2;
  return Math.max(14, Math.min(32, raw));
}

/**
 * Pure radial layout. Input is the community list (optionally with member
 * ids per community); output is a positions map. Deterministic.
 */
export function computeRadialLayout(
  communities: readonly RadialCommunityInput[],
  members?: RadialMembersInput,
): RadialLayoutResult {
  const core: RadialPosition = { x: 0, y: 0 };
  const hubs = new Map<number, RadialPosition>();
  const satellites = new Map<string, RadialPosition>();
  const rings = new Map<number, 1 | 2 | 3>();

  const hubCount = communities.length;
  // canvasUnit grows with the hub count so denser graphs spread further.
  const canvasUnit = 90 * Math.sqrt(Math.max(hubCount, 1));
  const ringRadii: [number, number, number] = [
    1 * canvasUnit,
    2 * canvasUnit,
    3 * canvasUnit,
  ];

  if (hubCount === 0) {
    return { hubs, satellites, rings, core, ringRadii };
  }

  // 1. Rank: avg_pagerank desc, member_count desc, community_id asc (stable).
  const ranked = [...communities].sort((a, b) => {
    if (b.avg_pagerank !== a.avg_pagerank) return b.avg_pagerank - a.avg_pagerank;
    if (b.member_count !== a.member_count) return b.member_count - a.member_count;
    return a.community_id - b.community_id;
  });

  // 2. Ring assignment by tercile of rank: top → R1, middle → R2, rest → R3.
  const tercile = Math.ceil(hubCount / 3);
  const ringMembers: Record<1 | 2 | 3, RadialCommunityInput[]> = { 1: [], 2: [], 3: [] };
  ranked.forEach((community, rank) => {
    const ring: 1 | 2 | 3 = rank < tercile ? 1 : rank < tercile * 2 ? 2 : 3;
    rings.set(community.community_id, ring);
    ringMembers[ring].push(community);
  });

  // 3. Angular placement: golden-angle sequence within each ring, then one
  //    relaxation pass that nudges the largest hubs apart so the biggest
  //    anchors don't crowd. Stable-sort by community_id keeps it reproducible.
  for (const ringKey of [1, 2, 3] as const) {
    const ring = ringMembers[ringKey];
    if (ring.length === 0) continue;
    const radius = ringRadii[ringKey - 1]!;

    // Stable order for the ring (by community_id) → reproducible angles.
    const ordered = [...ring].sort((a, b) => a.community_id - b.community_id);
    const n = ordered.length;

    // Base golden-angle angles, offset per-ring so rings don't align spokes.
    const angles = ordered.map(
      (_, i) => (i * GOLDEN_ANGLE + ringKey * 0.7) % (2 * Math.PI),
    );

    // One relaxation pass: maximise angular separation of the largest hubs by
    // re-seating ordered hubs onto evenly spaced slots, biggest-first. This is
    // deterministic (input order is community_id-stable) and crosses no NaNs.
    if (n > 1) {
      const bySize = ordered
        .map((c, i) => ({ i, member_count: c.member_count, community_id: c.community_id }))
        .sort((a, b) =>
          b.member_count !== a.member_count
            ? b.member_count - a.member_count
            : a.community_id - b.community_id,
        );
      const slot = (2 * Math.PI) / n;
      bySize.forEach((entry, k) => {
        angles[entry.i] = (k * slot + ringKey * 0.7) % (2 * Math.PI);
      });
    }

    ordered.forEach((community, i) => {
      const angle = angles[i] ?? 0;
      hubs.set(community.community_id, {
        x: radius * Math.cos(angle),
        y: radius * Math.sin(angle),
      });
    });
  }

  // 4. Satellites: golden-angle spiral around their own hub, hash-jittered,
  //    with a cheap two-pass noverlap *within the cluster only*.
  if (members) {
    for (const community of communities) {
      const hub = hubs.get(community.community_id);
      const ids = members.get(community.community_id);
      if (!hub || !ids || ids.length === 0) continue;

      const hubRadius = hubSizeFromMembers(community.member_count);
      const placed: RadialPosition[] = [];

      ids.forEach((nodeId, i) => {
        const angle = i * GOLDEN_ANGLE;
        const r = hubRadius + 14 + 11 * Math.sqrt(i);
        const h = hashString(nodeId);
        const jx = ((h % 1000) / 1000 - 0.5) * 8;
        const jy = (((h >> 10) % 1000) / 1000 - 0.5) * 8;
        placed.push({
          x: hub.x + r * Math.cos(angle) + jx,
          y: hub.y + r * Math.sin(angle) + jy,
        });
      });

      // Two-pass noverlap inside the cluster: push pairs that sit closer than
      // minDist apart along their connecting line. Deterministic, O(k^2) but k
      // is one community's satellite count (small in the G4 expansion case).
      const minDist = 16;
      for (let pass = 0; pass < 2; pass++) {
        for (let a = 0; a < placed.length; a++) {
          for (let b = a + 1; b < placed.length; b++) {
            const pa = placed[a]!;
            const pb = placed[b]!;
            const dx = pb.x - pa.x;
            const dy = pb.y - pa.y;
            const dist = Math.hypot(dx, dy) || 0.0001;
            if (dist < minDist) {
              const push = (minDist - dist) / 2;
              const ux = dx / dist;
              const uy = dy / dist;
              pa.x -= ux * push;
              pa.y -= uy * push;
              pb.x += ux * push;
              pb.y += uy * push;
            }
          }
        }
      }

      ids.forEach((nodeId, i) => {
        const p = placed[i]!;
        satellites.set(nodeId, { x: p.x, y: p.y });
      });
    }
  }

  return { hubs, satellites, rings, core, ringRadii };
}
