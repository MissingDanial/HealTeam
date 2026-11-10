// This server-only URL is also used when generating rewrite routes at build
// time. Docker passes it as a non-secret build argument; browsers use /api/v1.
const apiInternalUrl = (process.env.HEALTEAM_API_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // A live model call may take up to 600 seconds. Next's default 30-second
  // upstream idle timeout would cut the SSE connection before that deadline.
  experimental: {
    proxyTimeout: 660_000,
    // Keep the verified managed runtime immutable after serving pages.
    // Route cache stays in memory instead of adding unlisted disk files.
    isrFlushToDisk: false,
  },
  async redirects() {
    return [{ source: "/index.html", destination: "/", permanent: false }];
  },
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${apiInternalUrl}/api/v1/:path*` },
      { source: "/healthz", destination: `${apiInternalUrl}/healthz` },
      { source: "/readyz", destination: `${apiInternalUrl}/readyz` },
      { source: "/docs", destination: `${apiInternalUrl}/docs` },
      { source: "/openapi.json", destination: `${apiInternalUrl}/openapi.json` },
    ];
  },
};

module.exports = nextConfig;
