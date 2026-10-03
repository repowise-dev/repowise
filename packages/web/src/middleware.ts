import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  const apiUrl = process.env.REPOWISE_API_URL || "http://localhost:7337";

  // Construct the destination URL using the runtime API base URL
  const destination = new URL(
    request.nextUrl.pathname + request.nextUrl.search,
    apiUrl
  );

  const apiKey = process.env.REPOWISE_API_KEY;

  // Same-origin proxy: a deployed container already holds the key as a server
  // env var, so attach it upstream instead of making every browser paste it
  // into localStorage. A browser-supplied token still wins, and with no env
  // key the request is forwarded exactly as before.
  if (!apiKey || request.headers.has('authorization')) {
    return NextResponse.rewrite(destination);
  }

  // The override must carry the complete header set: Next drops every
  // incoming header the override list does not name, so this is a copy plus
  // the key, not a one-header replacement. The key stays server-side here; it
  // is never rendered into HTML or the client bundle.
  const headers = new Headers(request.headers);
  headers.set('authorization', `Bearer ${apiKey}`);

  return NextResponse.rewrite(destination, { request: { headers } });
}

export const config = {
  matcher: [
    '/api/:path*',
    '/health',
    '/metrics'
  ],
};
