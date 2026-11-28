import path from "path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// i18n. The request config resolves the locale from the `NEXT_LOCALE` cookie
// on every request; there is deliberately no `/{locale}/...` prefix routing, so
// existing links, bookmarks and uptime probes keep resolving unchanged.
const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  outputFileTracingRoot: path.join(__dirname, "../../"),
  transpilePackages: ["@repowise-dev/ui", "@repowise-dev/types", "@repowise-dev/api-client"],
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts"],
    // middleware.ts rewrites /api/* to the API, and Next proxies that rewrite
    // with a 30 s socket idle timeout by default. A model call (report
    // rewrite, chat before its first token, decision mining) can stay silent
    // longer, and the proxy then hangs up while the API still finishes the
    // work. Ten minutes covers any one model call; it is an idle timeout, so
    // a stream that keeps sending is never cut. This config is serialized into
    // the standalone server.js that `repowise serve` runs.
    proxyTimeout: 10 * 60 * 1000,
  },
  // The workspace packages (@repowise-dev/types, @repowise-dev/ui,
  // @repowise-dev/api-client) are ESM
  // ("type": "module") and their barrel files re-export with explicit ".js"
  // specifiers (e.g. export * from "./graph.js") that point at ".ts" sources.
  // Webpack needs an extension alias to map those ".js" specifiers back to the
  // real ".ts"/".tsx" files when it transpiles these packages inline; without
  // it, any value import of a barrel entry fails to resolve at build time.
  webpack(config) {
    config.resolve.extensionAlias = {
      ...config.resolve.extensionAlias,
      ".js": [".ts", ".tsx", ".js", ".jsx"],
      ".jsx": [".tsx", ".jsx"],
    };
    return config;
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
  images: {
    unoptimized: true,
  },
};

export default withNextIntl(nextConfig);
