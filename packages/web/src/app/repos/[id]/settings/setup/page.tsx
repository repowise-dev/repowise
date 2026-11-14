import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getRepo } from "@/lib/api/repos";
import { SetupPage } from "@/components/setup/setup-page";

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const repo = await getRepo(id);
    return { title: `${repo.name} · Setup` };
  } catch {
    return { title: "Setup" };
  }
}

/** `/repos/[id]/settings/setup`: what is set up for this repo. The checks load client side. */
export default async function RepoSetupPage({ params }: Props) {
  const { id } = await params;
  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }
  return <SetupPage repoId={id} repoName={repo.name} />;
}
