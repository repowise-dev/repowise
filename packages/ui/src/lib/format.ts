/**
 * Number, date, token, and other formatters for repowise UI.
 */

/** Format a number with commas: 1234567 → "1,234,567" */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat().format(n);
}

/** Format token counts: 4200000 → "4.2M", 980000 → "980K", 1234 → "1,234" */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return formatNumber(n);
}

/** Format USD cost: 0.004 → "<$0.01", 4.2 → "$4.20", 18.6 → "$18.60" */
export function formatCost(usd: number): string {
  if (usd > 0 && usd < 0.01) return "<$0.01";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(usd);
}

/** Format a datetime to a relative string: "2h ago", "3d ago", "just now" */
export function formatRelativeTime(date: string | Date): string {
  const d = typeof date === "string" ? new Date(date) : date;
  const now = Date.now();
  const diff = now - d.getTime();
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const weeks = Math.floor(days / 7);
  const months = Math.floor(days / 30);

  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7) return `${days}d ago`;
  if (weeks < 5) return `${weeks}w ago`;
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

/** Format a datetime to an absolute string: "Mar 19, 2026" */
export function formatDate(date: string | Date): string {
  const d = typeof date === "string" ? new Date(date) : date;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(d);
}

/** Format a datetime to full: "Mar 19, 2026 at 10:30 AM" */
export function formatDateTime(date: string | Date): string {
  const d = typeof date === "string" ? new Date(date) : date;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(d);
}

/** Format LOC counts: 50000 → "50K", 1234 → "1.2K" */
export function formatLOC(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Truncate a file path keeping as many trailing components as fit: src/very/long/path/file.py → …/long/path/file.py */
export function truncatePath(path: string, maxChars = 60): string {
  if (path.length <= maxChars) return path;
  const parts = path.split("/");
  if (parts.length <= 1) return `…${path.slice(-(maxChars - 1))}`;
  // Progressively include more trailing path components until we exceed maxChars
  for (let i = parts.length - 2; i >= 1; i--) {
    const candidate = `…/${parts.slice(i).join("/")}`;
    if (candidate.length <= maxChars) return candidate;
  }
  // Just filename
  const filename = parts[parts.length - 1] ?? path;
  return filename.length <= maxChars ? `…/${filename}` : `…${filename.slice(-(maxChars - 1))}`;
}

/** Format age in days to human-readable: 45 → "1.5 months", 400 → "1.1 years" */
export function formatAgeDays(n: number): string {
  if (n < 1) return "< 1 day";
  if (n < 30) return `${n} day${n !== 1 ? "s" : ""}`;
  if (n < 365) {
    const months = Math.round(n / 30 * 10) / 10;
    return `${months} month${months !== 1 ? "s" : ""}`;
  }
  const years = Math.round(n / 365 * 10) / 10;
  return `${years} year${years !== 1 ? "s" : ""}`;
}

/** Format a confidence score as a percentage string: 0.87 → "87%" */
export function formatConfidence(score: number): string {
  return `${Math.round(score * 100)}%`;
}

/** Format a job progress: 340 / 847 → "340 / 847 (40%)" */
export function formatProgress(done: number, total: number): string {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return `${formatNumber(done)} / ${formatNumber(total)} (${pct}%)`;
}
