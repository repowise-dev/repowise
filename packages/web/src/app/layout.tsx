import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { Toaster } from "sonner";
import { TooltipProvider } from "@repowise-dev/ui/ui/tooltip";
import { Sidebar } from "@/components/layout/sidebar";
import { MobileNav } from "@/components/layout/mobile-nav";
import { CommandPalette } from "@/components/search/command-palette";
import { listRepos } from "@/lib/api/repos";
import { getWorkspace } from "@/lib/api/workspace";
import type { WorkspaceResponse } from "@/lib/api/types";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: {
    default: "repowise",
    template: "%s â€” repowise",
  },
  description: "Open-source codebase documentation engine",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Fetch repos + workspace info server-side for the sidebar.
  // Gracefully fall back to empty/null if the API is unavailable.
  let repos: Awaited<ReturnType<typeof listRepos>> = [];
  let workspace: WorkspaceResponse | null = null;
  try {
    const [reposResult, wsResult] = await Promise.allSettled([
      listRepos(),
      getWorkspace(),
    ]);
    if (reposResult.status === "fulfilled") repos = reposResult.value;
    if (wsResult.status === "fulfilled") workspace = wsResult.value;
  } catch {
    // API not available â€” show empty sidebar
  }

  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} dark`}
    >
      <body className="bg-[var(--color-bg-root)] text-[var(--color-text-primary)] antialiased">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-[var(--color-bg-elevated)] focus:px-3 focus:py-2 focus:text-sm focus:text-[var(--color-text-primary)] focus:outline focus:outline-2 focus:outline-[var(--color-accent-primary)]"
        >
          Skip to content
        </a>
        <NuqsAdapter>
        <TooltipProvider delayDuration={300}>
          <div className="flex h-screen overflow-hidden">
            <Sidebar repos={repos} workspace={workspace} />
            <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
              <MobileNav repos={repos} workspace={workspace} />
              <main id="main-content" className="flex-1 overflow-auto min-w-0">
                {children}
              </main>
            </div>
          </div>
          <CommandPalette repos={repos} workspace={workspace} />
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
