import { notFound } from "next/navigation";
import { getCoupling } from "@/lib/api/coupling";
import { ApiClientError } from "@/lib/api/client";
import { CouplingView } from "@/components/coupling/coupling-view";

export const revalidate = 30;

interface Props {
  params: Promise<{ id: string }>;
}

export default async function CouplingPage({ params }: Props) {
  const { id: repoId } = await params;

  let data;
  try {
    data = await getCoupling(repoId, { limit: 200 });
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
          Change coupling
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-tertiary)]">
          Files that tend to change together in the same commit. A temporal hint
          for hidden relationships, not a verified code dependency.
        </p>
      </div>
      <CouplingView data={data} />
    </div>
  );
}
