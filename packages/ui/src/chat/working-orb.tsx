import { cn } from "../lib/cn";

export function WorkingOrb({ className }: { className?: string }) {
  return (
    <span
      data-working-orb="true"
      aria-hidden="true"
      className={cn(
        "relative inline-block h-4 w-4 shrink-0 rounded-full border border-[var(--color-border-hover)] motion-safe:animate-[pulse_2.4s_ease-in-out_infinite] motion-reduce:animate-none",
        className,
      )}
    >
      <span className="absolute left-[3px] top-[4px] h-1.5 w-1.5 rounded-full bg-[var(--color-accent-secondary)] opacity-70" />
      <span className="absolute bottom-[3px] right-[3px] h-1 w-1 rounded-full bg-[var(--color-text-tertiary)] opacity-70" />
    </span>
  );
}
