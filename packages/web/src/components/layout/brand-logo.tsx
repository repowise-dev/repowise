import Image from "next/image";
import { cn } from "@repowise-dev/ui/lib/cn";

/**
 * Theme-aware brand mark. The canonical logo has light strokes (drawn for
 * dark surfaces); light mode swaps in the plum-stroke variant so the owl
 * stays visible on warm paper. Both render so the theme flip is instant —
 * visibility is CSS-only via the `.dark` class.
 */
export function BrandLogo({
  size = 28,
  className,
}: {
  size?: number;
  className?: string;
}) {
  const shared = cn(
    "shrink-0 drop-shadow-[0_0_8px_rgba(245,149,32,0.3)]",
    className,
  );
  return (
    <>
      <Image
        src="/repowise-logo-light.png"
        alt="repowise"
        width={size}
        height={size}
        className={cn(shared, "dark:hidden")}
      />
      <Image
        src="/repowise-logo.png"
        alt=""
        aria-hidden
        width={size}
        height={size}
        className={cn(shared, "hidden dark:block")}
      />
    </>
  );
}
