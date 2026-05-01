"use client";

import { cn } from "../lib/cn";

const TYPE_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  http: { bg: "bg-blue-500/10", text: "text-blue-400", label: "HTTP" },
  grpc: { bg: "bg-purple-500/10", text: "text-purple-400", label: "gRPC" },
  topic: { bg: "bg-orange-500/10", text: "text-orange-400", label: "Topic" },
};

export function ContractTypeBadge({ type }: { type: string }) {
  const style = TYPE_STYLES[type] ?? { bg: "bg-gray-500/10", text: "text-gray-400", label: type };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
        style.bg,
        style.text,
      )}
    >
      {style.label}
    </span>
  );
}

export function RoleBadge({ role }: { role: string }) {
  const isProvider = role === "provider";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
        isProvider
          ? "bg-green-500/10 text-green-400"
          : "bg-yellow-500/10 text-yellow-400",
      )}
    >
      {isProvider ? "Provider" : "Consumer"}
    </span>
  );
}
