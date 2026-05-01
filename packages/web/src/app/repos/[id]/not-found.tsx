import Link from "next/link";
import { Home, ChevronLeft } from "lucide-react";

export default function RepoNotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] p-4">
        <Home className="h-8 w-8 text-[var(--color-text-tertiary)]" />
      </div>
      <div className="space-y-1">
        <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
          Page not found
        </h1>
        <p className="max-w-md text-sm text-[var(--color-text-secondary)]">
          The page or resource you requested doesn&apos;t exist for this repository. It may
          have been moved or never existed.
        </p>
      </div>
      <Link
        href="/"
        className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] transition-colors"
      >
        <ChevronLeft className="h-4 w-4" />
        Back to dashboard
      </Link>
    </div>
  );
}
