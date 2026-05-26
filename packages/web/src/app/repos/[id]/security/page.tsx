"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

// Security now lives as a tab under Risk. Keep this route as a redirect so
// existing links / bookmarks continue to work.
export default function SecurityPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();

  useEffect(() => {
    router.replace(`/repos/${params.id}/risk?tab=security`);
  }, [params.id, router]);

  return null;
}
