import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getPageById } from "@/lib/api/pages";

interface Props {
  params: Promise<{ id: string; slug: string[] }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const pageId = slug.join("/");
  try {
    const page = await getPageById(pageId);
    return { title: page.title };
  } catch {
    return { title: "Documentation" };
  }
}

/**
 * Deep-link alias. Docs and Wiki are one reader surface now; ``/wiki/{pageId}``
 * stays a permanent, shareable entry point that opens that page inside the
 * unified reader (tree + reader + intelligence rail) rather than a second
 * front-end. All historical wiki links keep resolving — no dead-ends.
 */
export default async function WikiPageRoute({ params }: Props) {
  const { id, slug } = await params;
  const pageId = slug.join("/");
  redirect(`/repos/${id}/docs?page=${encodeURIComponent(pageId)}`);
}
