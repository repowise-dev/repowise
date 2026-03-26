import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts", "d3"],
  },
  images: {
    unoptimized: true,
  },
  async rewrites() {
    const apiUrl = process.env.REPOWISE_API_URL || "http://localhost:7337";
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/api/:path*`,
      },
      {
        source: "/health",
        destination: `${apiUrl}/health`,
      },
      {
        source: "/metrics",
        destination: `${apiUrl}/metrics`,
      },
    ];
  },
};

export default nextConfig;
