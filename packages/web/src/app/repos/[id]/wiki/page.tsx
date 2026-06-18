import { redirect } from "next/navigation";

interface Props {
  params: Promise<{ id: string }>;
}

/**
 * Wiki home. There is one documentation reader now, so the wiki index opens it
 * at its default landing (the Guided Tour spine) rather than dead-ending on a
 * bare slug. Keeps ``/repos/{id}/wiki`` a valid, shareable entry point.
 */
export default async function WikiIndexRoute({ params }: Props) {
  const { id } = await params;
  redirect(`/repos/${id}/docs`);
}
