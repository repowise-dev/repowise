import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  const apiUrl = process.env.REPOWISE_API_URL || "http://localhost:7337";
  
  // Construct the destination URL using the runtime API base URL
  const destination = new URL(
    request.nextUrl.pathname + request.nextUrl.search,
    apiUrl
  );

  return NextResponse.rewrite(destination);
}

export const config = {
  matcher: [
    '/api/:path*',
    '/health',
    '/metrics'
  ],
};
