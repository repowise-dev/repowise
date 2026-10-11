import { redirect } from "next/navigation";
import { getWorkspace } from "@/lib/api/workspace";
import { shouldRedirectFromWorkspace } from "@/lib/workspace-mode";

export default async function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  let isWorkspace: boolean | null = null;
  try {
    const ws = await getWorkspace({ cache: "no-store" });
    isWorkspace = ws.is_workspace;
  } catch {
    // API unavailable
  }

  if (shouldRedirectFromWorkspace(isWorkspace)) {
    redirect("/");
  }

  return <>{children}</>;
}
