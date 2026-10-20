import { cn } from "../lib/cn";
import { OrbLoader, type OrbState } from "../shared/orb-loader";

/**
 * The 20px inline orb beside a streaming answer or a running tool. Decorative:
 * the surrounding text names the step, so the orb is hidden from assistive
 * tech. Pass `state` (from `ORB_STATE`) to show what kind of work is running,
 * e.g. thinking, then the tool's kind, then writing.
 */
export function WorkingOrb({ className, state }: { className?: string; state?: OrbState }) {
  return (
    <span
      data-working-orb="true"
      aria-hidden="true"
      className={cn("inline-flex h-5 w-5 shrink-0 items-center justify-center", className)}
    >
      <OrbLoader size={20} {...(state ? { state } : {})} />
    </span>
  );
}
