import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Sidebar } from "@/components/layout/sidebar";
import { MobileNav } from "@/components/layout/mobile-nav";
import { CommandPalette } from "@/components/search/command-palette";
import { listRepos } from "@/lib/api/repos";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: {
    default: "repowise",
    template: "%s — repowise",
  },
  description: "Open-source codebase documentation engine",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Fetch repos server-side for the sidebar.
  // Gracefully fall back to empty if the API is unavailable.
  let repos: Awaited<ReturnType<typeof listRepos>> = [];
  try {
    repos = await listRepos();
  } catch {
    // API not available — show empty sidebar
  }

  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} dark`}
    >
      <body className="bg-[var(--color-bg-root)] text-[var(--color-text-primary)] antialiased">
        <NuqsAdapter>
        <TooltipProvider delayDuration={300}>
          <div className="flex h-screen overflow-hidden">
            <Sidebar repos={repos} />
            <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
              <MobileNav repos={repos} />
              <main className="flex-1 overflow-auto min-w-0">
                {children}
              </main>
            </div>
          </div>
          <CommandPalette repos={repos} />
        </TooltipProvider>
        </NuqsAdapter>
        <Toaster
          theme="dark"
          position="bottom-right"
          toastOptions={{
            style: {
              background: "var(--color-bg-elevated)",
              border: "1px solid var(--color-border-default)",
              color: "var(--color-text-primary)",
            },
          }}
        />
      </body>
    </html>
  );
}
