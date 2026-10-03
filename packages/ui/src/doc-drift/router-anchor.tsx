"use client";

/**
 * A real anchor the host router takes over on a plain left click only, so a
 * middle click or a modifier still opens a new tab.
 */
export function RouterAnchor({
  href,
  navigate,
  className,
  title,
  children,
}: {
  href: string;
  navigate?: ((href: string) => void) | undefined;
  className?: string | undefined;
  title?: string | undefined;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      title={title}
      className={className}
      onClick={(event) => {
        if (
          !navigate ||
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) {
          return;
        }
        event.preventDefault();
        navigate(href);
      }}
    >
      {children}
    </a>
  );
}
