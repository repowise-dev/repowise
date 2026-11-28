import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { repoLabel } from "@repowise-dev/types/repos";
import { getRepo } from "@/lib/api/repos";
import { ChatInterface } from "@/components/chat/chat-interface";

interface Props {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ q?: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const repo = await getRepo(id);
    return { title: `${repoLabel(repo, repo.name)} — Chat` };
  } catch {
    return { title: "Repository" };
  }
}

/** Thin shell: the breadcrumb already names the repo, so the chat owns the page. */
export default async function RepoChatPage({ params, searchParams }: Props) {
  const { id } = await params;
  const { q } = await searchParams;

  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }

  return (
    <div className="flex h-full flex-col">
      <ChatInterface
        repoId={id}
        repoName={repoLabel(repo, repo.name)}
        {...(q ? { initialQuestion: q } : {})}
      />
    </div>
  );
}
