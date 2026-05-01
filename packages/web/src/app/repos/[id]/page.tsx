import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Hash } from "lucide-react";
import { getRepo } from "@/lib/api/repos";
import { Badge } from "@repowise/ui/ui/badge";
import { ChatInterface } from "@/components/chat/chat-interface";

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const repo = await getRepo(id);
    return { title: `${repo.name} — Chat` };
  } catch {
    return { title: "Repository" };
  }
}

export default async function RepoChatPage({ params }: Props) {
  const { id } = await params;

  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }

  return (
    <div className="flex flex-col h-full">
      {/* Compact header */}
      <div className="px-4 pt-3 pb-2 flex items-center justify-between shrink-0 border-b border-[var(--color-border-default)]">
        <div className="min-w-0">
          <h1 className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {repo.name}
          </h1>
          <p className="text-[10px] font-mono text-[var(--color-text-tertiary)] truncate">
            {repo.local_path}
          </p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 ml-3">
          {repo.head_commit && (
            <Badge variant="outline" className="text-[10px] h-5">
              <Hash className="h-2.5 w-2.5" />
              {repo.head_commit.slice(0, 7)}
            </Badge>
          )}
          <Badge variant="outline" className="text-[10px] h-5">
            {repo.default_branch}
          </Badge>
        </div>
      </div>

      {/* Chat interface fills remaining height */}
      <div className="flex-1 min-h-0">
        <ChatInterface repoId={id} repoName={repo.name} />
      </div>
    </div>
  );
}
