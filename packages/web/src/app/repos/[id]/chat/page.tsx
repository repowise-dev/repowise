import type { Metadata } from "next";
import { notFound } from "next/navigation";
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
    return { title: `${repo.name} — Chat` };
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
        repoName={repo.name}
        {...(q ? { initialQuestion: q } : {})}
      />
    </div>
  );
}
