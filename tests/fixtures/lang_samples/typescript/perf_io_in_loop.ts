// Fixture for the performance pass (TypeScript io_in_loop).
// Hand-counted expectations live in test_perf_io_in_loop.py.

import axios from "axios";

// --- POSITIVES ---
async function fetchInLoop(urls: string[]) {
  for (const u of urls) {
    await fetch(u); // POSITIVE: network (fetch global)
  }
}

async function axiosInLoop(urls: string[]) {
  for (const u of urls) {
    await axios.get(u); // POSITIVE: network (imported axios)
  }
}

async function prismaInLoop(ids: number[]) {
  for (const id of ids) {
    await prisma.user.findMany({ where: { id } }); // POSITIVE: db (prisma verb)
  }
}

// --- NEGATIVES ---
async function fetchOutsideLoop(u: string) {
  return await fetch(u); // NEGATIVE: runs once
}

function mapDeleteInLoop(m: Map<string, number>, keys: string[]) {
  for (const k of keys) {
    m.delete(k); // NEGATIVE: Map.delete is not a db sink
  }
}
