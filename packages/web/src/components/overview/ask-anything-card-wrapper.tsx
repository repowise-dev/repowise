"use client";

import { useRouter } from "next/navigation";
import { AskAnythingCard } from "@repowise-dev/ui/dashboard/explore-cards";

/** Routes the overview "ask" input into the chat page's `?q=` deep link. */
export function AskAnythingCardWrapper({ repoId }: { repoId: string }) {
  const router = useRouter();
  return (
    <AskAnythingCard
      onAsk={(question) =>
        router.push(`/repos/${repoId}/chat?q=${encodeURIComponent(question)}`)
      }
    />
  );
}
