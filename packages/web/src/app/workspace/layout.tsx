import { redirect } from "next/navigation";
import { getWorkspace } from "@/lib/api/workspace";

export default async function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  let isWorkspace = false;
  try {
    const ws = await getWorkspace();
    isWorkspace = ws.is_workspace;
  } catch {
    // API unavailable
  }

  if (!isWorkspace) {
    redirect("/");
  }

  return <>{children}</>;
}
